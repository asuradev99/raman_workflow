#!/bin/bash
# Show the most recent status block from workflow.log.
# Usage: bash show_status.sh [path/to/workflow.log]
#   Defaults to $RAMAN_PROJECT_DIR/hBN/workflow.log if no argument given.
#
# workflow.log is append-only; this extracts only the last status table
# (delimited by ━━━ lines), so `tail -30` never misses the current state.

LOG="${1:-${RAMAN_PROJECT_DIR:-$HOME/vasp_calculations}/hBN/workflow.log}"

if [ ! -f "$LOG" ]; then
    echo "ERROR: workflow.log not found: $LOG"
    echo "Usage: bash show_status.sh [/path/to/workflow.log]"
    exit 1
fi

# Extract the last status block: reverse the file, grab lines up to the
# 5th ━━━ delimiter (each block has 4 ━ lines; the 5th in reversed order is
# the end of the preceding block), then reverse back.
tac "$LOG" | awk '/^━/{if(++n==5)exit} {print}' | tac
