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
#     1. Copies the material's scf/ and input/ from pscratch to:
#          /global/cfs/cdirs/m526/liangbo/<material_dir>/
#     2. Excludes huge CHG and *.h5 files
#     3. Symlinks CHGCAR and WAVECAR to save CFS quota
#     4. Copies workflow.log and workflow.out
#     5. Sets group = m526, group read-only, no world permissions
# =============================================================================

set -euo pipefail

MATERIAL="$1"
SRC="/pscratch/sd/e/easuresh/vasp_calculations/${MATERIAL}"
DST="/global/cfs/cdirs/m526/liangbo/${MATERIAL}"

if [ ! -d "$SRC" ]; then
    echo "ERROR: source not found: $SRC"
    exit 1
fi

echo "=== Copying ${MATERIAL} ==="
echo "  from: $SRC"
echo "    to: $DST"

# Remove stale copy and re-sync
rm -rf "$DST"
mkdir -p "$DST"

# Copy all subdirectories — exclude CHG (~1 GB), HDF5, and big binary files
for sub in $(ls "$SRC"); do
    src_sub="${SRC}/${sub}"
    if [ -d "$src_sub" ] && [ "$sub" != "." ] && [ "$sub" != ".." ]; then
        rsync -a --exclude='CHG' --exclude='*.h5' --exclude='CHGCAR' --exclude='WAVECAR' \
            "${src_sub}/" "${DST}/${sub}/"
    fi
done

# Symlink CHGCAR and WAVECAR from pscratch (save CFS quota, show 0-byte evidence)
for sub in $(ls "$SRC"); do
    for bigfile in CHGCAR WAVECAR; do
        src_file="${SRC}/${sub}/${bigfile}"
        dst_file="${DST}/${sub}/${bigfile}"
        if [ -f "$src_file" ]; then
            mkdir -p "$(dirname "$dst_file")"
            ln -sf "$src_file" "$dst_file"
            echo "  symlink: $dst_file -> $src_file"
        fi
    done
done

# Copy workflow.log and workflow.out (now live in pscratch)
for log in workflow.log workflow.out; do
    if [ -f "${SRC}/${log}" ]; then
        cp "${SRC}/${log}" "${DST}/"
    fi
done

# Permissions: group m526, group read, no other
chgrp -R m526 "$DST" 2>/dev/null || true
chmod -R u=rwX,g=rX,o= "$DST"
# sgid so new files inherit m526 group
find "$DST" -type d -exec chmod g+s {} \;

echo ""
echo "=== Done: ${DST} ==="
ls -la "$DST/"
echo ""
echo "=== contents ==="
for sub in $(ls "$DST"); do
    if [ -d "${DST}/${sub}" ]; then
        echo "  ${sub}/: $(ls "${DST}/${sub}" | wc -l) files"
    fi
done
