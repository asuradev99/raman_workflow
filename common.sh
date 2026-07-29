#!/bin/bash
# =============================================================================
#  common.sh  —  STATIC shared helper for the lightweight Raman pipeline.
#
#  Checked into the repo (NOT generated). Sourced by every material's run_all.sh
#  and each generated step script:  source <repo>/common.sh
#
#  Holds (1) the environment preamble and (2) two tiny helper subroutines.
#  Everything here is material-independent, so one file serves all simulations.
#  The environment block below is cluster-agnostic: it reads CONDA_INIT,
#  CONDA_ENV, VASP_MODULES from ~/.bashrc (written by `install.sh nersc` or
#  `install.sh pathfinder`, see <cluster>.bashrc at the repo root). Porting to
#  a new cluster means adding a <name>.bashrc and re-running install.sh, or
#  hand-editing the ~/.bashrc block — never editing this file.
# =============================================================================

# ── Environment ──────────────────────────────────────────────────────────────
# CONDA_INIT/CONDA_ENV/VASP_MODULES come from the raman_workflow install.sh
# block in ~/.bashrc (see <cluster>.bashrc at the repo root). generate.py
# reads its own system-path env vars (VASP_BINARY, BINARY_UTILITIES_DIR, ...)
# the same way. Neither reads shared_workflow_settings.yaml's system_paths
# section, if one is present there — that section (when it exists) is for
# src/ (the old pipeline) only.
#
# set +u around this: some clusters' /etc/bashrc (sourced from within
# ~/.bashrc) references its own doublesource-guard variable (e.g.
# BASHRCSOURCED) without a default, which is fine under an interactive
# login shell's normal (unset -u) settings but is fatal under our
# set -euo pipefail -- confirmed on Pathfinder, where every generated
# script was silently crashing at this exact line (the crash's exit code
# coincidentally matched a real --check failure, which is how it went
# unnoticed until traced directly).
set +u
source ~/.bashrc
set -u
source "$CONDA_INIT"
# CONDA_ENV may be a real conda environment (has conda-meta/, e.g. NERSC's
# phonopy_env) or a plain `python3 -m venv` virtualenv (has bin/activate, no
# conda-meta/ -- e.g. a Pathfinder env built via TB2J's venv-based install
# recipe). `conda activate` errors out on the latter ("Not a conda
# environment"), and venvs don't understand `conda activate` either, so pick
# the one that actually applies instead of hardcoding either per cluster.
if [ -d "$CONDA_ENV/conda-meta" ]; then
    conda activate "$CONDA_ENV"
else
    source "$CONDA_ENV/bin/activate"
fi
module load $VASP_MODULES

# ── run_until_complete <step-script> ────────────────────────────────────────
# Run a step once unless it's already done (`step.sh --check` exit 0). No
# in-process retry -- a step either passes --check after running or this
# aborts loudly. The only retry left anywhere in the pipeline is the outer
# per-phase resubmit in run_all.sh, for a Slurm wall-time TIMEOUT killing the
# whole allocation; that's the one case a step-local retry can't cover at all.
run_until_complete() {
    local step="$1"
    echo "=== [run_until_complete] checking $step at $(date '+%H:%M:%S') ==="
    bash "$step" --check && { echo "=== $step already complete ==="; return 0; }
    echo "=== running $step at $(date '+%H:%M:%S') ==="
    bash "$step"
    bash "$step" --check || { echo "FATAL: $step did not complete" >&2; exit 1; }
    echo "=== $step complete at $(date '+%H:%M:%S') ==="
}

# ── phase_steps_done <step...> ──  true iff every step's --check already passes
# Used by run_all.sh's per-phase resubmit loop (see emit_run_all) to decide
# whether a Slurm TIMEOUT'd sbatch phase actually needs resubmitting, or
# whether every step in it already finished/checkpointed before the timeout.
phase_steps_done() {
    local st
    for st in "$@"; do
        bash "$st" --check || return 1
    done
    return 0
}

# ── resume_contcar ──  crash/requeue resume: continue from the checkpoint
# `set -e` treats a bare "[ cond ] && cmd" as the function's exit status, so
# when the file is absent (the common no-op case) an unguarded call kills the
# calling script instantly and silently. `|| true` neutralizes that.
resume_contcar() { [ -s CONTCAR ] && cp CONTCAR POSCAR || true; }
