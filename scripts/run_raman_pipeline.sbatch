#!/bin/bash
#SBATCH -N 1
#SBATCH -C gpu
#SBATCH -q preempt
#SBATCH -t 08:00:00
#SBATCH -A m526
#SBATCH --gpus-per-node=4
#SBATCH --job-name=raman_pipeline
# NOTE: --output/--error defaults below land in ~/log/. The preferred
# submission path is submit_parallel_preempt.sh, which overrides these
# to place logs in each material's own directory.
#SBATCH --output=/global/homes/e/easuresh/log/raman_%j.out
#SBATCH --error=/global/homes/e/easuresh/log/raman_%j.err
#SBATCH --mail-type=BEGIN,END,FAIL,REQUEUE
#SBATCH --mail-user=ethanaditya@gmail.com
#SBATCH --time-min=02:00:00
#
# =============================================================================
#  Raman Pipeline — Slurm Batch Job
# =============================================================================
#  Usage:
#    1. Edit MATERIAL_NAME below to match your target material directory
#    2. Submit with:  sbatch run_raman_pipeline.sbatch
#
#  Status tracking:
#    The Python pipeline writes a combined status/log file (workflow.log)
#    in the material directory on HOME using a box-drawn table format.
#    Monitor with:
#      tail -f $RAMAN_PROJECT_DIR/<material>/workflow.log
# =============================================================================

# ── User Configuration (set before submission) ─────────────────────────────
# MATERIAL_NAME can be overridden via --export when submitting with
# submit_parallel_preempt.sh; fall back to "hBN" for direct sbatch calls.
MATERIAL_NAME="${MATERIAL_NAME:-hBN}"

# ── Pipeline script location ────────────────────────────────────────────────
# $0 is unreliable inside Slurm jobs (it points to the spool dir).
# Use the known project location instead.
PIPELINE_DIR="$HOME/raman_workflow"
AUTOMATION_SCRIPT="$PIPELINE_DIR/automation_raman_analysis.py"
# ─────────────────────────────────────────────────────────────────────────────

# ── Parse flags ─────────────────────────────────────────────────────────────
# Defaults: scratch enabled, GPU mode, no restart.
# Pass --no-scratch to run VASP on HOME (not recommended).
RESTART_FLAG=""
CPU_FLAG=""
SCRATCH_FLAG="--scratch"
for arg in "$@"; do
    if [ "$arg" = "--restart" ]; then
        RESTART_FLAG="--restart"
    elif [ "$arg" = "--no-scratch" ]; then
        SCRATCH_FLAG=""
    elif [ "$arg" = "--cpu" ]; then
        CPU_FLAG="--cpu"
    fi
done
# ─────────────────────────────────────────────────────────────────────────────

# ── Environment Setup ───────────────────────────────────────────────────────
set -euo pipefail

# Source .bashrc to get environment variables
source ~/.bashrc

# Verify required environment variables
for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR VASP_BINARY; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done

MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL_NAME"
STATUS_FILE="$MATERIAL_DIR/workflow.log"

# ── Read display label from per-material workflow_settings.yaml ──────────────
MATERIAL_LABEL="$MATERIAL_NAME"
_CONFIG_FILE="$MATERIAL_DIR/input/workflow_settings.yaml"
if [ -f "$_CONFIG_FILE" ]; then
    _PARSED="$(grep -oP '^name:\s*\K.+' "$_CONFIG_FILE" 2>/dev/null || true)"
    [ -n "$_PARSED" ] && MATERIAL_LABEL="$_PARSED"
fi

echo "============================================"
echo " Raman Pipeline Batch Job"
echo " Material:      $MATERIAL_LABEL  ($MATERIAL_NAME)"
echo " Project Dir:   $RAMAN_PROJECT_DIR"
echo " Material Dir:  $MATERIAL_DIR"
echo " Binary Utils:  $BINARY_UTILITIES_DIR"
echo " VASP Binary:   $VASP_BINARY"
echo " Status File:   $STATUS_FILE"
echo " Job ID:        $SLURM_JOB_ID"
echo "============================================"

# Validate material directory exists
if [ ! -d "$MATERIAL_DIR" ]; then
    echo "ERROR: Material directory $MATERIAL_DIR does not exist."
    exit 1
fi

# NOTE: Status/log management is handled entirely by the Python pipeline
# (write_status() + Tee). No shell-level status file is created here.
# The cleanup trap only prints to stdout for the batch log.
cleanup() {
    local exit_code=$?
    echo ""
    if [ $exit_code -ne 0 ]; then
        echo "=== Pipeline failed with exit code $exit_code ==="
    else
        echo "=== Pipeline completed successfully ==="
    fi
}
trap cleanup EXIT

# ── Preflight: verify binaries are executable ───────────────────────────────
echo "Checking binaries..."
for bin in ramdiscar genRApos610 runRA raman_tensor broadening; do
    if [ ! -x "${BINARY_UTILITIES_DIR}/${bin}" ]; then
        echo "ERROR: ${bin} not executable at ${BINARY_UTILITIES_DIR}/${bin}"
        exit 1
    fi
done
echo "  All required binaries found."

# ── Module Loading ──────────────────────────────────────────────────────────
echo "Loading modules..."
# Use $VASP_MODULES from ~/.bashrc if set, otherwise fall back to the default list.
module load ${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}

# ── Conda Activation ────────────────────────────────────────────────────────
echo "Activating phonopy conda environment..."
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env

# ── Navigate to Material Directory ──────────────────────────────────────────
cd "$MATERIAL_DIR" || { echo "ERROR: Cannot cd to $MATERIAL_DIR"; exit 1; }
echo "Working directory: $(pwd)"

# ── Run Automation Script ──────────────────────────────────────────────────
# The automation_raman_analysis.py script handles all status tracking
# via write_status() calls at each step. This wrapper only writes the
# initial header (above) and final cleanup (trap).

echo "Running: python $AUTOMATION_SCRIPT $RESTART_FLAG $CPU_FLAG $SCRATCH_FLAG"
python "$AUTOMATION_SCRIPT" $RESTART_FLAG $CPU_FLAG $SCRATCH_FLAG

echo "============================================"
echo " Pipeline completed successfully."
echo " Check $STATUS_FILE for detailed status."
echo "============================================"
