#!/bin/bash
# =============================================================================
#  new/common.sh  —  STATIC shared helper for the lightweight Raman pipeline.
#
#  Checked into the repo (NOT generated). Sourced by every material's run_all.sh
#  and each generated step script:  source <repo>/new/common.sh
#
#  Holds (1) the environment preamble and (2) two tiny helper subroutines.
#  Everything here is material-independent, so one file serves all simulations.
#  Only the environment block below is cluster-specific — edit it to port.
# =============================================================================

# ── Environment (Perlmutter) ────────────────────────────────────────────────
# Mirrors system_paths.{conda_init, conda_env, vasp_modules} in the shared YAML.
source ~/.bashrc
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env
module load gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu

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
