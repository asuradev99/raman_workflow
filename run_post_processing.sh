#!/bin/bash
# =============================================================================
#  Post-Processing — runs after ALL parallel pipeline jobs finish
# =============================================================================
#  Aggregates final status from all materials and optionally runs comparison
#  plots. This is submitted automatically by submit_parallel_preempt.sh with
#  a dependency on all per-material jobs.
#
#  Usage (manual):
#    sbatch --export=ALL,MATERIAL_NAMES="hBN_PBEsol_3x3x1,hBN_PBEsol_4x4x1,..." \
#           run_post_processing.sh
# =============================================================================
#SBATCH -N 1
#SBATCH -C cpu
#SBATCH -q preempt
#SBATCH -t 00:15:00
#SBATCH -A m526
#SBATCH --job-name=raman_post_all
#SBATCH --output=/dev/null
#SBATCH --error=/dev/null

set -euo pipefail

source ~/.bashrc 2>/dev/null || true

echo "================================================================================"
echo "  Post-Processing — All Materials"
echo "  Job ID: $SLURM_JOB_ID"
echo "  Time:   $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "================================================================================"

# ── Parse material names ──────────────────────────────────────────────────────
IFS=',' read -ra MATERIALS <<< "${MATERIAL_NAMES:-}"

if [ ${#MATERIALS[@]} -eq 0 ]; then
    echo "  No materials specified (MATERIAL_NAMES env var is empty)."
    exit 0
fi

echo "  Materials to check: ${#MATERIALS[@]}"
echo ""

# ── Check status of each material ─────────────────────────────────────────────
COMPLETED=0
FAILED=0
for MATERIAL in "${MATERIALS[@]}"; do
    LOG_PATH="$RAMAN_PROJECT_DIR/$MATERIAL/workflow.log"

    if [ -f "$LOG_PATH" ]; then
        if grep -q "COMPLETED" "$LOG_PATH" 2>/dev/null; then
            echo "  [✓] $MATERIAL — COMPLETED"
            COMPLETED=$((COMPLETED + 1))
        elif grep -q "FAILED" "$LOG_PATH" 2>/dev/null; then
            echo "  [✗] $MATERIAL — FAILED (see $LOG_PATH)"
            FAILED=$((FAILED + 1))
        else
            echo "  [?] $MATERIAL — status unclear (check $LOG_PATH)"
        fi
    else
        echo "  [?] $MATERIAL — no workflow.log found (may still be running)"
    fi
done

echo ""
echo "================================================================================"
echo "  Final: $COMPLETED completed, $FAILED failed, $(( ${#MATERIALS[@]} - COMPLETED - FAILED )) unknown"
echo "================================================================================"
