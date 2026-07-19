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
# The entire "orchestration". A step reports done via `bash step.sh --check`
# (exit 0). If not done, run it; if the run exits nonzero, wait and retry.
# A hard cap (MAX_RETRIES) prevents an infinite loop on a genuinely broken step
# — it aborts loudly instead. Under Slurm --requeue a preempted step is simply
# re-run on its next turn and resumes from its own checkpoint (idempotent).
: "${MAX_RETRIES:=10}"
run_until_complete() {
    local step="$1" tries=0
    until bash "$step" --check; do
        if (( tries >= MAX_RETRIES )); then
            echo "FATAL: $step did not complete after $MAX_RETRIES attempts" >&2
            exit 1
        fi
        tries=$(( tries + 1 ))
        echo "=== running $step (attempt $tries) ==="
        bash "$step" || { echo "$step exited nonzero; retrying in 60s"; sleep 60; }
    done
    echo "=== $step complete ==="
}

# ── resume_contcar ──  crash/requeue resume: continue from the checkpoint
resume_contcar() { [ -s CONTCAR ] && cp CONTCAR POSCAR; }
