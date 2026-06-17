#!/bin/bash
# =============================================================================
#  Raman Pipeline — Interactive Session Runner (tmux-friendly)
# =============================================================================
#  Usage:
#    1. (Recommended) Start a tmux session:  tmux new-session -s raman
#    2. Allocate a GPU node:
#         salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526
#    3. Once granted, run:
#         bash raman_workflow/run_raman_pipeline_interactive.sh <material>
#
#    Ctrl+B, d  to detach from tmux;  tmux attach -t raman  to reconnect.
#
#  Flags:
#    bash run_raman_pipeline_interactive.sh <material> [--restart] [--no-scratch]
#
#  Status tracking:
#    workflow.log is written to $SCRATCH/vasp_calculations/<material>/ (with --scratch)
#    or to the material directory on HOME (with --no-scratch).
#    Monitor from a second terminal:
#      tail -f $SCRATCH/vasp_calculations/<material>/workflow.log  # default (--scratch)
#      tail -f $RAMAN_PROJECT_DIR/<material>/workflow.log          # --no-scratch
# =============================================================================

set -euo pipefail

# ── Parse arguments ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAMAN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"  # raman_workflow/ root
DEFAULT_MATERIAL="hBN"
RESTART_FLAG=""
SCRATCH_FLAG="--scratch"
MATERIAL_NAME=""

for arg in "$@"; do
    case "$arg" in
        --restart)     RESTART_FLAG="--restart" ;;
        --no-scratch)  SCRATCH_FLAG="" ;;
        -h|--help)
            echo "Usage: bash run_raman_pipeline_interactive.sh [material] [flags]"
            echo ""
            echo "  material      Subdirectory inside \$RAMAN_PROJECT_DIR (default: hBN)"
            echo "  --restart     Delete all generated files, restart from scratch"
            echo "  --no-scratch  Run VASP on HOME instead of \$SCRATCH"
            echo ""
            echo "  Prerequisites:"
            echo "    1. Run inside an active salloc session on a compute node"
            echo "    2. \$RAMAN_PROJECT_DIR, \$BINARY_UTILITIES_DIR, \$VASP_BINARY set in ~/.bashrc"
            echo ""
            echo "  Examples:"
            echo "    salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526"
            echo "    bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_4x4x1"
            echo "    bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_4x4x1 --restart"
            exit 0
            ;;
        *)
            if [ -z "$MATERIAL_NAME" ]; then
                MATERIAL_NAME="$arg"
            else
                echo "ERROR: Unknown argument: $arg"
                exit 1
            fi
            ;;
    esac
done
MATERIAL_NAME="${MATERIAL_NAME:-$DEFAULT_MATERIAL}"

# ── Check: Running inside salloc? ──────────────────────────────────────────
if [ -z "${SLURM_JOB_ID:-}" ]; then
    echo "ERROR: Not running inside a Slurm allocation."
    echo "Allocate a GPU node first:"
    echo "  salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526"
    echo "Then run this script from the allocated shell."
    exit 1
fi

# ── Source environment + validate ──────────────────────────────────────────
source ~/.bashrc 2>/dev/null || true

for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR VASP_BINARY; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done

MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL_NAME"
STATUS_FILE="$MATERIAL_DIR/workflow.log"

# ── Read display label from per-material workflow_settings.yaml ────────────
MATERIAL_LABEL="$MATERIAL_NAME"
_CONFIG_FILE="$MATERIAL_DIR/input/workflow_settings.yaml"
if [ -f "$_CONFIG_FILE" ]; then
    _PARSED="$(grep -oP '^name:\s*\K.+' "$_CONFIG_FILE" 2>/dev/null || true)"
    [ -n "$_PARSED" ] && MATERIAL_LABEL="$_PARSED"
fi

echo "════════════════════════════════════════════════════════════"
echo "  Raman Pipeline — Interactive Mode"
echo "  Material:     $MATERIAL_LABEL  ($MATERIAL_NAME)"
echo "  Job ID:       $SLURM_JOB_ID"
echo "  Node:         $(hostname)"
echo "  Project Dir:  $RAMAN_PROJECT_DIR"
echo "  Material Dir: $MATERIAL_DIR"
echo "  Status File:  $STATUS_FILE"
echo "  Scratch:      ${SCRATCH_FLAG:---scratch}"
echo "════════════════════════════════════════════════════════════"

# Validate material directory
if [ ! -d "$MATERIAL_DIR" ]; then
    echo "ERROR: Material directory $MATERIAL_DIR does not exist."
    exit 1
fi

# ── Preflight: verify binaries ─────────────────────────────────────────────
echo ""
echo "Checking binaries..."
for bin in ramdiscar genRApos610 runRA raman_tensor broadening; do
    if [ ! -x "${BINARY_UTILITIES_DIR}/${bin}" ]; then
        echo "ERROR: ${bin} not executable at ${BINARY_UTILITIES_DIR}/${bin}"
        exit 1
    fi
done
echo "  All required binaries found."

# ── Load modules ───────────────────────────────────────────────────────────
echo ""
echo "Loading GPU modules..."
module load ${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}
echo "  Modules loaded."

# ── Activate conda ─────────────────────────────────────────────────────────
echo ""
echo "Activating phonopy conda environment..."
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env
echo "  Conda environment active."

# ── Cleanup trap ───────────────────────────────────────────────────────────
cleanup() {
    local exit_code=$?
    echo ""
    echo "════════════════════════════════════════════════════════════"
    if [ $exit_code -ne 0 ]; then
        echo "  Pipeline FAILED (exit code $exit_code)"
        echo "  Check: tail -80 $STATUS_FILE"
    else
        echo "  Pipeline COMPLETED successfully"
        echo "  Results: $MATERIAL_DIR/output/"
    fi
    echo "════════════════════════════════════════════════════════════"
}
trap cleanup EXIT

# ── Run ────────────────────────────────────────────────────────────────────
echo ""
echo "Running pipeline..."
echo ""

cd "$MATERIAL_DIR" || { echo "ERROR: Cannot cd to $MATERIAL_DIR"; exit 1; }
echo "Working directory: $(pwd)"
echo ""

export PYTHONPATH="$RAMAN_DIR:${PYTHONPATH:-}"
python "$RAMAN_DIR/src/automation_raman_analysis.py" $RESTART_FLAG $SCRATCH_FLAG
