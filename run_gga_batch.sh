#!/bin/bash
# =============================================================================
#  Run GGA Workflows — Sequential Raman Pipeline for GGA/LDA Functionals
# =============================================================================
#  Automates the full Raman workflow for available materials.
#
#  Usage (inside tmux on login node):
#    tmux new-session -s raman
#
#    # -- GPU mode (default) -------------------------------------------------
#    salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 \
#           --qos=interactive -A m526
#    bash raman_workflow/run_gga_batch.sh              # resume from last step
#    bash raman_workflow/run_gga_batch.sh --restart     # start fresh
#
#    # -- CPU mode -----------------------------------------------------------
#    salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526
#    bash raman_workflow/run_gga_batch.sh --cpu         # resume on CPU
#    bash raman_workflow/run_gga_batch.sh --cpu --restart  # fresh start on CPU
#
#    # Ctrl+B, d  to detach from tmux
#
#  The script processes materials in order, then plots results for each.
# =============================================================================

set -euo pipefail

# ── Parse arguments ────────────────────────────────────────────────────────────
RESTART_FLAG=""
CPU_FLAG=""
SCRATCH_FLAG=""
for arg in "$@"; do
    if [ "$arg" = "--restart" ] || [ "$arg" = "-r" ]; then
        RESTART_FLAG="--restart"
    elif [ "$arg" = "--cpu" ]; then
        CPU_FLAG="--cpu"
    elif [ "$arg" = "--scratch" ]; then
        SCRATCH_FLAG="--scratch"
    elif [ "$arg" = "-h" ] || [ "$arg" = "--help" ]; then
        echo "Usage: bash raman_workflow/run_gga_batch.sh [--cpu] [--scratch] [--restart|-r] [-h]"
        echo ""
        echo "  --cpu          Use CPU VASP binary and CPU node srun arguments."
        echo "                 The salloc must use -C cpu (not -C gpu)."
        echo "  --scratch      Run VASP on \$SCRATCH (fast I/O), keep config on \$HOME."
        echo "  --restart, -r  Delete all generated files and restart from scratch"
        echo "                  for each material. Without this flag, the pipeline"
        echo "                  resumes from the last completed step."
        echo "  -h, --help     Show this help message."
        echo ""
        echo "Examples:"
        echo "  salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526"
        echo "  bash raman_workflow/run_gga_batch.sh"
        echo ""
        echo "  salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526"
        echo "  bash raman_workflow/run_gga_batch.sh --cpu"
        exit 0
    fi
done

# ── Configuration ──────────────────────────────────────────────────────────────
# Edit this list to change which materials are processed.
MATERIALS=(
    "hBN_PBEsol_6x6x1"
)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SUMMARY_LOG="${SCRIPT_DIR}/gga_workflow_${TIMESTAMP}.log"

# ── Logging helper ────────────────────────────────────────────────────────────
log() {
    local msg="[$(date '+%H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$SUMMARY_LOG"
}

# ── Validate salloc ───────────────────────────────────────────────────────────
log "=== GGA Workflow ==="
log "Materials: ${MATERIALS[*]}"
[ -n "$CPU_FLAG" ] && log "Mode: CPU (--cpu)"
log ""

if [ -z "${SLURM_JOB_ID:-}" ]; then
    log "ERROR: Not running inside a Slurm allocation."
    if [ -n "$CPU_FLAG" ]; then
        log "Allocate a CPU node:"
        log "  salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526"
    else
        log "Allocate a GPU node:"
        log "  salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526"
    fi
    log "Then run this script from the allocated shell."
    exit 1
fi

log "Job ID:  $SLURM_JOB_ID"
log "Node:    $(hostname)"
if [ -z "$CPU_FLAG" ]; then
    log "GPUs:    $SLURM_GPUS_PER_NODE"
fi

# ── Source environment ─────────────────────────────────────────────────────────
log ""
log "Sourcing ~/.bashrc..."
source ~/.bashrc

for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done
if [ -n "$CPU_FLAG" ]; then
    if [ -z "${VASP_BINARY_CPU:-}" ]; then
        echo "WARNING: VASP_BINARY_CPU not set — using default CPU binary."
    fi
else
    if [ -z "${VASP_BINARY:-}" ]; then
        echo "ERROR: VASP_BINARY is not set. Add it to ~/.bashrc."
        exit 1
    fi
fi

log "  RAMAN_PROJECT_DIR:      $RAMAN_PROJECT_DIR"
log "  BINARY_UTILITIES_DIR:   $BINARY_UTILITIES_DIR"
if [ -n "$CPU_FLAG" ]; then
    log "  VASP_BINARY:            $VASP_BINARY (or CPU default if unset)"
else
    log "  VASP_BINARY:            $VASP_BINARY"
fi

# ── Load modules ──────────────────────────────────────────────────────────────
log ""
log "Loading modules..."
if [ -n "$CPU_FLAG" ]; then
    module load ${VASP_MODULES_CPU:-cpu PrgEnv-gnu cray-hdf5 cray-fftw vasp/6.4.3-cpu}
else
    module load ${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}
fi
log "  Modules loaded."

# ── Activate conda ─────────────────────────────────────────────────────────────
log ""
log "Activating phonopy conda environment..."
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env
log "  Conda environment active."

# ── Main loop: process each material ──────────────────────────────────────────
OVERALL_SUCCESS=true
MATERIAL_INDEX=1
TOTAL=${#MATERIALS[@]}

for MATERIAL in "${MATERIALS[@]}"; do
    MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL"
    SEP="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    log ""
    log "$SEP"
    log "  [${MATERIAL_INDEX}/${TOTAL}] Processing: ${MATERIAL}"
    log "$SEP"
    log "  Directory: ${MATERIAL_DIR}"

    # Validate material directory exists
    if [ ! -d "$MATERIAL_DIR" ]; then
        log "  ERROR: Material directory does not exist: ${MATERIAL_DIR}"
        log "  Skipping ${MATERIAL}."
        OVERALL_SUCCESS=false
        MATERIAL_INDEX=$((MATERIAL_INDEX + 1))
        continue
    fi

    # ── Step A: Run the Raman pipeline ───────────────────────────────────────
    EXTRA_ARGS=""
    [ -n "$RESTART_FLAG" ] && EXTRA_ARGS="$EXTRA_ARGS $RESTART_FLAG"
    [ -n "$CPU_FLAG" ] && EXTRA_ARGS="$EXTRA_ARGS $CPU_FLAG"
    [ -n "$SCRATCH_FLAG" ] && EXTRA_ARGS="$EXTRA_ARGS $SCRATCH_FLAG"
    log ""
    log "  [A] Running Raman pipeline${EXTRA_ARGS}..."

    cd "$MATERIAL_DIR" || {
        log "  ERROR: Cannot cd to ${MATERIAL_DIR}"
        OVERALL_SUCCESS=false
        MATERIAL_INDEX=$((MATERIAL_INDEX + 1))
        continue
    }

    # shellcheck disable=SC2086
    _pipeline_ok=true
    if python "$SCRIPT_DIR/automation_raman_analysis.py" $EXTRA_ARGS; then
        log "  [A] Pipeline completed successfully for ${MATERIAL}."
    else
        log "  [A] Pipeline FAILED for ${MATERIAL} (exit code $?)."
        log "  Continuing to next material..."
        OVERALL_SUCCESS=false
        _pipeline_ok=false
    fi

    # Return to script directory for next iteration / plotting
    cd "$SCRIPT_DIR" 2>/dev/null || true

    # If the pipeline failed, skip plotting and go to next material
    if [ "$_pipeline_ok" != "true" ]; then
        MATERIAL_INDEX=$((MATERIAL_INDEX + 1))
        continue
    fi

    # ── Step B: Plot results ─────────────────────────────────────────────────
    log ""
    log "  [B] Plotting results for ${MATERIAL}..."

    if bash "$SCRIPT_DIR/plot_raman_results.sh" "$MATERIAL_DIR"; then
        log "  [B] Plotting completed for ${MATERIAL}."
    else
        log "  [B] Plotting FAILED for ${MATERIAL} (exit code $?)."
        OVERALL_SUCCESS=false
    fi

    # Return to script directory for next iteration
    cd "$SCRIPT_DIR" 2>/dev/null || true
    MATERIAL_INDEX=$((MATERIAL_INDEX + 1))

    log ""
    log "  Finished ${MATERIAL}."
done

# ── Summary ────────────────────────────────────────────────────────────────────
SEP="═══════════════════════════════════════════════════════════════════════════"
log ""
log "$SEP"
log "  GGA WORKFLOW — COMPLETE"
log "$SEP"
log "  Log file: ${SUMMARY_LOG}"
log ""

for MATERIAL in "${MATERIALS[@]}"; do
    MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL"
    STATUS_FILE="${MATERIAL_DIR}/workflow_status.txt"
    OUTPUT_DIR="${MATERIAL_DIR}/output"

    if [ -f "$STATUS_FILE" ]; then
        FINAL_STATUS=$(grep -oP 'Overall Status:\s*\K.+' "$STATUS_FILE" 2>/dev/null || echo "unknown")
        log "  ${MATERIAL}: ${FINAL_STATUS}"
    else
        log "  ${MATERIAL}: No status file found"
    fi

    # List output files
    if [ -d "$OUTPUT_DIR" ]; then
        RAMAN_PLOTS=$(find "$OUTPUT_DIR/raman_spectra" -name "*.png" 2>/dev/null | wc -l)
        log "           -> ${RAMAN_PLOTS} Raman spectra plots"
    fi
done

log ""
log "$SEP"

if [ "$OVERALL_SUCCESS" = true ]; then
    log "  Overall: ALL MATERIALS COMPLETED SUCCESSFULLY"
else
    log "  Overall: Some materials had errors (see above)"
fi

log "$SEP"
log ""
log "Results per material:"
for MATERIAL in "${MATERIALS[@]}"; do
    log "  ls -la \$RAMAN_PROJECT_DIR/${MATERIAL}/output/"
done
log ""
