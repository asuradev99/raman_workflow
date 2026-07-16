#!/bin/bash
# =============================================================================
#  share_material.sh — Copy a material directory to the shared CFS location
# =============================================================================
#  Usage:
#     bash raman_workflow/scripts/share_material.sh <material_dir> [--with-chgcar-wavecar]
#
#  Example:
#     bash raman_workflow/scripts/share_material.sh hBN_defect_test
#     bash raman_workflow/scripts/share_material.sh hBN_defect_test --with-chgcar-wavecar
#
#  What it does:
#     1. Requests a 4-node interactive allocation and re-execs itself inside
#        it as a coordinator.
#     2. Splits the copy into one rsync stream per node: the "base" stream
#        carries everything except raman/ra_pos_*; the remaining streams
#        split the ra_pos_* directories evenly between them.
#     3. The base stream uses --copy-unsafe-links to dereference the input/
#        symlink (it points outside the material tree, at $HOME). The ra_pos
#        streams use plain -a so the internal relative CHGCAR ->
#        ../../scf/CHGCAR (and WAVECAR) links stay links -- the real file in
#        scf/ arrives via the base stream, so they resolve at the destination
#        instead of being duplicated at every hf_POSCAR-*/ra_pos_* dir.
#     4. By default excludes CHGCAR*, WAVECAR* (incl. backups), WAVEDER,
#        *.h5, *.ispin1_backup. Pass --with-chgcar-wavecar to include the
#        real CHGCAR/WAVECAR files in scf/ (and their downstream symlinks) --
#        everything else (WAVEDER, *.h5, *.ispin1_backup) stays excluded.
#     5. Sets group = m526, group read-only, no world permissions.
#
#  rsync is incremental, so rerunning after a timeout/failure resumes where
#  the previous run left off.
# =============================================================================

set -uo pipefail

MATERIAL="$1"
SRC="/pscratch/sd/e/easuresh/vasp_calculations/${MATERIAL}"
DST="/global/cfs/cdirs/m526/liangbo/${MATERIAL}"

WITH_CHGCAR_WAVECAR=0
for arg in "$@"; do
    [[ "$arg" == "--with-chgcar-wavecar" ]] && WITH_CHGCAR_WAVECAR=1
done

if [ ! -d "$SRC" ]; then
    echo "ERROR: source not found: $SRC"
    exit 1
fi

# Re-exec inside an interactive allocation. "--inside-salloc" is appended on
# the re-exec below to skip this the second time through. The coordinator
# itself runs on the submitting host; each rsync stream is dispatched to its
# own node via srun -w below.
if [[ " $* " != *" --inside-salloc "* ]]; then
    echo "=== Requesting interactive allocation for the transfer ==="
    exec salloc -N 4 -C cpu -t 01:00:00 --qos=interactive -A m526 \
        bash "$0" "$@" --inside-salloc
fi

EXCLUDES=(--exclude='WAVEDER' --exclude='*.h5' --exclude='*.ispin1_backup')
if (( WITH_CHGCAR_WAVECAR )); then
    echo "=== Including CHGCAR/WAVECAR (real files copied once in scf/, symlinked elsewhere) ==="
else
    EXCLUDES+=(--exclude='CHGCAR*' --exclude='WAVECAR*')
fi

mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
N_NODES=${#NODES[@]}

echo "=== Copying ${MATERIAL} (${N_NODES} parallel streams) ==="
echo "  from: $SRC"
echo "    to: $DST"

mkdir -p "$DST"

# Always fix permissions on exit, even if a stream fails partway through
_fix_permissions() {
    echo "  setting permissions..."
    chgrp -R m526 "$DST" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o= "$DST"
    find "$DST" -type d -exec chmod g+s {} +
}
trap _fix_permissions EXIT

# Single-node fallback: one full-tree rsync, original behaviour.
if (( N_NODES < 2 )); then
    srun -N1 -n1 rsync -a --copy-unsafe-links --info=progress2 \
        "${EXCLUDES[@]}" "${SRC}/" "${DST}/"
    echo ""
    echo "=== Done: ${DST}/ ==="
    du -sh "$DST"
    exit
fi

PIDS=()
LABELS=()

_launch() {  # <node> <label> <rsync args...>
    local node="$1" label="$2"
    shift 2
    echo "  [${label}] starting on ${node}"
    srun -w "$node" -N1 -n1 --overlap "$@" &
    PIDS+=($!)
    LABELS+=("$label")
}

# Base stream — everything except the per-displacement raman dirs
_launch "${NODES[0]}" "base" \
    rsync -a --copy-unsafe-links "${EXCLUDES[@]}" \
    --exclude='/raman/ra_pos_*' "${SRC}/" "${DST}/"

# Remaining streams — ra_pos_* dirs split evenly across the other nodes
if [ -d "${SRC}/raman" ]; then
    mapfile -t RA_DIRS < <(cd "${SRC}/raman" && ls -d ra_pos_* 2>/dev/null)
    N_RA=${#RA_DIRS[@]}
    N_CHUNKS=$(( N_NODES - 1 ))
    if (( N_RA > 0 )); then
        mkdir -p "${DST}/raman"
        PER=$(( (N_RA + N_CHUNKS - 1) / N_CHUNKS ))
        for (( c = 0; c < N_CHUNKS; c++ )); do
            CHUNK=( "${RA_DIRS[@]:c*PER:PER}" )
            (( ${#CHUNK[@]} > 0 )) || continue
            SRCS=( "${CHUNK[@]/#/${SRC}/raman/}" )
            _launch "${NODES[c+1]}" "ra_pos-$((c+1))/${N_CHUNKS} (${#CHUNK[@]} dirs)" \
                rsync -a "${EXCLUDES[@]}" "${SRCS[@]}" "${DST}/raman/"
        done
    fi
fi

RC=0
for i in "${!PIDS[@]}"; do
    if wait "${PIDS[$i]}"; then
        echo "  [${LABELS[$i]}] done"
    else
        echo "  [${LABELS[$i]}] FAILED"
        RC=1
    fi
done

echo ""
if (( RC != 0 )); then
    echo "=== ERROR: one or more streams failed — rerun this script to resume ==="
else
    echo "=== Done: ${DST}/ ==="
fi
du -sh "$DST"
exit $RC
