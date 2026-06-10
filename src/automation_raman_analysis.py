import os
import sys

# Ensure raman_workflow/ (the parent of src/) is on sys.path so that the
# sibling `util/` package is importable regardless of how this script is invoked
# (python /path/to/src/script.py adds src/ to sys.path, not raman_workflow/).
_RAMAN_WORKFLOW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAMAN_WORKFLOW_DIR not in sys.path:
    sys.path.insert(0, _RAMAN_WORKFLOW_DIR)

import argparse
import shutil
from util import (
    Tee, run_command, load_config, validate_config, build_srun_args,
    parse_resume_step, make_pipeline_excepthook, run_relaxation_with_zbrent_retry,
    print_job_header, make_write_status, STEP_HISTORY, STEP_DESCRIPTIONS,
)

# ── CLI flags via argparse ──────────────────────────────────────────────────
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument(
    "--restart",
    action="store_true",
    help="Clean all generated files and restart pipeline from scratch",
)
parser.add_argument(
    "--cpu", action="store_true", help="Use CPU VASP binary instead of GPU"
)
parser.add_argument(
    "--scratch", action="store_true", help="Run VASP stages on SCRATCH filesystem"
)
args, remaining = parser.parse_known_args()
# Strip parsed flags, keep everything else for any downstream consumer
sys.argv = [sys.argv[0]] + remaining

RESTART_FLAG = args.restart
CPU_FLAG = args.cpu
SCRATCH_FLAG = args.scratch

# ── Config paths (env vars in ~/.bashrc; see CLAUDE.md) ──────────────────────
BASE_PROJECT_DIR = os.environ.get("RAMAN_PROJECT_DIR", "")

if not os.path.isdir(BASE_PROJECT_DIR):
    print(f"Error: BASE_PROJECT_DIR '{BASE_PROJECT_DIR}' does not exist.")
    print("Set the RAMAN_PROJECT_DIR environment variable to your project directory.")
    sys.exit(1)

# ── Bootstrap material dir from CWD ──────────────────────────────────────────
CWD_BASENAME = os.path.basename(os.getcwd())
if CWD_BASENAME not in os.listdir(BASE_PROJECT_DIR):
    print(
        f"Error: Script must be run from a material directory (e.g., MoS2, WS2) inside {BASE_PROJECT_DIR}"
    )
    sys.exit(1)
MATERIAL_DIR = os.path.join(BASE_PROJECT_DIR, CWD_BASENAME)

# workflow.log ALWAYS lives on HOME — it's tiny text, and keeping it on HOME:
#  - survives SCRATCH purges (90-day NERSC retention)
#  - is visible to monitoring scripts (show_status.sh, tail)
#  - makes config-staleness checks meaningful (same filesystem as configs)
STATUS_FILE = os.path.join(MATERIAL_DIR, "workflow.log")
OUTPUT_FILE = os.path.join(MATERIAL_DIR, "workflow.out")

# Preliminary WORK_DIR for --restart cleanup (refined after config load)
_scratch_base = os.environ.get("SCRATCH", "")
WORK_DIR = os.path.join(_scratch_base, "vasp_calculations", CWD_BASENAME) if SCRATCH_FLAG and _scratch_base else MATERIAL_DIR

# ── --restart: delete all generated directories, keep input/ + config ────────
if RESTART_FLAG:
    # Clean generated dirs from WORK_DIR (SCRATCH or HOME)
    for dirname in ("scf", "hf", "raman", "output"):
        dp = os.path.join(WORK_DIR, dirname)
        if os.path.exists(dp) and not os.path.islink(dp):
            shutil.rmtree(dp)
            print(f"  [restart] Removed: {dp}/")

    # --scratch: also clean HOME/output/ (final copy destination)
    if SCRATCH_FLAG:
        home_output = os.path.join(MATERIAL_DIR, "output")
        if os.path.exists(home_output) and not os.path.islink(home_output):
            shutil.rmtree(home_output)
            print(f"  [restart] Removed HOME output/: {home_output}")

    # Remove all log files (clean start)
    for log_name in ("workflow.log", "workflow.out", "salloc_output.log"):
        log_path = os.path.join(MATERIAL_DIR, log_name)
        if os.path.exists(log_path):
            os.remove(log_path)
            print(f"  [restart] Removed: {log_path}")

    print(f"  [restart] Done — input/ (including workflow_settings.yaml) preserved.")
    print(f"  [restart] Starting fresh pipeline from step 3...")

# Redirect ALL output AFTER restart cleanup so logs are fresh.
# (Must come after cleanup — otherwise Tee holds file handles open,
#  preventing log files from being properly replaced.)
sys.stdout = Tee(STATUS_FILE, OUTPUT_FILE)

if RESTART_FLAG:
    print("")  # blank line after restart messages

# Binary utilities dir (override via env var)
DEFAULT_BINARY_UTILITIES_DIR = "/global/cfs/cdirs/m526/vasp_binaries/binary_utility"
BINARY_UTILITIES_DIR = os.environ.get(
    "BINARY_UTILITIES_DIR", DEFAULT_BINARY_UTILITIES_DIR
)

# VASP binary path (GPU default; VASP_BINARY_CPU with --cpu)
DEFAULT_VASP_BINARY_GPU = "/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std"
DEFAULT_VASP_BINARY_CPU = "/global/cfs/cdirs/m526/liangbo/bin/cpu/vasp_std"
if CPU_FLAG:
    VASP_BINARY_PATH = os.environ.get("VASP_BINARY_CPU", DEFAULT_VASP_BINARY_CPU)
else:
    VASP_BINARY_PATH = os.environ.get("VASP_BINARY", DEFAULT_VASP_BINARY_GPU)

# Validate VASP binary
if not os.path.isfile(VASP_BINARY_PATH):
    print(f"Error: VASP binary not found at '{VASP_BINARY_PATH}'")
    print("Set the VASP_BINARY environment variable to a valid VASP binary path.")
    print(f"Expected location: {VASP_BINARY_PATH}")
    sys.exit(1)
print(f"VASP binary found: {VASP_BINARY_PATH}")
if CPU_FLAG:
    print(f"  (CPU mode: --cpu flag set)")

if not os.path.isdir(BINARY_UTILITIES_DIR):
    print(f"Error: BINARY_UTILITIES_DIR '{BINARY_UTILITIES_DIR}' does not exist.")
    print("Set the BINARY_UTILITIES_DIR environment variable to a valid directory.")
    sys.exit(1)
print(f"Binary utilities directory found: {BINARY_UTILITIES_DIR}")

# Config inheritance: shared YAML → per-material YAML
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(MATERIAL_DIR, "input", "workflow_settings.yaml")
SHARED_CONFIG_PATH = os.path.join(BASE_PROJECT_DIR, "shared_workflow_settings.yaml")

CONFIG = load_config([
    (SHARED_CONFIG_PATH, "shared"),
    (CONFIG_PATH, "per-material"),
])

validate_config(CONFIG)

# ── Resolve material identity from config ────────────────────────────────────
MATERIAL_NAME = CONFIG.get("name") or CWD_BASENAME
MATERIAL_LABEL = CONFIG.get("material") or MATERIAL_NAME

# Reconstruct MATERIAL_DIR from config name (in case it differs from CWD)
MATERIAL_DIR = os.path.join(BASE_PROJECT_DIR, MATERIAL_NAME)
# Finalize WORK_DIR (--scratch: VASP on $SCRATCH, config on $HOME)
SCRATCH_BASE = os.environ.get("SCRATCH", "")
if SCRATCH_FLAG:
    if not SCRATCH_BASE:
        print("Error: --scratch flag requires $SCRATCH environment variable.")
        sys.exit(1)
    WORK_DIR = os.path.join(SCRATCH_BASE, "vasp_calculations", MATERIAL_NAME)
    print(f"  [scratch] WORK_DIR = {WORK_DIR}")
else:
    WORK_DIR = MATERIAL_DIR
HFFILES_DIR = os.path.join(WORK_DIR, "hf")
RAMAN_DIR = os.path.join(WORK_DIR, "raman")

# STATUS_FILE always on HOME — see comment at preliminary assignment above
sys.excepthook = make_pipeline_excepthook(STATUS_FILE)

# ── Job start header ──────────────────────────────────────────────────────────
print_job_header(
    material_label=MATERIAL_LABEL,
    material_name=MATERIAL_NAME,
    work_dir=WORK_DIR,
    status_file=STATUS_FILE,
    scratch_flag=SCRATCH_FLAG,
    restart_flag=RESTART_FLAG,
    cpu_flag=CPU_FLAG,
)

if MATERIAL_NAME != CWD_BASENAME:
    print(
        f"  [config] Config 'name' differs from CWD: '{MATERIAL_NAME}' vs '{CWD_BASENAME}'"
    )
    print(f"  [config] Using config name for paths: {MATERIAL_DIR}")
if not os.path.isdir(MATERIAL_DIR):
    print(
        f"Error: MATERIAL_DIR '{MATERIAL_DIR}' does not exist (from config name '{MATERIAL_NAME}')."
    )
    sys.exit(1)

SRUN_ARGS = build_srun_args(CONFIG, CPU_FLAG)

HF_PARALLEL = CONFIG.get("hf_parallel", False)


# Pre-bound write_status (created by util.make_write_status)
write_status = make_write_status(
    STATUS_FILE,
    MATERIAL_LABEL,
    MATERIAL_NAME,
    BASE_PROJECT_DIR,
)


def vasp_loop_check_and_restart(vasp_script_path, max_restarts=3):
    """Run VASP in all hf_POSCAR-* dirs — delegates to util.vasp_loop."""
    from util.vasp_loop import run_hf_loop
    return run_hf_loop(
        hffiles_dir=HFFILES_DIR,
        vasp_script_path=vasp_script_path,
        max_restarts=max_restarts,
        srun_args=SRUN_ARGS,
        vasp_binary=VASP_BINARY_PATH,
        cpu_flag=CPU_FLAG,
        hf_parallel=HF_PARALLEL,
    )


# --- Workflow Steps ---

print(
    f"Starting Raman automation for {MATERIAL_LABEL} ({MATERIAL_NAME}) in {MATERIAL_DIR}"
)
print(f"Current working directory: {os.getcwd()}")


# ── Resume: skip completed steps via workflow.log ──────────────────────────
START_STEP = parse_resume_step(STATUS_FILE, STEP_HISTORY, STEP_DESCRIPTIONS)
if START_STEP is None:
    sys.exit(0)

print(f"[resume] START_STEP = {START_STEP} — starting pipeline execution.")

# ── Config staleness warning ─────────────────────────────────────────────────
if START_STEP > 1 and os.path.exists(STATUS_FILE):
    log_mtime = os.path.getmtime(STATUS_FILE)
    for cfg_path, cfg_label in [
        (SHARED_CONFIG_PATH, "shared"),
        (CONFIG_PATH, "per-material"),
    ]:
        if os.path.exists(cfg_path):
            if os.path.getmtime(cfg_path) > log_mtime:
                print(
                    f"WARNING: {cfg_label} config ({os.path.basename(cfg_path)}) "
                    f"was modified after the last pipeline run."
                )

# ── --scratch: symlink input/ from HOME → SCRATCH ───────────────────────────
if SCRATCH_FLAG:
    print(f"\n  [scratch] Linking input/ from HOME to SCRATCH...")
    run_command(f"mkdir -p {WORK_DIR}", cwd=MATERIAL_DIR)
    scratch_input = os.path.join(WORK_DIR, "input")
    if os.path.islink(scratch_input):
        os.unlink(scratch_input)
    elif os.path.isdir(scratch_input):
        shutil.rmtree(scratch_input)
    os.symlink(os.path.join(MATERIAL_DIR, "input"), scratch_input)
    print(f"  [scratch] Symlink created. VASP stages will run in: {WORK_DIR}")
    stale_on_home = []
    for d in ("scf", "hf", "raman"):
        dp = os.path.join(MATERIAL_DIR, d)
        if os.path.exists(dp) and not os.path.islink(dp):
            stale_on_home.append(d)
    if stale_on_home:
        print(f"  [scratch] WARNING: Stale intermediate directories found on HOME:")
        for d in stale_on_home:
            print(f"  [scratch]   {MATERIAL_DIR}/{d}/")

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKFLOW STEPS — dispatched to steps/ modules
# ═══════════════════════════════════════════════════════════════════════════════

from src import STEP_FUNCTIONS, build_context

ctx = build_context(
    write_status,
    CONFIG,
    MATERIAL_DIR,
    MATERIAL_NAME,
    WORK_DIR,
    SRUN_ARGS,
    VASP_BINARY_PATH,
    HFFILES_DIR,
    RAMAN_DIR,
    SCRIPT_DIR,
    BINARY_UTILITIES_DIR,
    CPU_FLAG,
    SCRATCH_FLAG,
    run_relaxation_with_zbrent_retry,
    vasp_loop_check_and_restart,
)

for step_num in sorted(STEP_FUNCTIONS):
    if step_num < START_STEP:
        continue
    fn = STEP_FUNCTIONS[step_num]
    fn_name = fn.__name__.split(".")[-1]
    print(f"\n  [dispatch] Running step {step_num} ({fn_name})")
    ctx["_step"] = step_num
    fn.run(ctx)
