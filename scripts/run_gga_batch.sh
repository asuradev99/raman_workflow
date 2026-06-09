#!/bin/bash
# =============================================================================
#  Serial Multi-Material Runner — Interactive Mode
# =============================================================================
#  Processes materials one at a time inside a single salloc allocation, then
#  generates plots for each. Useful for running multiple materials in one
#  interactive session without re-allocating.
#
#  Usage (inside tmux):
#    salloc -N 1 -C gpu --gpus-per-node=4 -t 08:00:00 --qos=interactive -A m526
#    bash raman_workflow/run_gga_batch.sh              # resume all
#    bash raman_workflow/run_gga_batch.sh --restart     # fresh start
#    bash raman_workflow/run_gga_batch.sh --no-scratch  # run on HOME
#
#  Status: each material writes its own workflow.log on HOME.
# =============================================================================

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
# Edit this list to change which materials are processed (in order).
MATERIALS=(
    "hBN_PBEsol_6x6x1"
    "hBN_PBEsol_5x5x1"
    "hBN_PBEsol_4x4x1"
    "hBN_PBEsol_3x3x1"
)

# ── Parse arguments ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESTART_FLAG=""
SCRATCH_FLAG="--scratch"

for arg in "$@"; do
    case "$arg" in
        --restart|-r)  RESTART_FLAG="--restart" ;;
        --no-scratch)  SCRATCH_FLAG="" ;;
        -h|--help)
            echo "Usage: bash run_gga_batch.sh [--restart] [--no-scratch]"
            echo ""
            echo "  Processes ${#MATERIALS[@]} materials in sequence, then plots each."
            echo "  Materials: ${MATERIALS[*]}"
            echo ""
            echo "  --restart     Delete all generated files, restart each from scratch"
            echo "  --no-scratch  Run VASP on HOME instead of \$SCRATCH"
            echo ""
            echo "  Prerequisites:"
            echo "    salloc -N 1 -C gpu --gpus-per-node=4 -t 08:00:00 --qos=interactive -A m526"
            echo "    bash raman_workflow/run_gga_batch.sh"
            exit 0
            ;;
    esac
done

# ── Validate salloc ─────────────────────────────────────────────────────────
if [ -z "${SLURM_JOB_ID:-}" ]; then
    echo "ERROR: Not running inside a Slurm allocation."
    echo "Allocate a GPU node first:"
    echo "  salloc -N 1 -C gpu --gpus-per-node=4 -t 08:00:00 --qos=interactive -A m526"
    exit 1
fi

# ── Source environment ──────────────────────────────────────────────────────
source ~/.bashrc 2>/dev/null || true

for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR VASP_BINARY; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done

echo "════════════════════════════════════════════════════════════"
echo "  Serial Multi-Material Runner"
echo "  Job ID:       $SLURM_JOB_ID"
echo "  Node:         $(hostname)"
echo "  Materials:    ${#MATERIALS[@]} (${MATERIALS[*]})"
echo "  Scratch:      ${SCRATCH_FLAG:---scratch}"
echo "════════════════════════════════════════════════════════════"

# ── Preflight: verify binaries ──────────────────────────────────────────────
echo ""
echo "Checking binaries..."
for bin in ramdiscar genRApos610 runRA raman_tensor broadening; do
    if [ ! -x "${BINARY_UTILITIES_DIR}/${bin}" ]; then
        echo "ERROR: ${bin} not executable at ${BINARY_UTILITIES_DIR}/${bin}"
        exit 1
    fi
done
echo "  All binaries found."

# ── Load modules ────────────────────────────────────────────────────────────
echo ""
echo "Loading GPU modules..."
module load ${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}
echo "  Modules loaded."

# ── Activate conda ──────────────────────────────────────────────────────────
echo ""
echo "Activating phonopy conda environment..."
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env
echo "  Conda environment active."

# ── Main loop ───────────────────────────────────────────────────────────────
OVERALL_SUCCESS=true
TOTAL=${#MATERIALS[@]}

for ((i=0; i<TOTAL; i++)); do
    MATERIAL="${MATERIALS[$i]}"
    MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL"
    N=$((i + 1))

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  [$N/$TOTAL] $MATERIAL"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ ! -d "$MATERIAL_DIR" ]; then
        echo "  SKIP — directory not found: $MATERIAL_DIR"
        OVERALL_SUCCESS=false
        continue
    fi

    # ── Run pipeline ────────────────────────────────────────────────────
    PIPELINE_ARGS="$RESTART_FLAG"
    [ -n "$SCRATCH_FLAG" ] && PIPELINE_ARGS="$PIPELINE_ARGS $SCRATCH_FLAG"

    echo "  Running pipeline..."
    cd "$MATERIAL_DIR" || { echo "  ERROR: Cannot cd to $MATERIAL_DIR"; OVERALL_SUCCESS=false; continue; }

    PIPELINE_OK=true
    if python "$SCRIPT_DIR/automation_raman_analysis.py" $PIPELINE_ARGS; then
        echo "  ✓ Pipeline completed"
    else
        echo "  ✗ Pipeline FAILED (exit code $?)"
        echo "  Check: tail -80 $MATERIAL_DIR/workflow.log"
        OVERALL_SUCCESS=false
        PIPELINE_OK=false
    fi

    cd "$SCRIPT_DIR" 2>/dev/null || true

    # ── Plot results ─────────────────────────────────────────────────────
    if [ "$PIPELINE_OK" = "true" ]; then
        echo ""
        echo "  Generating plots..."
        if bash "$SCRIPT_DIR/plot_raman_results.sh" "$MATERIAL_DIR"; then
            echo "  ✓ Plots generated → $MATERIAL_DIR/output/raman_spectra/"
        else
            echo "  ✗ Plotting FAILED"
            OVERALL_SUCCESS=false
        fi
    fi

    echo ""
    echo "  Done: $MATERIAL ($N/$TOTAL)"
done

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
if [ "$OVERALL_SUCCESS" = "true" ]; then
    echo "  ALL MATERIALS COMPLETE"
else
    echo "  FINISHED WITH ERRORS — check workflow.log files"
fi
echo "════════════════════════════════════════════════════════════"
echo ""

for MATERIAL in "${MATERIALS[@]}"; do
    STATUS_FILE="$RAMAN_PROJECT_DIR/$MATERIAL/workflow.log"
    if [ -f "$STATUS_FILE" ]; then
        STATUS=$(grep -oP 'Status\s+\K(RUNNING|COMPLETED|FAILED)' "$STATUS_FILE" | tail -1 || echo "?")
        echo "  $STATUS  $MATERIAL"
    else
        echo "  ?  $MATERIAL (no workflow.log)"
    fi
done
echo ""
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
