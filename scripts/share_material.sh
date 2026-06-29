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

set -uo pipefail

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

mkdir -p "$DST"

# Always fix permissions on exit, even if rsync fails partway through
_fix_permissions() {
    echo "  setting permissions..."
    chgrp -R m526 "$DST" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o= "$DST"
    find "$DST" -type d -exec chmod g+s {} +
}
trap _fix_permissions EXIT

# Copy all subdirectories — exclude CHG (~1 GB), HDF5, big binary files, and vasprun.xml
RSYNC=(rsync -a --info=progress2
    --exclude=CHG --exclude='*.h5' --exclude=CHGCAR --exclude=WAVECAR
    --exclude=vasprun.xml --exclude='*.vesta'
)
for sub in $(ls "$SRC"); do
    src_sub="${SRC}/${sub}"
    if [ ! -d "$src_sub" ] || [ "$sub" = "." ] || [ "$sub" = ".." ]; then
        continue
    fi

    if [ "$sub" = "raman" ]; then
        # Copy raman/ metadata but skip all ra_pos_* dirs
        "${RSYNC[@]}" --exclude='ra_pos_*/' "${src_sub}/" "${DST}/${sub}/" || true
        # Copy only the first ra_pos_* directory as a sample
        first_rapos=$(ls -d "${src_sub}"/ra_pos_* 2>/dev/null | sort | head -1)
        if [ -n "$first_rapos" ]; then
            rapos_name=$(basename "$first_rapos")
            echo "  raman: copying sample dir ${rapos_name} (1 of $(ls -d "${src_sub}"/ra_pos_* 2>/dev/null | wc -l))"
            "${RSYNC[@]}" "${first_rapos}/" "${DST}/${sub}/${rapos_name}/" || true
        fi
    else
        "${RSYNC[@]}" "${src_sub}/" "${DST}/${sub}/" || true
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
