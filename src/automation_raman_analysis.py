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
    parse_resume_step, make_pipeline_excepthook, run_relaxation_with_zbrent_retry,
    print_job_header, make_write_status, STEP_HISTORY, STEP_DESCRIPTIONS,
    populate_step_descriptions,
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
    help="Internal: run only salloc-required steps (1-3) and exit"
)
parser.add_argument(
    "--inside-salloc",
    action="store_true",
    help="Internal: pipeline is already inside an salloc allocation — skip auto-provision",
)
args, remaining = parser.parse_known_args()
sys.argv = [sys.argv[0]] + remaining

RESTART_FLAG = args.restart
CPU_FLAG = args.cpu
SCRATCH_FLAG = args.scratch
SALLOC_STEPS_FLAG = args.salloc_steps
INSIDE_SALLOC_FLAG = args.inside_salloc

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

# Preliminary WORK_DIR (refined after config load) — used for log files too
_scratch_base = os.environ.get("SCRATCH", "")
WORK_DIR = os.path.join(_scratch_base, "vasp_calculations", CWD_BASENAME) if SCRATCH_FLAG and _scratch_base else MATERIAL_DIR

# workflow.log and workflow.out live in WORK_DIR (pscratch by default)
STATUS_FILE = os.path.join(WORK_DIR, "workflow.log")
OUTPUT_FILE = os.path.join(WORK_DIR, "workflow.out")

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

    # Remove all log files (clean start) — check both HOME and WORK_DIR
    for log_name in ("workflow.log", "workflow.out", "salloc_output.log"):
        for base in (MATERIAL_DIR, WORK_DIR):
            log_path = os.path.join(base, log_name)
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

# Config inheritance: shared YAML → per-material YAML
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(MATERIAL_DIR, "input", "workflow_settings.yaml")
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

if not VASP_BINARY_PATH:
    print("Error: VASP binary not configured. Set system_paths.vasp_binary in config "
          "or VASP_BINARY env var.")
    sys.exit(1)
if not os.path.isfile(VASP_BINARY_PATH):
    print(f"Error: VASP binary not found at '{VASP_BINARY_PATH}'")
    sys.exit(1)
print(f"VASP binary: {VASP_BINARY_PATH}" + (" (CPU)" if CPU_FLAG else ""))
if CPU_FLAG:
    print(f"  (CPU mode: --cpu flag set)")

if not BINARY_UTILITIES_DIR:
    print("Error: binary_utilities_dir not set. Add to system_paths in config.")
    sys.exit(1)
if not os.path.isdir(BINARY_UTILITIES_DIR):
    print(f"Error: BINARY_UTILITIES_DIR '{BINARY_UTILITIES_DIR}' does not exist.")
    print("Set the BINARY_UTILITIES_DIR environment variable to a valid directory.")
    sys.exit(1)
print(f"Binary utilities directory found: {BINARY_UTILITIES_DIR}")

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

from src import PIPELINE, STEP_BY_NUMBER, build_context
from util.compute import build_bash_setup, run_pipeline_in_salloc

# Derive status-table descriptions from the Step registry (single source of truth)
populate_step_descriptions(
    {s.number: s.description for s in PIPELINE} | {"final": "Pipeline complete"}
)

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
    inside_salloc=INSIDE_SALLOC_FLAG or SALLOC_STEPS_FLAG,
)

# ── Auto-provision: wrap pipeline in salloc when not already inside one ──────
_AUTO_MODES = frozenset({
    "interactive_serial",
    "sbatch_parallel", "interactive_parallel", "sbatch",
})
_SALLOC_FULL_MODES = frozenset({"interactive_serial", "interactive_parallel"})
if COMPUTE_MODE in _AUTO_MODES and not (INSIDE_SALLOC_FLAG or SALLOC_STEPS_FLAG):
    import subprocess
    full_pipeline = COMPUTE_MODE in _SALLOC_FULL_MODES
    print(f"\n  [auto-provision] Wrapping pipeline in salloc ({COMPUTE_MODE})…")
    try:
        run_pipeline_in_salloc(ctx, full_pipeline=full_pipeline)
    except subprocess.CalledProcessError:
        pass

    if full_pipeline:
        next_step = parse_resume_step(STATUS_FILE, STEP_HISTORY, STEP_DESCRIPTIONS)
        sep = "  " + "=" * 70
        print(f"\n{sep}")
        if next_step is None:
            print(f"  [pipeline] SALLOC RELEASED — pipeline COMPLETE.")
            print(f"  [pipeline] Log: {STATUS_FILE}")
            print(f"{sep}\n")
            sys.exit(0)
        else:
            print(f"  [pipeline] SALLOC RELEASED — pipeline INCOMPLETE (next: step {next_step}).")
            print(f"  [pipeline] Retry will resume from step {next_step}.")
            print(f"  [pipeline] Log: {STATUS_FILE}")
            print(f"{sep}\n")
            sys.exit(42)
    else:
        print(f"  [auto-provision] SALLOC done — continuing steps 4+ on login node.")
        START_STEP = max(START_STEP, 4)

# ── sbatch-serial: wrap entire pipeline in a single sbatch (no salloc) ──────
elif COMPUTE_MODE == "sbatch_serial" and not (INSIDE_SALLOC_FLAG or SALLOC_STEPS_FLAG):
    from util.compute import submit_sbatch_wrapper
    sp = ctx.system_paths
    raman_workflow_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python_bin = os.path.join(sp.get("conda_env", ""), "bin", "python3")
    if not os.path.isfile(python_bin):
        python_bin = "python3"
    flags = ["--inside-salloc"]
    if SCRATCH_FLAG:
        flags.append("--scratch")
    if CPU_FLAG:
        flags.append("--cpu")
    wrapper = "\n".join([
        "#!/bin/bash -l",
        build_bash_setup(sp),
        f"export PYTHONPATH={raman_workflow_dir}:$PYTHONPATH",
        f"cd {MATERIAL_DIR}",
        f"export RAMAN_PROJECT_DIR={os.environ.get('RAMAN_PROJECT_DIR', '')}",
        f"{python_bin} -m src.automation_raman_analysis {' '.join(flags)}",
    ])
    print(f"\n  [sbatch-serial] Submitting full pipeline as single sbatch…")
    ok = submit_sbatch_wrapper(
        wrapper,
        job_name=f"raman_{MATERIAL_NAME}",
        output_dir=WORK_DIR,
        walltime="04:00:00",
        nodes=1,
    )
    if not ok:
        print("  [sbatch-serial] sbatch job failed. Check slurm logs.")
        sys.exit(1)
    # Check completion
    next_step = parse_resume_step(STATUS_FILE, STEP_HISTORY, STEP_DESCRIPTIONS)
    sep = "  " + "=" * 70
    print(f"\n{sep}")
    if next_step is None:
        print(f"  [sbatch-serial] sbatch COMPLETED — pipeline COMPLETE.")
        print(f"  [sbatch-serial] Log: {STATUS_FILE}")
        print(f"{sep}\n")
        sys.exit(0)
    else:
        print(f"  [sbatch-serial] sbatch COMPLETED — pipeline INCOMPLETE (next: step {next_step}).")
        print(f"  [sbatch-serial] Log: {STATUS_FILE}")
        print(f"{sep}\n")
        sys.exit(1)

# ── Step dispatch ─────────────────────────────────────────────────────────────
for step in PIPELINE:
    if step.number < START_STEP:
        continue
    if SALLOC_STEPS_FLAG and step.number > 3:
        print(f"\n  [salloc-steps] Done with salloc-required steps (1–3). Exiting.")
        break
    ctx.current_step = step.number
    print(f"\n  [dispatch] Step {step.number} — {step.description} ({step.name})")
    step.run(ctx)
