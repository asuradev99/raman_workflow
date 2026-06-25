#!/bin/bash
# =============================================================================
#  vasp_archive.sh — Archive or delete VASP calculation directories
# =============================================================================
#  Usage:
#    vasp_archive.sh archive <dir_name> [<dir_name> ...]
#        Moves named directories to old/ in both HOME and SCRATCH vasp_calculations.
#        Creates old/ if it doesn't exist. Safe: only moves, never deletes.
#
#    vasp_archive.sh delete <dir_name> [<dir_name> ...]
#        Permanently deletes named directories from old/ only.
#        Refuses to delete anything outside old/.
# =============================================================================

set -euo pipefail

HOME_BASE="$HOME/vasp_calculations"
HOME_OLD="$HOME/old"
SCRATCH_BASE="/pscratch/sd/e/easuresh/vasp_calculations"
SCRATCH_OLD="/pscratch/sd/e/easuresh/old"

usage() {
    echo "Usage: vasp_archive.sh archive <dir> [<dir> ...]"
    echo "       vasp_archive.sh delete  <dir> [<dir> ...]"
    echo "       vasp_archive.sh rename  <old_name> <new_name>"
    exit 1
}

[[ $# -lt 2 ]] && usage
CMD="$1"; shift

case "$CMD" in

archive)
    for NAME in "$@"; do
        # Strip trailing slashes and any path prefix — operate on names only
        NAME="${NAME%/}"
        NAME="${NAME##*/}"

        if [[ "$NAME" == "old" || "$NAME" == "old/"* ]]; then
            echo "ERROR: '$NAME' looks like it's already in old/ — skipping"
            continue
        fi

        MOVED_ANY=0
        for PAIR in "$HOME_BASE:$HOME_OLD" "$SCRATCH_BASE:$SCRATCH_OLD"; do
            BASE="${PAIR%%:*}"
            OLD_DIR="${PAIR##*:}"
            SRC="$BASE/$NAME"
            DST="$OLD_DIR/$NAME"

            if [ ! -e "$SRC" ]; then
                echo "  [skip] $SRC — not found"
                continue
            fi
            if [ -e "$DST" ]; then
                echo "  [skip] $DST — already exists in old/"
                continue
            fi

            mkdir -p "$OLD_DIR"
            mv "$SRC" "$DST"
            echo "  [archived] $SRC → $DST"
            MOVED_ANY=1
        done

        if [ $MOVED_ANY -eq 0 ]; then
            echo "  [warn] '$NAME' not found in HOME or SCRATCH vasp_calculations"
        fi
    done
    ;;

delete)
    for NAME in "$@"; do
        NAME="${NAME%/}"
        NAME="${NAME##*/}"

        # Safety: only delete from old/
        if [[ "$NAME" == *"/"* ]]; then
            echo "ERROR: '$NAME' contains a path separator — pass directory names only"
            continue
        fi

        DELETED_ANY=0
        for OLD_DIR in "$HOME_OLD" "$SCRATCH_OLD"; do
            TARGET="$OLD_DIR/$NAME"

            if [ ! -e "$TARGET" ]; then
                echo "  [skip] $TARGET — not found"
                continue
            fi

            # Double-check the target is genuinely inside an old/ directory
            if [[ "$TARGET" != */old/* ]]; then
                echo "ERROR: Safety check failed — '$TARGET' is not inside an old/ directory"
                continue
            fi

            echo -n "  [delete] $TARGET ... "
            rm -rf "$TARGET"
            echo "done"
            DELETED_ANY=1
        done

        if [ $DELETED_ANY -eq 0 ]; then
            echo "  [warn] '$NAME' not found in old/ on HOME or SCRATCH"
        fi
    done
    ;;

rename)
    [[ $# -ne 2 ]] && { echo "Usage: vasp_archive.sh rename <old_name> <new_name>"; exit 1; }
    OLD_NAME="${1%/}"; OLD_NAME="${OLD_NAME##*/}"
    NEW_NAME="${2%/}"; NEW_NAME="${NEW_NAME##*/}"

    RENAMED_ANY=0
    for BASE in "$HOME_BASE" "$SCRATCH_BASE"; do
        SRC="$BASE/$OLD_NAME"
        DST="$BASE/$NEW_NAME"

        if [ ! -e "$SRC" ]; then
            echo "  [skip] $SRC — not found"
            continue
        fi
        if [ -e "$DST" ]; then
            echo "  [skip] $DST — destination already exists"
            continue
        fi

        mv "$SRC" "$DST"
        echo "  [renamed] $SRC → $DST"
        RENAMED_ANY=1
    done

    if [ $RENAMED_ANY -eq 0 ]; then
        echo "  [warn] '$OLD_NAME' not found in HOME or SCRATCH vasp_calculations"
    fi
    ;;

*)
    echo "Unknown command: $CMD"
    usage
    ;;

esac
