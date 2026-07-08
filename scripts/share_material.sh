#!/bin/bash
# =============================================================================
#  share_material.sh — Copy a material directory to the shared CFS location
# =============================================================================
#  Usage:
#     bash raman_workflow/scripts/share_material.sh <material_dir>
#
#  Example:
#     bash raman_workflow/scripts/share_material.sh hBN_defect_test
#
#  What it does:
#     1. Requests a small interactive allocation (faster path to CFS than the
#        shared login node) and re-execs itself inside it via srun.
#     2. Directly rsyncs the ENTIRE material folder from pscratch to CFS,
#        excluding only WAVECAR, WAVEDER, and *.h5. No intermediate archive.
#     3. --copy-unsafe-links dereferences the input/ symlink (it points
#        outside the material tree, at $HOME) while preserving the internal
#        relative ra_pos_*/CHGCAR -> ../../scf/CHGCAR symlinks (they resolve
#        inside the copied tree, so rsync keeps them as links -- no 966x
#        duplication of CHGCAR).
#     4. Sets group = m526, group read-only, no world permissions.
# =============================================================================

set -uo pipefail

MATERIAL="$1"
SRC="/pscratch/sd/e/easuresh/vasp_calculations/${MATERIAL}"
DST="/global/cfs/cdirs/m526/liangbo/${MATERIAL}"

if [ ! -d "$SRC" ]; then
    echo "ERROR: source not found: $SRC"
    exit 1
fi

# Re-exec inside a small interactive allocation for faster I/O to CFS than the
# login node gives. "${2:-}" is set on the re-exec below to skip this the
# second time through (once already inside the allocation).
if [[ "${2:-}" != "--inside-salloc" ]]; then
    echo "=== Requesting interactive allocation for the transfer ==="
    exec salloc -N 1 -C cpu -t 00:30:00 --qos=interactive -A m526 \
        srun --ntasks=1 --nodes=1 bash "$0" "$MATERIAL" --inside-salloc
fi

echo "=== Copying ${MATERIAL} ==="
echo "  from: $SRC"
echo "    to: $DST"

mkdir -p "$DST"

# Always fix permissions on exit, even if rsync fails partway through
_fix_permissions() {
    echo "  setting permissions..."
    chgrp -R m526 "$DST" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o= "$DST"
    find "$DST" -type d -exec chmod g+s {} +
}
trap _fix_permissions EXIT

echo "=== Copying entire folder: ${MATERIAL} (excluding WAVECAR, WAVEDER, *.h5) ==="
rsync -a --copy-unsafe-links --info=progress2 \
    --exclude='WAVECAR' --exclude='WAVEDER' --exclude='*.h5' \
    "${SRC}/" "${DST}/"

echo ""
echo "=== Done: ${DST}/ ==="
du -sh "$DST"
