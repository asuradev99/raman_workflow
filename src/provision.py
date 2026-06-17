#!/usr/bin/env python3
"""Provision compute resources and run the Raman pipeline.

Usage (called by scripts/run_raman_pipeline_auto.sh, or directly):
    cd /path/to/hBN_PBEsol_4x4x1
    python /path/to/raman_workflow/src/provision.py [--scratch] [--cpu] [--restart]

Reads config from input/workflow_settings.yaml (+ shared_workflow_settings.yaml),
requests the appropriate Slurm allocation for the configured compute_mode, runs
automation_raman_analysis.py inside the allocation, then exits with:

    0  — pipeline complete
    42 — preempted/incomplete — safe to retry (run_raman_pipeline_auto.sh loops on this)
    1  — fatal configuration or submission error

automation_raman_analysis.py is never aware of provisioning: it just runs pipeline
steps and assumes resources are already available.  Control flows one way:

    run_raman_pipeline_auto.sh
        → provision.py            (allocate, then call)
            → automation_raman_analysis.py   (run steps, exit)
        ← exit 0/42/1
"""
import argparse
import os
import subprocess
import sys

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_RAMAN_WORKFLOW_DIR = os.path.dirname(_SRC_DIR)
if _RAMAN_WORKFLOW_DIR not in sys.path:
    sys.path.insert(0, _RAMAN_WORKFLOW_DIR)

from util.config import load_config
from util.status import parse_resume_step, STEP_HISTORY
from util.slurm import build_bash_setup, run_via_salloc_pipe, submit_sbatch_wrapper

_PIPELINE_SCRIPT = os.path.join(_SRC_DIR, "automation_raman_analysis.py")

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument("--scratch", action="store_true",
                    help="Run VASP stages on $SCRATCH for faster I/O")
parser.add_argument("--cpu",     action="store_true",
                    help="Use CPU VASP binary instead of GPU")
parser.add_argument("--restart", action="store_true",
                    help="Delete all generated files and restart from scratch")
args = parser.parse_args()

SCRATCH_FLAG = args.scratch
CPU_FLAG     = args.cpu
RESTART_FLAG = args.restart

# ── Material dir (must be cwd — same convention as automation_raman_analysis.py) ─
MATERIAL_DIR  = os.getcwd()
MATERIAL_NAME = os.path.basename(MATERIAL_DIR)
CONFIG_PATH   = os.path.join(MATERIAL_DIR, "input", "workflow_settings.yaml")
if not os.path.isfile(CONFIG_PATH):
    print(f"Error: no input/workflow_settings.yaml in {MATERIAL_DIR}.")
    print("Run provision.py from a material directory (e.g. hBN_PBEsol_4x4x1/).")
    sys.exit(1)

BASE_PROJECT_DIR = os.environ.get("RAMAN_PROJECT_DIR", "")
if not os.path.isdir(BASE_PROJECT_DIR):
    print(f"Error: RAMAN_PROJECT_DIR '{BASE_PROJECT_DIR}' is not set or does not exist.")
    sys.exit(1)
SHARED_CONFIG = os.path.join(BASE_PROJECT_DIR, "shared_workflow_settings.yaml")

CONFIG = load_config([(SHARED_CONFIG, "shared"), (CONFIG_PATH, "per-material")])

sp           = CONFIG.get("system_paths", {})
compute_mode = CONFIG.get("compute_mode", "interactive_manual")
cm_cfg       = CONFIG.get("compute_modes", {}).get(compute_mode, {})

# ── Work dir and status file ──────────────────────────────────────────────────
scratch_base = os.environ.get("SCRATCH", "")
if SCRATCH_FLAG and scratch_base:
    work_dir = os.path.join(scratch_base, "vasp_calculations", MATERIAL_NAME)
else:
    work_dir = MATERIAL_DIR
STATUS_FILE = os.path.join(work_dir, "workflow.log")

# ── Python interpreter ────────────────────────────────────────────────────────
_conda_env  = sp.get("conda_env", "")
_conda_py   = os.path.join(_conda_env, "bin", "python3")
PYTHON_BIN  = _conda_py if (_conda_env and os.path.isfile(_conda_py)) else sys.executable

# ── Flags forwarded verbatim to automation_raman_analysis.py ─────────────────
_fwd = []
if SCRATCH_FLAG:  _fwd.append("--scratch")
if CPU_FLAG:      _fwd.append("--cpu")
if RESTART_FLAG:  _fwd.append("--restart")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _salloc_wrapper(*pipeline_flags):
    """Return a bash script that runs the pipeline with *pipeline_flags* added."""
    flags = " ".join(_fwd + list(pipeline_flags))
    return "\n".join([
        "#!/bin/bash -l",
        build_bash_setup(sp),
        f"export PYTHONPATH={_RAMAN_WORKFLOW_DIR}:$PYTHONPATH",
        f"export RAMAN_PROJECT_DIR={BASE_PROJECT_DIR}",
        f"cd {MATERIAL_DIR}",
        f"{PYTHON_BIN} {_PIPELINE_SCRIPT} {flags}",
    ])


def _run_direct(*pipeline_flags):
    """Run automation_raman_analysis.py directly (no allocation). Returns exit code."""
    cmd = [PYTHON_BIN, _PIPELINE_SCRIPT] + _fwd + list(pipeline_flags)
    result = subprocess.run(
        cmd,
        cwd=MATERIAL_DIR,
        env={**os.environ,
             "PYTHONPATH": f"{_RAMAN_WORKFLOW_DIR}:{os.environ.get('PYTHONPATH', '')}",
             "RAMAN_PROJECT_DIR": BASE_PROJECT_DIR},
    )
    return result.returncode


def _is_complete():
    """Return True if workflow.log shows every expected step COMPLETED."""
    # Deferred import: importing src triggers all step modules (phonopy etc).
    # Safe here — provision.py is a standalone script, not imported by anything.
    from src import expected_labels as get_expected_labels
    start_from_supercell = CONFIG.get("start_from_supercell", False)
    expected = get_expected_labels(CONFIG, start_from_supercell)
    next_label = parse_resume_step(STATUS_FILE, STEP_HISTORY, expected)
    return next_label is None


# ── Mode dispatch ─────────────────────────────────────────────────────────────
print(f"[provision] compute_mode={compute_mode!r}  material={MATERIAL_NAME}")

if compute_mode == "interactive_manual":
    # No allocation — user already has compute access or runs on a dev node.
    # Pass through the pipeline's exit code unchanged.
    sys.exit(_run_direct())

elif compute_mode == "interactive_serial":
    # Entire pipeline runs inside one interactive salloc; salloc may time out.
    salloc_args = cm_cfg.get("salloc", "") or cm_cfg.get("salloc_relax", "")
    if not salloc_args:
        print(f"Error: compute_modes.interactive_serial.salloc not configured.")
        sys.exit(1)
    try:
        run_via_salloc_pipe(
            _salloc_wrapper("--inside-salloc"),
            salloc_args=salloc_args,
            job_name=f"raman_{MATERIAL_NAME}",
            work_dir=work_dir,
        )
    except subprocess.CalledProcessError:
        pass  # salloc timeout/preemption — check the log for actual progress
    sys.exit(0 if _is_complete() else 42)

elif compute_mode in ("sbatch_parallel", "sbatch"):
    # Two-phase: GPU-only early steps inside salloc, then login-node continuation.
    #
    # Phase 1: relax + supercell + hf_setup run inside the salloc allocation.
    #          --salloc-steps tells the pipeline to stop after those steps.
    # Phase 2: force_constants + later steps run on the login node; they submit
    #          per-directory sbatch jobs from there and poll until done.
    salloc_args = cm_cfg.get("salloc_relax", "")
    if not salloc_args:
        print(f"Error: compute_modes.{compute_mode}.salloc_relax not configured.")
        sys.exit(1)

    print(f"[provision] Phase 1 — early steps in salloc ({salloc_args})")
    try:
        run_via_salloc_pipe(
            _salloc_wrapper("--inside-salloc", "--salloc-steps"),
            salloc_args=salloc_args,
            job_name=f"raman_{MATERIAL_NAME}",
            work_dir=work_dir,
        )
    except subprocess.CalledProcessError:
        pass

    if not _is_complete():
        print(f"[provision] Phase 2 — login-node continuation (per-dir sbatch dispatch)")
        _run_direct()  # resumes from where Phase 1 left off; return code informational only

    sys.exit(0 if _is_complete() else 42)

elif compute_mode in ("sbatch_serial", "sbatch_mix"):
    # One big sbatch job runs the full pipeline (--inside-salloc so per-step
    # dispatch doesn't try to allocate further sub-jobs).
    sbatch_args = cm_cfg.get("sbatch", "")
    if not sbatch_args:
        print(f"Error: compute_modes.{compute_mode}.sbatch not configured.")
        sys.exit(1)
    print(f"[provision] Submitting sbatch job ({sbatch_args})")
    ok = submit_sbatch_wrapper(
        _salloc_wrapper("--inside-salloc"),
        job_name=f"raman_{MATERIAL_NAME}",
        output_dir=work_dir,
        sbatch_args=sbatch_args,
    )
    if not ok:
        print(f"[provision] sbatch submission or execution failed.")
        sys.exit(1)
    sys.exit(0 if _is_complete() else 42)

else:
    print(f"Error: unknown compute_mode {compute_mode!r}.")
    print(f"  Valid values: interactive_manual, interactive_serial, "
          f"sbatch_parallel, sbatch, sbatch_serial, sbatch_mix")
    sys.exit(1)
