#!/bin/bash
# =============================================================================
#  Raman Pipeline — Interactive Session Runner (tmux-friendly)
# =============================================================================
#  ╔═══════════════════════════════════════════════════════════════════════════╗
#  ║  RECOMMENDED: Run inside `tmux` to survive SSH disconnects               ║
#  ║                                                                          ║
#  ║  GPU mode (default):                                                     ║
#  ║    tmux new-session -s raman                                            ║
#  ║    salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 \                  ║
#  ║           --qos=interactive -A m526                                      ║
#  ║    bash raman_workflow/run_raman_pipeline_interactive.sh hBN_LDA         ║
#  ║                                                                          ║
#  ║  CPU mode (--cpu):                                                       ║
#  ║    tmux new-session -s raman                                            ║
#  ║    salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526             ║
#  ║    bash raman_workflow/run_raman_pipeline_interactive.sh \              ║
#  ║      hBN_PBEsol_CPU --cpu                                                ║
#  ║                                                                          ║
#  ║    # Ctrl+B, d  to detach from tmux                                      ║
#  ║    # Reconnect: tmux attach -t raman                                     ║
#  ╚═══════════════════════════════════════════════════════════════════════════╝
#
#  Usage:
#    1. (Recommended) Start a tmux session:  tmux new-session -s raman
#    2. Allocate a compute node:
#         GPU:  salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526
#         CPU:  salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526
#    3. Once granted, run:
#         GPU:  bash run_raman_pipeline_interactive.sh <material>
#         CPU:  bash run_raman_pipeline_interactive.sh <material> --cpu
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
CPU_FLAG=""
SCRATCH_FLAG=""
MATERIAL_NAME=""

for arg in "$@"; do
    if [ "$arg" = "--restart" ]; then
        RESTART_FLAG="--restart"
    elif [ "$arg" = "--cpu" ]; then
        CPU_FLAG="--cpu"
    elif [ "$arg" = "--scratch" ]; then
        SCRATCH_FLAG="--scratch"
    elif [ "$arg" = "-h" ] || [ "$arg" = "--help" ]; then
        echo "Usage: bash run_raman_pipeline_interactive.sh [material_name] [--cpu] [--restart] [--scratch]"
        echo ""
        echo "  material_name   Subdirectory inside \$RAMAN_PROJECT_DIR (default: hBN)"
        echo "  --cpu           Use CPU VASP binary and CPU node srun arguments."
        echo "                  Requires: salloc -N 1 -C cpu (not -C gpu)"
        echo "  --restart       Delete all generated files and restart from scratch"
        echo "  --scratch       Run VASP on \$SCRATCH (fast I/O), keep config on \$HOME."
        echo "                  Results copied back to HOME on completion."
        echo ""
        echo "  Prerequisites:"
        echo "    1. Run inside an active salloc session on a compute node"
        echo "    2. \$RAMAN_PROJECT_DIR, \$BINARY_UTILITIES_DIR, \$VASP_BINARY"
        echo "       must be set in ~/.bashrc"
        echo ""
        echo "  Examples:"
        echo "    # GPU material (default):"
        echo "    salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 \\"
        echo "           --qos=interactive -A m526"
        echo "    bash raman_workflow/run_raman_pipeline_interactive.sh hBN_LDA"
        echo ""
        echo "    # GPU material with scratch (for large supercells):"
        echo "    salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 \\"
        echo "           --qos=interactive -A m526"
        echo "    bash raman_workflow/run_raman_pipeline_interactive.sh \\"
        echo "      hBN_PBEsol_6x6x1 --scratch"
        echo ""
        echo "    # CPU material:"
        echo "    salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526"
        echo "    bash raman_workflow/run_raman_pipeline_interactive.sh \\"
        echo "      hBN_PBEsol_CPU --cpu"
        echo ""
        echo "    # Combined flags:"
        echo "    bash raman_workflow/run_raman_pipeline_interactive.sh \\"
        echo "      hBN_LDA --restart"
        echo "    bash raman_workflow/run_raman_pipeline_interactive.sh \\"
        echo "      hBN_LDA --scratch --restart"
        exit 0
    elif [ -z "$MATERIAL_NAME" ]; then
        MATERIAL_NAME="$arg"
    fi
done
MATERIAL_NAME="${MATERIAL_NAME:-$DEFAULT_MATERIAL}"

# ── Check: Running inside salloc? ──────────────────────────────────────────
if [ -z "${SLURM_JOB_ID:-}" ]; then
    echo "ERROR: Not running inside a Slurm allocation."
    if [ -n "$CPU_FLAG" ]; then
        echo "Allocate a CPU node:"
        echo "  salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526"
    else
        echo "Allocate a GPU node:"
        echo "  salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526"
    fi
    echo "Then run this script from the allocated shell."
    exit 1
fi

# ── Read display label from per-material or shared workflow_settings.yaml ────
# Priority: per-material file, then shared file, then directory name.
MATERIAL_LABEL="$MATERIAL_NAME"
_SHARED_CONFIG_FILE="$RAMAN_PROJECT_DIR/shared_workflow_settings.yaml"
_CONFIG_FILE="$RAMAN_PROJECT_DIR/$MATERIAL_NAME/workflow_settings.yaml"
for _cfg in "$_CONFIG_FILE" "$_SHARED_CONFIG_FILE"; do
    if [ -f "$_cfg" ]; then
        _PARSED="$(grep -oP '^material:\s*\K.+' "$_cfg" 2>/dev/null || true)"
        if [ -n "$_PARSED" ]; then
            MATERIAL_LABEL="$_PARSED"
            break
        fi
    fi
done

MODE_STR="GPU"
[ -n "$CPU_FLAG" ] && MODE_STR="CPU"

echo "============================================"
echo " Raman Pipeline — Interactive Mode (${MODE_STR})"
echo " Material:      ${MATERIAL_LABEL}  (${MATERIAL_NAME})"
echo " Job ID:        $SLURM_JOB_ID"
echo " Node:          $(hostname)"
echo "============================================"

# ── Source Environment ─────────────────────────────────────────────────────
echo ""
echo "[1/5] Sourcing ~/.bashrc for environment variables..."
source ~/.bashrc

# Verify required environment variables
for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done
if [ -n "$CPU_FLAG" ]; then
    # CPU mode: VASP_BINARY_CPU must be set (or use default)
    if [ -z "${VASP_BINARY_CPU:-}" ]; then
        echo "WARNING: VASP_BINARY_CPU not set — using default CPU binary."
    fi
else
    # GPU mode: VASP_BINARY must be set
    if [ -z "${VASP_BINARY:-}" ]; then
        echo "ERROR: VASP_BINARY is not set. Add it to ~/.bashrc or use --cpu."
        exit 1
    fi
fi

MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL_NAME"
STATUS_FILE="$MATERIAL_DIR/workflow_status.txt"

echo "  Project Dir:      $RAMAN_PROJECT_DIR"
echo "  Material Dir:     $MATERIAL_DIR"
echo "  Binary Utils:     $BINARY_UTILITIES_DIR"
echo "  VASP Binary:      $VASP_BINARY"
echo "  Mode:             ${MODE_STR}"
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
  RAMAN WORKFLOW STATUS  (Interactive Mode, ${MODE_STR})
================================================================================
  Material:         $MATERIAL_LABEL  ($MATERIAL_NAME)
  Project Dir:      $RAMAN_PROJECT_DIR
  Job ID:           $SLURM_JOB_ID
  Node:             $(hostname)
  Mode:             Interactive (salloc, ${MODE_STR})
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
if [ -n "$CPU_FLAG" ]; then
    echo "[3/5] Loading Perlmutter CPU modules..."
    module load ${VASP_MODULES_CPU:-cpu PrgEnv-gnu cray-hdf5 cray-fftw vasp/6.4.3-cpu}
else
    echo "[3/5] Loading Perlmutter GPU modules..."
    module load ${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}
fi
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

# Run the automation script (forward --restart, --cpu, and --scratch if set)
python "$SCRIPT_DIR/automation_raman_analysis.py" $RESTART_FLAG $CPU_FLAG $SCRATCH_FLAG

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
