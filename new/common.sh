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
    echo "=== [run_until_complete] checking $step at $(date '+%H:%M:%S') ==="
    until bash "$step" --check; do
        if (( tries >= MAX_RETRIES )); then
            echo "FATAL: $step did not complete after $MAX_RETRIES attempts" >&2
            exit 1
        fi
        tries=$(( tries + 1 ))
        echo "=== running $step (attempt $tries) at $(date '+%H:%M:%S') ==="
        bash "$step"
        rc=$?
        if [ "$rc" -ne 0 ]; then
            echo "=== $step exited $rc at $(date '+%H:%M:%S'); retrying in 60s ==="
            sleep 60
        fi
    done
    echo "=== $step complete at $(date '+%H:%M:%S') ==="
}

# ── resubmit_until_done <jobname> <sbatch-args-string> <script-file> <step...> ─
# Wraps a whole sbatch --wait phase in its own retry loop. A phase job can die
# for reasons run_until_complete can't retry from inside (Slurm TIMEOUT kills
# the entire allocation, including the shell running the retry loop) -- this
# is the outer layer that resubmits the sbatch job itself when that happens.
# script_file holds the heredoc body to submit (written by the caller so this
# stays a plain function, no quoting-through-quoting).  Bounded by MAX_RETRIES,
# same as run_until_complete, so a genuinely broken phase still aborts loudly
# instead of resubmitting forever.
: "${MAX_PHASE_RETRIES:=10}"
resubmit_until_done() {
    local jobname="$1" sbatch_args="$2" script_file="$3"; shift 3
    local steps=("$@") tries=0
    while true; do
        local all_done=1
        for st in "${steps[@]}"; do
            bash "$st" --check || { all_done=0; break; }
        done
        if [ "$all_done" -eq 1 ]; then
            echo "=== [resubmit_until_done] $jobname: all steps already done ==="
            return 0
        fi
        if (( tries >= MAX_PHASE_RETRIES )); then
            echo "FATAL: $jobname phase did not complete after $MAX_PHASE_RETRIES sbatch submissions" >&2
            exit 1
        fi
        tries=$(( tries + 1 ))
        echo "=== [resubmit_until_done] $jobname: submitting sbatch (phase attempt $tries) at $(date '+%H:%M:%S') ==="
        sbatch --wait $sbatch_args --requeue -J "$jobname" \
            --mail-type=BEGIN,FAIL,END --mail-user=easuresh@mit.edu "$script_file"
        echo "=== [resubmit_until_done] $jobname: sbatch --wait returned at $(date '+%H:%M:%S') ==="
    done
}

# ── resume_contcar ──  crash/requeue resume: continue from the checkpoint
# `set -e` treats a bare "[ cond ] && cmd" as the function's exit status, so
# when the file is absent (the common no-op case) an unguarded call kills the
# calling script instantly and silently. `|| true` neutralizes that.
resume_contcar() { [ -s CONTCAR ] && cp CONTCAR POSCAR || true; }
