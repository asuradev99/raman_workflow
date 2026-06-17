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
    Tee, run_command, load_config, validate_config, get_srun_args,
    parse_resume_step, make_pipeline_excepthook, run_relaxation,
    print_job_header, make_write_status, STEP_HISTORY,
    set_expected_labels, do_restart_cleanup, require_path,
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
parser.add_argument(
    "--salloc-steps",
    action="store_true",
    help="Internal: run only salloc-required steps and exit"
)
parser.add_argument(
    "--inside-salloc",
    action="store_true",
    help="Internal: pipeline is running inside a provisioned allocation — use srun directly",
)
args, remaining = parser.parse_known_args()
sys.argv = [sys.argv[0]] + remaining

RESTART_FLAG = args.restart
CPU_FLAG = args.cpu
SCRATCH_FLAG = args.scratch
SALLOC_STEPS_FLAG = args.salloc_steps
INSIDE_SALLOC_FLAG = args.inside_salloc

# ── Material directory — must be CWD (run from inside a material dir) ─────────
MATERIAL_DIR = os.getcwd()
CWD_BASENAME = os.path.basename(MATERIAL_DIR)

CONFIG_PATH = os.path.join(MATERIAL_DIR, "input", "workflow_settings.yaml")
if not os.path.isfile(CONFIG_PATH):
    print(f"Error: no input/workflow_settings.yaml found in {MATERIAL_DIR}.")
    print("Run this script from a material directory (e.g., hBN_PBEsol_4x4x1/).")
    sys.exit(1)

# ── Shared config location (requires RAMAN_PROJECT_DIR) ──────────────────────
BASE_PROJECT_DIR = os.environ.get("RAMAN_PROJECT_DIR", "")
if not os.path.isdir(BASE_PROJECT_DIR):
    print(f"Error: RAMAN_PROJECT_DIR '{BASE_PROJECT_DIR}' not set or does not exist.")
    print("Set the RAMAN_PROJECT_DIR environment variable to your project directory.")
    sys.exit(1)

# Preliminary WORK_DIR (refined after config load) — used for log files too
_scratch_base = os.environ.get("SCRATCH", "")
WORK_DIR = os.path.join(_scratch_base, "vasp_calculations", CWD_BASENAME) if SCRATCH_FLAG and _scratch_base else MATERIAL_DIR

# workflow.log and workflow.out live in WORK_DIR (pscratch by default)
STATUS_FILE = os.path.join(WORK_DIR, "workflow.log")
OUTPUT_FILE = os.path.join(WORK_DIR, "workflow.out")

# ── --restart: delete all generated directories, keep input/ + config ────────
if RESTART_FLAG:
    do_restart_cleanup(MATERIAL_DIR, WORK_DIR, SCRATCH_FLAG)

# Redirect ALL output AFTER restart cleanup so logs are fresh.
# (Must come after cleanup — otherwise Tee holds file handles open,
#  preventing log files from being properly replaced.)
sys.stdout = Tee(STATUS_FILE, OUTPUT_FILE)

# Config inheritance: shared YAML → per-material YAML
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_CONFIG_PATH = os.path.join(BASE_PROJECT_DIR, "shared_workflow_settings.yaml")

CONFIG = load_config([
    (SHARED_CONFIG_PATH, "shared"),
    (CONFIG_PATH, "per-material"),
])

validate_config(CONFIG)

# ── System paths — from YAML config, with env-var override for backward compat ─
_sp = CONFIG.get("system_paths", {})
BINARY_UTILITIES_DIR = os.environ.get("BINARY_UTILITIES_DIR") or _sp.get(
    "binary_utilities_dir", ""
)
VASP_BINARY_PATH = (
    os.environ.get("VASP_BINARY_CPU") or _sp.get("vasp_binary_cpu", "")
    if CPU_FLAG
    else os.environ.get("VASP_BINARY") or _sp.get("vasp_binary", "")
)

require_path(VASP_BINARY_PATH, "VASP binary", os.path.isfile,
             "Set system_paths.vasp_binary in config or VASP_BINARY env var.")
print(f"VASP binary: {VASP_BINARY_PATH}" + (" (CPU)" if CPU_FLAG else ""))
if CPU_FLAG:
    print("  (CPU mode: --cpu flag set)")

require_path(BINARY_UTILITIES_DIR, "binary_utilities_dir", os.path.isdir,
             "Set system_paths.binary_utilities_dir in config or BINARY_UTILITIES_DIR env var.")
print(f"Binary utilities directory found: {BINARY_UTILITIES_DIR}")

# ── Material identity ─────────────────────────────────────────────────────────
MATERIAL_NAME = CWD_BASENAME
MATERIAL_LABEL = CONFIG.get("material") or MATERIAL_NAME

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

sys.excepthook = make_pipeline_excepthook(STATUS_FILE)

# ── Compute mode — must be defined before print_job_header ───────────────────
COMPUTE_MODE = CONFIG.get("compute_mode", "interactive_manual")
SRUN_ARGS = get_srun_args(CONFIG, COMPUTE_MODE, "srun_relax", CPU_FLAG)

# ── Job start header ──────────────────────────────────────────────────────────
print_job_header(
    material_label=MATERIAL_LABEL,
    material_name=MATERIAL_NAME,
    work_dir=WORK_DIR,
    status_file=STATUS_FILE,
    scratch_flag=SCRATCH_FLAG,
    restart_flag=RESTART_FLAG,
    cpu_flag=CPU_FLAG,
    compute_mode=COMPUTE_MODE,
    inside_salloc=INSIDE_SALLOC_FLAG or SALLOC_STEPS_FLAG,
)

# Pre-bound write_status (created by util.make_write_status)
write_status = make_write_status(
    STATUS_FILE,
    MATERIAL_LABEL,
    MATERIAL_NAME,
    BASE_PROJECT_DIR,
)


# --- Workflow Steps ---

print(
    f"Starting Raman automation for {MATERIAL_LABEL} ({MATERIAL_NAME}) in {MATERIAL_DIR}"
)
print(f"Current working directory: {os.getcwd()}")


# ── Resume: skip completed steps via workflow.log ──────────────────────────
# Step *numbers* carry no identity anywhere below — resume matching is done
# purely by label (each step's human-readable description). EXPECTED is the
# full ordered label sequence for this material's config (the defect relax
# step contributes 1 or 2 labels depending on whether the two-stage
# relax 1 + relax 2 applies); set_expected_labels() must run before
# parse_resume_step() so its "is everything completed?" fallback check has
# something to compare against.
from src import (
    PIPELINE, expected_labels, SALLOC_REQUIRED_STEP_NAMES, PipelineContext,
)

START_FROM_SUPERCELL = CONFIG.get("start_from_supercell", False)
EXPECTED = expected_labels(CONFIG, START_FROM_SUPERCELL)
set_expected_labels(EXPECTED)

START_LABEL = parse_resume_step(STATUS_FILE, STEP_HISTORY, EXPECTED)
if START_LABEL is None:
    sys.exit(0)

print(f"[resume] START_LABEL = \"{START_LABEL}\" — starting pipeline execution.")

# ── Config staleness warning ─────────────────────────────────────────────────
if START_LABEL != EXPECTED[0] and os.path.exists(STATUS_FILE):
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

ctx = PipelineContext(
    raw_config=CONFIG,
    material_dir=MATERIAL_DIR,
    material_name=MATERIAL_NAME,
    work_dir=WORK_DIR,
    srun_args=SRUN_ARGS,
    vasp_binary=VASP_BINARY_PATH,
    hffiles_dir=HFFILES_DIR,
    raman_dir=RAMAN_DIR,
    script_dir=SCRIPT_DIR,
    binary_utilities_dir=BINARY_UTILITIES_DIR,
    cpu_flag=CPU_FLAG,
    scratch_flag=SCRATCH_FLAG,
    run_relaxation=run_relaxation,
    write_status=write_status,
    inside_salloc=INSIDE_SALLOC_FLAG or SALLOC_STEPS_FLAG,
)

# ── Step dispatch ─────────────────────────────────────────────────────────────
# Skip decision is purely label-rank based: a dispatched unit is skipped only
# if ALL of its labels fall strictly before the resume point. Step numbers
# are never consulted here — `step.name` (a stable slug) is used only for
# the --salloc-steps boundary and dispatch log lines.
resume_idx = EXPECTED.index(START_LABEL)
for step in PIPELINE:
    step_labels = step.resolved_labels(CONFIG, START_FROM_SUPERCELL)
    if max(EXPECTED.index(l) for l in step_labels) < resume_idx:
        continue
    if SALLOC_STEPS_FLAG and step.name not in SALLOC_REQUIRED_STEP_NAMES:
        print(f"\n  [salloc-steps] Done with salloc-required steps. Exiting.")
        break
    ctx.current_label = step_labels[0]
    print(f"\n  [dispatch] {step.name} — {', '.join(step_labels)}")
    step.run(ctx)
