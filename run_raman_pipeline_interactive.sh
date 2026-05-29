#!/bin/bash
# =============================================================================
#  Raman Pipeline — Interactive Session Runner (tmux-friendly)
# =============================================================================
#  ╔═══════════════════════════════════════════════════════════════════════════╗
#  ║  RECOMMENDED: Run inside `tmux` to survive SSH disconnects               ║
#  ║                                                                          ║
#  ║    tmux new-session -s raman                                            ║
#  ║    salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 \                  ║
#  ║           --qos=interactive -A m526                                      ║
#  ║    # (wait for shell on compute node)                                    ║
#  ║    bash raman_workflow/run_raman_pipeline_interactive.sh hBN             ║
#  ║    # Ctrl+B, d  to detach from tmux                                      ║
#  ║    # Reconnect: tmux attach -t raman                                     ║
#  ╚═══════════════════════════════════════════════════════════════════════════╝
#
#  Usage:
#    1. (Recommended) Start a tmux session:  tmux new-session -s raman
#    2. Allocate a GPU node:
#         salloc -N 1 -C gpu --gpus-per-node=4 \
#                -t 04:00:00 --qos=interactive -A m526
#    3. Once granted, run:  bash run_raman_pipeline_interactive.sh [material]
#
#  How tmux helps:
#    - tmux runs as a background process on the LOGIN node
#    - salloc runs INSIDE tmux, so it doesn't die when SSH drops
#    - The compute node allocation stays alive even if you disconnect
#    - Reconnect later:  tmux attach -t raman
#
#  Status tracking:
#    A file named workflow_status.txt is written to $MATERIAL_DIR
#    after each step completes. Monitor with:
#      watch -n 30 cat $MATERIAL_DIR/workflow_status.txt
# =============================================================================

set -euo pipefail

# ── Parse arguments ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_MATERIAL="hBN"
RESTART_FLAG=""
MATERIAL_NAME=""

# Parse: first non-flag argument is the material name; look for --restart anywhere
for arg in "$@"; do
    if [ "$arg" = "--restart" ]; then
        RESTART_FLAG="--restart"
    elif [ "$arg" = "-h" ] || [ "$arg" = "--help" ]; then
        echo "Usage: bash run_raman_pipeline_interactive.sh [material_name] [--restart]"
        echo ""
        echo "  material_name   Subdirectory inside \$RAMAN_PROJECT_DIR (default: hBN)"
        echo "  --restart       Delete all generated files and restart from scratch"
        echo ""
        echo "  Prerequisites:"
        echo "    1. Run inside an active salloc session on a GPU compute node"
        echo "    2. \$RAMAN_PROJECT_DIR, \$BINARY_UTILITIES_DIR, \$VASP_BINARY"
        echo "       must be set in ~/.bashrc"
        echo ""
        echo "  Examples:"
        echo "    salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 \\"
        echo "           --qos=interactive -A m526"
        echo "    bash raman_workflow/run_raman_pipeline_interactive.sh hBN"
        echo "    bash raman_workflow/run_raman_pipeline_interactive.sh hBN_LDA --restart"
        exit 0
    elif [ -z "$MATERIAL_NAME" ]; then
        MATERIAL_NAME="$arg"
    fi
done
MATERIAL_NAME="${MATERIAL_NAME:-$DEFAULT_MATERIAL}"

# ── Check: Running inside salloc? ──────────────────────────────────────────
if [ -z "${SLURM_JOB_ID:-}" ]; then
    echo "ERROR: Not running inside a Slurm allocation."
    echo "You must first allocate a GPU node:"
    echo "  salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526"
    echo "Then run this script from the allocated shell."
    exit 1
fi

# ── Read display label from per-material workflow_settings.yaml ──────────────
MATERIAL_LABEL="$MATERIAL_NAME"
_CONFIG_FILE="$RAMAN_PROJECT_DIR/$MATERIAL_NAME/workflow_settings.yaml"
if [ -f "$_CONFIG_FILE" ]; then
    _PARSED="$(grep -oP '^material:\s*\K.+' "$_CONFIG_FILE" 2>/dev/null || true)"
    [ -n "$_PARSED" ] && MATERIAL_LABEL="$_PARSED"
fi

echo "============================================"
echo " Raman Pipeline — Interactive Mode"
echo " Material:      ${MATERIAL_LABEL}  (${MATERIAL_NAME})"
echo " Job ID:        $SLURM_JOB_ID"
echo " Node:          $(hostname)"
echo "============================================"

# ── Source Environment ─────────────────────────────────────────────────────
echo ""
echo "[1/5] Sourcing ~/.bashrc for environment variables..."
source ~/.bashrc

# Verify required environment variables
for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR VASP_BINARY; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done

MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL_NAME"
STATUS_FILE="$MATERIAL_DIR/workflow_status.txt"

echo "  Project Dir:      $RAMAN_PROJECT_DIR"
echo "  Material Dir:     $MATERIAL_DIR"
echo "  Binary Utils:     $BINARY_UTILITIES_DIR"
echo "  VASP Binary:      $VASP_BINARY"
echo "  Status File:      $STATUS_FILE"

# Validate material directory
if [ ! -d "$MATERIAL_DIR" ]; then
    echo "ERROR: Material directory $MATERIAL_DIR does not exist."
    exit 1
fi

# ── Initialize Status File ────────────────────────────────────────────────
echo ""
echo "[2/5] Initializing status file..."
START_TIME="$(date -u +%Y-%m-%d_%H:%M:%S_UTC)"
# IMPORTANT: Only create the status file if it does not already exist.
# If it exists from a previous run, the Python resume logic reads it
# to determine which steps to skip.
if [ ! -f "$STATUS_FILE" ]; then
    cat > "$STATUS_FILE" << EOF
================================================================================
  RAMAN WORKFLOW STATUS  (Interactive Mode)
================================================================================
  Material:         $MATERIAL_LABEL  ($MATERIAL_NAME)
  Project Dir:      $RAMAN_PROJECT_DIR
  Job ID:           $SLURM_JOB_ID
  Node:             $(hostname)
  Mode:             Interactive (salloc)
  Started:          $START_TIME
  Overall Status:   INITIALIZING

  (Detailed step tracking will appear as the pipeline progresses.)
================================================================================
EOF
fi

# Trap to append final status on exit
cleanup() {
    local exit_code=$?
    local end_time="$(date -u +%Y-%m-%d_%H:%M:%S_UTC)"
    if [ $exit_code -ne 0 ]; then
        cat >> "$STATUS_FILE" << EOF

================================================================================
  ERROR — Pipeline failed with exit code $exit_code
  Time: $end_time
================================================================================
EOF
    else
        cat >> "$STATUS_FILE" << EOF

================================================================================
  PIPELINE COMPLETED SUCCESSFULLY
  Time: $end_time
================================================================================
EOF
    fi
}
trap cleanup EXIT

# ── Load Modules ───────────────────────────────────────────────────────────
echo ""
echo "[3/5] Loading Perlmutter GPU modules..."
# Use $VASP_MODULES from ~/.bashrc if set, otherwise fall back to the default list.
module load ${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}
echo "  Modules loaded."

# ── Activate Conda ─────────────────────────────────────────────────────────
echo ""
echo "[4/5] Activating phonopy conda environment..."
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env
echo "  Conda environment active."

# ── Navigate and Run ──────────────────────────────────────────────────────
echo ""
echo "[5/5] Running automation script..."
echo ""
cd "$MATERIAL_DIR" || { echo "ERROR: Cannot cd to $MATERIAL_DIR"; exit 1; }
echo "Working directory: $(pwd)"
echo ""

# Run the automation script (forward --restart if set)
python "$SCRIPT_DIR/automation_raman_analysis.py" $RESTART_FLAG

# ── Post-Run Summary ──────────────────────────────────────────────────────
echo ""
echo "============================================"
echo " Automation script finished."
echo "============================================"
echo ""
echo "Status file:  $STATUS_FILE"
echo ""
echo "To view results:"
echo "  cat $STATUS_FILE"
echo ""
echo "To exit the interactive session:"
echo "  exit"
echo ""
echo "NOTE: end_salloc.sh is NOT called automatically in interactive mode."
echo "You control when to exit with the 'exit' command above."
echo "============================================"
