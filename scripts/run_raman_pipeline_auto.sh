#!/bin/bash
# =============================================================================
#  run_raman_pipeline_auto.sh — Run the Raman pipeline autonomously (login node)
# =============================================================================
#  Usage:
#    bash raman_workflow/scripts/run_raman_pipeline_auto.sh <material> [flags]
#
#  Runs entirely from the login node — provisions compute via salloc/sbatch
#  automatically when compute_mode is set to "sbatch" in the config.
#  Falls back to requiring a manual salloc when compute_mode is "srun".
#
#  Flags:
#    --restart    Delete all generated files, restart from scratch
#    --no-scratch Run VASP on HOME instead of $SCRATCH
#
#  Examples:
#    bash raman_workflow/scripts/run_raman_pipeline_auto.sh hBN_PBEsol_6x6x1_defect
#    nohup bash raman_workflow/scripts/run_raman_pipeline_auto.sh hBN_PBEsol_6x6x1_defect &> pipeline.out &
# =============================================================================

set -euo pipefail

trap 'echo "=== Interrupted — stopping retry loop ==="; exit 1' INT TERM

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAMAN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MATERIAL_NAME="${1:-$(basename "$PWD")}"
if [ "$MATERIAL_NAME" != "$(basename "$PWD")" ]; then
    shift  # only shift if an explicit material name was given
else
    set --  # no args to shift
fi

RESTART_FLAG=""
SCRATCH_FLAG="--scratch"

for arg in "$@"; do
    case "$arg" in
        --restart)    RESTART_FLAG="--restart" ;;
        --no-scratch) SCRATCH_FLAG="" ;;
        -h|--help)
            echo "Usage: bash run_raman_pipeline_auto.sh <material> [--restart] [--no-scratch]"
            echo ""
            echo "  Runs the full Raman pipeline from the login node."
            echo "  With compute_mode='sbatch' in the config, provisions compute automatically."
            exit 0
            ;;
        *) echo "Unknown arg: $arg"; exit 1 ;;
    esac
done

# ── Validate environment ─────────────────────────────────────────────────────
source ~/.bashrc 2>/dev/null || true
PYTHON="/global/common/software/m526/phonopy_env/bin/python3"
if [ -z "${RAMAN_PROJECT_DIR:-}" ]; then
    echo "ERROR: RAMAN_PROJECT_DIR not set"
    exit 1
fi

MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL_NAME"
if [ ! -d "$MATERIAL_DIR" ]; then
    echo "ERROR: Material directory not found: $MATERIAL_DIR"
    exit 1
fi

# ── Run (auto-retry on salloc timeout) ──────────────────────────────────────
cd "$MATERIAL_DIR"
export PYTHONPATH="$RAMAN_DIR:$PYTHONPATH"

ATTEMPT=0
while true; do
    ATTEMPT=$((ATTEMPT + 1))
    echo "=== Pipeline attempt $ATTEMPT ==="

    set +e
    "$PYTHON" "$RAMAN_DIR/src/provision.py" $SCRATCH_FLAG $RESTART_FLAG
    EXIT_CODE=$?
    set -e

    if [ $EXIT_CODE -eq 0 ]; then
        echo "=== Pipeline COMPLETE ==="
        exit 0
    fi

    # Exit code 42 = preempted/timed out — retry after short wait
    # Exit code 43 = salloc allocation limit hit — wait longer before retry
    # Any other non-zero exit code = fatal error
    if [ $EXIT_CODE -eq 42 ]; then
        echo "=== Preempted/timed out — retrying in 5m (attempt $ATTEMPT) ==="
        RESTART_FLAG=""
        sleep 300
        continue
    fi

    if [ $EXIT_CODE -eq 43 ]; then
        echo "=== Allocation limit hit — retrying in 10m (attempt $ATTEMPT) ==="
        RESTART_FLAG=""
        sleep 600
        continue
    fi

    echo "=== Pipeline FAILED (exit $EXIT_CODE) — stopping ==="
    exit $EXIT_CODE
done
