#!/bin/bash
# =============================================================================
#  Parallel Multi-Material Submission — Preempt Queue
# =============================================================================
#  Submits each material as an independent batch job to the preempt queue.
#  All jobs run concurrently on separate nodes — no serial blocking.
#
#  Usage (from any login node):
#    bash raman_workflow/submit_parallel_preempt.sh              # resume all
#    bash raman_workflow/submit_parallel_preempt.sh --restart     # fresh start
#    bash raman_workflow/submit_parallel_preempt.sh --no-scratch  # run on HOME
#
#  Monitor after submission:
#    squeue -u $USER
#    bash raman_workflow/show_status.sh
#
#  Per-material status (see summary at end of this script's output):
#    tail -80 $RAMAN_PROJECT_DIR/<material>/workflow.log         # HOME
#    tail -80 $SCRATCH/vasp_calculations/<material>/workflow.log  # --scratch
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SBATCH_SCRIPT="$SCRIPT_DIR/run_raman_pipeline.sbatch"

# ═══════════════════════════════════════════════════════════════════════════════
#  MATERIAL CONFIGURATION — edit this section to add/remove materials
# ═══════════════════════════════════════════════════════════════════════════════
#
#  Format: "name:mode:time"
#    name  — material directory under $RAMAN_PROJECT_DIR
#    mode  — "gpu" or "cpu" (cpu required for 6×6×1 to avoid GPU OOM on LOPTICS)
#    time  — wall-time estimate (used as --time-min for preempt backfill)
#
#  Time estimates (per material, 4 GPUs, --scratch):
#    3×3×1:  ~2 h
#    4×4×1:  ~3 h
#    5×5×1:  ~5 h
#    6×6×1:  ~6 h  (large supercell; may OOM on LOPTICS — monitor Step 14)
# ═══════════════════════════════════════════════════════════════════════════════

MATERIAL_CONFIGS=(
    "hBN_PBEsol_3x3x1:gpu:02:00:00"
    "hBN_PBEsol_4x4x1:gpu:03:00:00"
    "hBN_PBEsol_5x5x1:gpu:05:00:00"
    "hBN_PBEsol_6x6x1:gpu:06:00:00"
)

# ── Parse flags ───────────────────────────────────────────────────────────────
RESTART_FLAG=""
SCRATCH_FLAG="--scratch"    # default: use $SCRATCH for VASP I/O

for arg in "$@"; do
    case "$arg" in
        --restart|-r)  RESTART_FLAG="--restart" ;;
        --no-scratch)  SCRATCH_FLAG="" ;;
        -h|--help)
            echo "Usage: bash submit_parallel_preempt.sh [--restart] [--no-scratch]"
            echo ""
            echo "  --restart     Delete all generated files, restart each material from scratch"
            echo "  --no-scratch  Run VASP on HOME instead of \$SCRATCH"
            echo ""
            echo "Materials are configured at the top of this script (MATERIAL_CONFIGS array)."
            exit 0
            ;;
    esac
done

# ── Validate environment ──────────────────────────────────────────────────────
source ~/.bashrc 2>/dev/null || true

for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR VASP_BINARY; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done

if [ ! -f "$SBATCH_SCRIPT" ]; then
    echo "ERROR: sbatch script not found: $SBATCH_SCRIPT"
    exit 1
fi

# ── Count materials ───────────────────────────────────────────────────────────
N_MATERIALS=${#MATERIAL_CONFIGS[@]}
echo "================================================================================"
echo "  Parallel Preempt Submission"
echo "  Queue:     preempt"
echo "  Materials: $N_MATERIALS"
echo "  Scratch:   ${SCRATCH_FLAG:---scratch (default)}"
echo "  Restart:   ${RESTART_FLAG:-(resume from last step)}"
echo "================================================================================"
echo ""

# ── Submit one independent job per material ───────────────────────────────────
SUBMITTED_JOBS=()
FAILED_MATERIALS=()
ALL_JOB_IDS=()

for CONFIG in "${MATERIAL_CONFIGS[@]}"; do
    IFS=':' read -r MATERIAL MODE TIME_MIN <<< "$CONFIG"
    MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL"

    if [ ! -d "$MATERIAL_DIR" ]; then
        echo "  [SKIP]   $MATERIAL — directory not found: $MATERIAL_DIR"
        FAILED_MATERIALS+=("$MATERIAL")
        continue
    fi

    # Check input files exist
    if [ ! -f "$MATERIAL_DIR/input/POSCAR" ] || [ ! -f "$MATERIAL_DIR/input/POTCAR" ]; then
        echo "  [SKIP]   $MATERIAL — missing input/POSCAR or input/POTCAR"
        FAILED_MATERIALS+=("$MATERIAL")
        continue
    fi

    # ── Build sbatch arguments ────────────────────────────────────────────
    JOB_NAME="raman_${MATERIAL}"
    SBATCH_ARGS=(
        "--job-name=$JOB_NAME"
        "--export=ALL,MATERIAL_NAME=$MATERIAL"
        "--time-min=$TIME_MIN"
        "--output=$MATERIAL_DIR/slurm_%j.out"
        "--error=$MATERIAL_DIR/slurm_%j.err"
    )

    # Build pipeline flags
    PIPELINE_ARGS="$RESTART_FLAG"
    [ -n "$SCRATCH_FLAG" ] || PIPELINE_ARGS="$PIPELINE_ARGS --no-scratch"

    # ── Submit ────────────────────────────────────────────────────────────
    # shellcheck disable=SC2086
    JOB_OUTPUT=$(sbatch "${SBATCH_ARGS[@]}" "$SBATCH_SCRIPT" $PIPELINE_ARGS 2>&1)

    if [[ "$JOB_OUTPUT" =~ Submitted\ batch\ job\ ([0-9]+) ]]; then
        JOB_ID="${BASH_REMATCH[1]}"
        SUBMITTED_JOBS+=("${MATERIAL}:${JOB_ID}:${MODE}")
        ALL_JOB_IDS+=("$JOB_ID")
        printf "  [OK]  %-30s  mode=%-3s  job=%s  time-min=%s\n" \
               "$MATERIAL" "$MODE" "$JOB_ID" "$TIME_MIN"
    else
        printf "  [FAIL]  %-28s  %s\n" "$MATERIAL" "$JOB_OUTPUT"
        FAILED_MATERIALS+=("$MATERIAL")
    fi
done

# ── Submit post-processing job (runs after ALL materials finish) ──────────────
if [ ${#ALL_JOB_IDS[@]} -gt 0 ]; then
    # Build dependency string: afterok:JOB1:JOB2:...
    DEP_STR=$(IFS=:; echo "${ALL_JOB_IDS[*]}")
    # Build comma-separated material names (strip :mode:time suffixes)
    MAT_NAMES=""
    for CONFIG in "${MATERIAL_CONFIGS[@]}"; do
        MAT_NAME="${CONFIG%%:*}"
        [ -z "$MAT_NAMES" ] && MAT_NAMES="$MAT_NAME" || MAT_NAMES="${MAT_NAMES},${MAT_NAME}"
    done

    echo ""
    echo "  Submitting post-processing job (runs after all materials complete)..."

    POST_OUTPUT=$(sbatch \
        --job-name="raman_post_all" \
        --dependency="afterok:$DEP_STR" \
        --export=ALL,MATERIAL_NAMES="$MAT_NAMES" \
        --output=/dev/null \
        --error=/dev/null \
        --time=00:15:00 \
        --ntasks=1 \
        --cpus-per-task=2 \
        --constraint=cpu \
        "$SCRIPT_DIR/run_post_processing.sh" 2>&1) || true

    if [[ "$POST_OUTPUT" =~ Submitted\ batch\ job\ ([0-9]+) ]]; then
        POST_JOB_ID="${BASH_REMATCH[1]}"
        echo "  [OK]  Post-processing job: $POST_JOB_ID (after all $N_MATERIALS materials)"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "================================================================================"
echo "  Summary: ${#SUBMITTED_JOBS[@]} submitted, ${#FAILED_MATERIALS[@]} failed"
echo "================================================================================"

if [ ${#SUBMITTED_JOBS[@]} -gt 0 ]; then
    echo ""
    echo "  Monitor queue:"
    echo "    squeue -u \$USER"
    echo "    bash $SCRIPT_DIR/show_status.sh"
    echo ""
    echo "  Per-material progress:"
    for entry in "${SUBMITTED_JOBS[@]}"; do
        IFS=':' read -r MATERIAL JID MODE <<< "$entry"
        LOG_PATH="$RAMAN_PROJECT_DIR/$MATERIAL/workflow.log"
        printf "    [%s] %-30s  tail -80 %s\n" "$JID" "$MATERIAL ($MODE)" "$LOG_PATH"
    done
fi

if [ ${#FAILED_MATERIALS[@]} -gt 0 ]; then
    echo ""
    echo "  Failed to submit:"
    for m in "${FAILED_MATERIALS[@]}"; do
        echo "    - $m"
    done
fi
echo "================================================================================"
