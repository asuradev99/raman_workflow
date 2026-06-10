#!/bin/bash
# =============================================================================
#  Autonomous Sequential Job Queue — run_queue.sh
# =============================================================================
#  Submits materials as sequential sbatch jobs with dependencies — each job
#  starts only after the previous one finishes.  The script exits immediately
#  after submission; Slurm handles the queuing.  Your terminal stays free.
#
#  Usage (from login node):
#    bash raman_workflow/run_queue.sh                    # resume all
#    bash raman_workflow/run_queue.sh --restart           # fresh start
#    bash raman_workflow/run_queue.sh --no-scratch        # run on HOME
#    bash raman_workflow/run_queue.sh --cpu               # CPU mode
#
#  Monitor:
#    squeue -u $USER                                      # job queue
#    bash raman_workflow/show_status.sh <material>/workflow.log
#
#  Configuration:
#    Edit QUEUE_MATERIALS array below to set materials and walltimes.
# =============================================================================

set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════════
#  QUEUE CONFIGURATION — edit this section
# ═══════════════════════════════════════════════════════════════════════════════
#
#  Format: "name|walltime|qos|nodes"
#    name      — material directory under $RAMAN_PROJECT_DIR
#    walltime  — max time to request for this material (HH:MM:SS)
#    qos       — Slurm QoS: "regular" (up to 48h), "preempt" (up to 2d,
#                 can be preempted), or "interactive" (max 4h, fast queue)
#    nodes     — number of nodes (1 = 4 GPUs, 4 = 16 GPUs for hf_parallel)
#
#  "interactive" QoS gives fastest queue access (usually granted in seconds)
#  but is capped at 4h. Longer materials will time out and resume on next run.
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
#  PARSE ARGUMENTS
# ═══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAMAN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"  # raman_workflow/ root
RESTART_FLAG=""
SCRATCH_FLAG="--scratch"
CPU_FLAG=""
SKIP_COMPLETED=true   # skip materials whose workflow.log shows COMPLETED
RETRY_FAILED=false     # only meaningful with --retry-failed

# ── Load queue materials ──────────────────────────────────────────────────
# Edit queue_materials.conf to change what runs without touching this script.
QUEUE_CONF="${RAMAN_DIR}/queue_materials.conf"
if [ -f "$QUEUE_CONF" ]; then
    source "$QUEUE_CONF"
else
    echo "ERROR: Queue config not found: $QUEUE_CONF"
    exit 1
fi

show_help() {
    echo "Usage: bash run_queue.sh [options]"
    echo ""
    echo "  Autonomous sequential job queue for Raman pipeline materials."
    echo "  Each material gets its own salloc allocation — no manual salloc needed."
    echo ""
    echo "  Options:"
    echo "    --restart       Delete generated files, restart each material fresh"
    echo "    --no-scratch    Run VASP on HOME instead of \$SCRATCH (slower I/O)"
    echo "    --cpu           Use CPU VASP binary instead of GPU"
    echo "    --retry-failed  Re-run materials that previously FAILED"
    echo "    --retry-all     Re-run ALL materials (ignore workflow.log status)"
    echo "    -h, --help      Show this help"
    echo ""
    echo "  Detached usage:"
    echo "    nohup bash raman_workflow/run_queue.sh &> queue_\$(date +%Y%m%d_%H%M).log &"
    echo "    tmux new-session -s queue 'bash raman_workflow/run_queue.sh'"
    echo ""
    echo "  Queue (${#QUEUE_MATERIALS[@]} materials):"
    for entry in "${QUEUE_MATERIALS[@]}"; do
        IFS='|' read -r name wt qos nodes <<< "$entry"
        printf "    %-30s  %s  %s  %s node(s)\n" "$name" "$wt" "$qos" "${nodes:-1}"
    done
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --restart)          RESTART_FLAG="--restart" ;;
        --no-scratch)       SCRATCH_FLAG="" ;;
        --cpu)              CPU_FLAG="--cpu" ;;
        --retry-failed)     SKIP_COMPLETED=true; RETRY_FAILED=true ;;
        --retry-all)        SKIP_COMPLETED=false ;;
        -h|--help)          show_help ;;
        *)
            echo "ERROR: Unknown argument: $arg"
            echo "Use --help for usage."
            exit 1
            ;;
    esac
done

# ═══════════════════════════════════════════════════════════════════════════════
#  ENVIRONMENT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
#
#  Perlmutter's .bashrc has "[ -z \"$PS1\" ] && return" which skips
#  everything in non-interactive shells.  We try the parent environment
#  first (vars are already set if run from an interactive terminal), then
#  fall back to forcing an interactive bash to extract them from .bashrc.

_load_env() {
    # Already set?  Use the parent environment.
    if [ -n "${RAMAN_PROJECT_DIR:-}" ] && [ -n "${BINARY_UTILITIES_DIR:-}" ] && [ -n "${VASP_BINARY:-}" ]; then
        return 0
    fi
    # Try non-interactive sourcing (works if .bashrc has no PS1 guard)
    source ~/.bashrc 2>/dev/null || true
    if [ -n "${RAMAN_PROJECT_DIR:-}" ] && [ -n "${BINARY_UTILITIES_DIR:-}" ] && [ -n "${VASP_BINARY:-}" ]; then
        return 0
    fi
    # Last resort: force interactive bash to extract vars from .bashrc
    echo "  [env] Extracting variables from interactive .bashrc..."
    eval "$(bash -i -c 'declare -p RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR VASP_BINARY VASP_BINARY_CPU VASP_MODULES 2>/dev/null' 2>/dev/null)" 2>/dev/null || true
}

_load_env

for var in RAMAN_PROJECT_DIR BINARY_UTILITIES_DIR VASP_BINARY; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set. Add it to ~/.bashrc"
        exit 1
    fi
done

# CPU mode uses a different binary
VASP_BIN="$VASP_BINARY"
if [ -n "$CPU_FLAG" ]; then
    VASP_BIN="${VASP_BINARY_CPU:-$VASP_BINARY}"
fi

# ═══════════════════════════════════════════════════════════════════════════════
#  QUEUE LOOP
# ═══════════════════════════════════════════════════════════════════════════════

QUEUE_TOTAL=${#QUEUE_MATERIALS[@]}
QUEUE_COMPLETED=0
QUEUE_FAILED=0
QUEUE_SKIPPED=0
OVERALL_START=$(date +%s)

# Build the pipeline flags string
PIPELINE_FLAGS="$RESTART_FLAG $SCRATCH_FLAG $CPU_FLAG"
# Trim leading/trailing whitespace
PIPELINE_FLAGS="$(echo "$PIPELINE_FLAGS" | xargs)"

echo ""
echo "╔══════════════════════════════════════════════════════════════════════════╗"
echo "║  AUTONOMOUS RAMAN QUEUE                                                 ║"
echo "╠══════════════════════════════════════════════════════════════════════════╣"
printf "║  Started:     %-55s ║\n" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
printf "║  Host:        %-55s ║\n" "$(hostname)"
printf "║  Queue size:  %-55s ║\n" "${QUEUE_TOTAL} materials"
printf "║  Scratch:     %-55s ║\n" "${SCRATCH_FLAG:---scratch (on)}"
printf "║  CPU mode:    %-55s ║\n" "${CPU_FLAG:+on (--cpu)}${CPU_FLAG:-off (GPU)}"
printf "║  Restart:     %-55s ║\n" "${RESTART_FLAG:+yes}${RESTART_FLAG:-no}"
printf "║  Skip done:   %-55s ║\n" "$([ "$SKIP_COMPLETED" = "true" ] && echo yes || echo no)"
echo "╚══════════════════════════════════════════════════════════════════════════╝"

QUEUE_IDX=0
for entry in "${QUEUE_MATERIALS[@]}"; do
    QUEUE_IDX=$((QUEUE_IDX + 1))

    # ── Parse entry ────────────────────────────────────────────────────────
    IFS='|' read -r MATERIAL_NAME WALLTIME QOS NODES <<< "$entry"
    NODES="${NODES:-1}"
    GPUS=$((NODES * 4))

    MATERIAL_DIR="$RAMAN_PROJECT_DIR/$MATERIAL_NAME"
    LOG_FILE="$MATERIAL_DIR/workflow.log"

    # ── Pre-check: directory exists? ───────────────────────────────────────
    if [ ! -d "$MATERIAL_DIR" ]; then
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  [$QUEUE_IDX/$QUEUE_TOTAL] $MATERIAL_NAME  —  SKIP (no directory)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        QUEUE_SKIPPED=$((QUEUE_SKIPPED + 1))
        continue
    fi

    # ── Skip already-completed materials? ──────────────────────────────────
    if [ "$SKIP_COMPLETED" = "true" ] && [ -f "$LOG_FILE" ]; then
        if grep -q "Status.*COMPLETED" "$LOG_FILE" 2>/dev/null; then
            echo ""
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "  [$QUEUE_IDX/$QUEUE_TOTAL] $MATERIAL_NAME  —  SKIP (already COMPLETED)"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            QUEUE_SKIPPED=$((QUEUE_SKIPPED + 1))
            continue
        fi
    fi

    # ── Print material header ─────────────────────────────────────────────
    MATERIAL_START=$(date +%s)
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════════╗"
    printf "║  [%d/%d] %-62s ║\n" "$QUEUE_IDX" "$QUEUE_TOTAL" "$MATERIAL_NAME"
    echo "╠══════════════════════════════════════════════════════════════════════════╣"
    printf "║  Walltime:  %-62s ║\n" "$WALLTIME"
    printf "║  QoS:       %-62s ║\n" "$QOS"
    printf "║  Nodes:     %-62s ║\n" "$NODES ($GPUS GPUs)"
    printf "║  Queue pos: %-62s ║\n" "$QUEUE_IDX / $QUEUE_TOTAL"
    echo "╚══════════════════════════════════════════════════════════════════════════╝"

    # ── Build salloc + pipeline command ────────────────────────────────────
    #
    # The pipeline command runs INSIDE salloc → srun.  salloc blocks until
    # the allocation is granted AND the inner command (srun) completes.
    # This gives us automatic monitoring — no polling needed.
    #
    # The inner script:
    #   1. sources bashrc for env vars
    #   2. loads GPU modules
    #   3. activates phonopy conda env
    #   4. cds to the material directory
    #   5. runs automation_raman_analysis.py
    #
    # All stdout/stderr from the pipeline is redirected to a per-material
    # log so the terminal stays free.  Monitor with:
    #   tail -f $MATERIAL_DIR/salloc_output.log

    SRUN_LOG="$MATERIAL_DIR/salloc_output.log"
    echo ""
    echo "  Requesting salloc ($NODES node(s), $WALLTIME, $QOS) — waiting for allocation..."
    echo "  Log:  $SRUN_LOG"
    echo "  Status: $LOG_FILE"
    echo ""

    # ── Execute salloc via pipe ───────────────────────────────────────────────
    # salloc --qos=interactive creates an interactive step (srun --pty $SHELL).
    # Piping the command into salloc feeds it to that shell naturally.
    # The shell executes the temp script, pipeline's srun calls work within
    # the allocation, shell exits when done → salloc releases.
    set +e

    PIPE_FILE="${MATERIAL_DIR}/.rq_pipe_$$.sh"
    cat > "$PIPE_FILE" <<PIPE_EOF
#!/bin/bash -l
source ~/.bashrc 2>/dev/null || true
module load ${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu} 2>/dev/null
source /global/common/software/m3035/conda/etc/profile.d/conda.sh 2>/dev/null
conda activate /global/common/software/m526/phonopy_env 2>/dev/null
echo ""
echo "=== Pipeline starting at \$(date) ==="
echo "Flags: ${PIPELINE_FLAGS}"
cd "${MATERIAL_DIR}"
export PYTHONPATH="${RAMAN_DIR}:\${PYTHONPATH:-}"
python "${RAMAN_DIR}/src/automation_raman_analysis.py" ${PIPELINE_FLAGS}
PIPELINE_EXIT=\$?
echo ""
echo "=== Pipeline finished at \$(date) (exit=\$PIPELINE_EXIT) ==="
exit \$PIPELINE_EXIT
PIPE_EOF
    chmod +x "$PIPE_FILE"

    echo "bash \"$PIPE_FILE\"" | salloc \
        -N "$NODES" \
        -C gpu \
        --gpus-per-node=4 \
        -t "$WALLTIME" \
        --qos="$QOS" \
        -A m526 \
        --job-name="rq_${MATERIAL_NAME:0:20}" \
        > "$SRUN_LOG" 2>&1

    PIPELINE_EXIT=$?
    rm -f "$PIPE_FILE"
    set -e

    MATERIAL_END=$(date +%s)
    MATERIAL_ELAPSED=$((MATERIAL_END - MATERIAL_START))
    _fmt_elapsed() {
        local s=$1
        printf "%dh %dm %ds" $((s/3600)) $(((s%3600)/60)) $((s%60))
    }

    # ── Verify results ────────────────────────────────────────────────────
    echo ""
    if [ $PIPELINE_EXIT -eq 0 ] && [ -f "$LOG_FILE" ]; then
        if grep -q "Status.*COMPLETED" "$LOG_FILE" 2>/dev/null; then
            echo "  ✓ $MATERIAL_NAME — COMPLETED ($(_fmt_elapsed $MATERIAL_ELAPSED))"
            QUEUE_COMPLETED=$((QUEUE_COMPLETED + 1))
        else
            echo "  ⚠ $MATERIAL_NAME — Pipeline exited 0 but 'COMPLETED' not found in workflow.log"
            echo "     Check: less $LOG_FILE"
            QUEUE_FAILED=$((QUEUE_FAILED + 1))
        fi
    else
        echo "  ✗ $MATERIAL_NAME — FAILED (exit=$PIPELINE_EXIT, $(_fmt_elapsed $MATERIAL_ELAPSED))"
        echo "     Check: tail -80 $LOG_FILE"
        QUEUE_FAILED=$((QUEUE_FAILED + 1))
    fi

done

# ═══════════════════════════════════════════════════════════════════════════════
#  QUEUE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

OVERALL_END=$(date +%s)
OVERALL_ELAPSED=$((OVERALL_END - OVERALL_START))

echo ""
echo "╔══════════════════════════════════════════════════════════════════════════╗"
echo "║  QUEUE COMPLETE                                                         ║"
echo "╠══════════════════════════════════════════════════════════════════════════╣"
printf "║  Finished:    %-55s ║\n" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
printf "║  Total time:  %-55s ║\n" "$(_fmt_elapsed $OVERALL_ELAPSED)"
printf "║  Completed:   %-55s ║\n" "$QUEUE_COMPLETED / $QUEUE_TOTAL"
printf "║  Failed:      %-55s ║\n" "$QUEUE_FAILED"
printf "║  Skipped:     %-55s ║\n" "$QUEUE_SKIPPED"
echo "╚══════════════════════════════════════════════════════════════════════════╝"

if [ "$QUEUE_FAILED" -gt 0 ]; then
    echo ""
    echo "  Failed materials:"
    for entry in "${QUEUE_MATERIALS[@]}"; do
        IFS='|' read -r name _wt _qos _nodes <<< "$entry"
        LOG="$RAMAN_PROJECT_DIR/$name/workflow.log"
        if [ -f "$LOG" ] && ! grep -q "Status.*COMPLETED" "$LOG" 2>/dev/null; then
            echo "    ✗ $name  (tail -80 $LOG)"
        fi
    done
fi

exit $QUEUE_FAILED
