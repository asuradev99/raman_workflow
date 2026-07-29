#!/bin/bash
# =============================================================================
#  share_material.sh — Copy a material directory to a shared location
# =============================================================================
#  Usage:
#     bash raman_workflow/scripts/share_material.sh <material_dir> [--with-chgcar-wavecar] [--pathfinder] [--login-node]
#
#  Example:
#     bash raman_workflow/scripts/share_material.sh hBN_defect_test
#     bash raman_workflow/scripts/share_material.sh hBN_defect_test --with-chgcar-wavecar
#     bash raman_workflow/scripts/share_material.sh MoS2_fullsym --pathfinder
#     bash raman_workflow/scripts/share_material.sh MoS2 --pathfinder --login-node
#
#  What it does:
#     1. Requests a 4-node interactive allocation and re-execs itself inside
#        it as a coordinator. Pass --login-node to skip this entirely and
#        just run a single plain rsync directly, no salloc/srun at all --
#        simplest option, and the only sane one for a modest-size transfer
#        (a few hundred MB-few GB) where a 4-node allocation is overkill and
#        each cluster's Slurm resource-spec requirements (ntasks/cpus/mem
#        are NOT all optional on Pathfinder, unlike Perlmutter) otherwise
#        have to be gotten exactly right just to move some files.
#     2. (multi-node mode only) Splits the copy into one rsync stream per
#        node: the "base" stream carries everything except raman/ra_pos_*;
#        the remaining streams split the ra_pos_* directories evenly between
#        them.
#     3. The base stream uses --copy-unsafe-links to dereference the input/
#        symlink (it points outside the material tree, at $HOME). The ra_pos
#        streams use plain -a so the internal relative CHGCAR ->
#        ../../scf/CHGCAR (and WAVECAR) links stay links -- the real file in
#        scf/ arrives via the base stream, so they resolve at the destination
#        instead of being duplicated at every hf_POSCAR-*/ra_pos_* dir.
#        (--login-node mode does one single -a --copy-unsafe-links pass over
#        everything, since there's no point splitting a single stream.)
#     4. By default excludes CHGCAR*, WAVECAR* (incl. backups), WAVEDER,
#        *.h5, *.ispin1_backup. Pass --with-chgcar-wavecar to include the
#        real CHGCAR/WAVECAR files in scf/ (and their downstream symlinks) --
#        everything else (WAVEDER, *.h5, *.ispin1_backup) stays excluded.
#     5. Sets group read-only, no world permissions -- group is m526 by
#        default, or hpcl-mat269 with --pathfinder (see below).
#
#  Destination is always $SHARE_MATERIAL_DIR/<material> -- SHARE_MATERIAL_DIR
#  is exported by pathfinder.bashrc / nersc.bashrc (cluster-specific value),
#  NOT hardcoded here, so there's one place to fix if it's ever wrong. See
#  the "SHARE_MATERIAL_DIR is not set" check below if it's missing --
#  install.sh's ~/.bashrc writer doesn't forward it yet, so export it by
#  hand for now if it's not already in your shell.
#
#  --pathfinder: shares from ORNL CADES Pathfinder's scratch instead of NERSC
#  Perlmutter's, and sets Pathfinder's project group on the copy:
#     source: $SCRATCH/vasp_calculations/<material>  ($SCRATCH =
#             /scratch/hpcl-mat269/e4z per pathfinder.bashrc, matching where
#             generate.py's scratch mode writes MoS2/MoS2_nosym/MoS2_fullsym
#             on this cluster)
#     group:  hpcl-mat269 (Pathfinder's project group, not m526)
#     allocation: Pathfinder has no -C cpu / -A / --qos=interactive (those
#                 are Perlmutter-specific) -- uses the same
#                 "-p parallel -q normal" partition/QOS as this repo's own
#                 sbatch_mix_pathfinder compute_mode.
#
#  rsync is incremental, so rerunning after a timeout/failure resumes where
#  the previous run left off.
# =============================================================================

set -uo pipefail

MATERIAL="$1"

WITH_CHGCAR_WAVECAR=0
PATHFINDER=0
LOGIN_NODE=0
for arg in "$@"; do
    [[ "$arg" == "--with-chgcar-wavecar" ]] && WITH_CHGCAR_WAVECAR=1
    [[ "$arg" == "--pathfinder" ]] && PATHFINDER=1
    [[ "$arg" == "--login-node" ]] && LOGIN_NODE=1
done

if [ -z "${SHARE_MATERIAL_DIR:-}" ]; then
    echo "ERROR: SHARE_MATERIAL_DIR is not set."
    echo "  It's exported by pathfinder.bashrc/nersc.bashrc (installed via install.sh's"
    echo "  ~/.bashrc block) -- but that writer doesn't forward this var yet, so it must"
    echo "  be exported by hand for now: export SHARE_MATERIAL_DIR=\"...\""
    exit 1
fi
DST="${SHARE_MATERIAL_DIR}/${MATERIAL}"

if (( PATHFINDER )); then
    SRC="${SCRATCH:-$HOME/scratch}/vasp_calculations/${MATERIAL}"
    SHARE_GROUP="hpcl-mat269"
else
    SRC="/pscratch/sd/e/easuresh/vasp_calculations/${MATERIAL}"
    SHARE_GROUP="m526"
fi

if [ ! -d "$SRC" ]; then
    echo "ERROR: source not found: $SRC"
    exit 1
fi

EXCLUDES=(--exclude='WAVEDER' --exclude='*.h5' --exclude='*.ispin1_backup')
if (( WITH_CHGCAR_WAVECAR )); then
    echo "=== Including CHGCAR/WAVECAR (real files copied once in scf/, symlinked elsewhere) ==="
else
    EXCLUDES+=(--exclude='CHGCAR*' --exclude='WAVECAR*')
fi

_fix_permissions() {
    echo "  setting permissions..."
    chgrp -R "$SHARE_GROUP" "$DST" 2>/dev/null || true
    chmod -R u=rwX,g=rX,o= "$DST"
    find "$DST" -type d -exec chmod g+s {} +
}

# --login-node: no salloc/srun at all -- a single plain rsync run right here,
# on whatever node this script is invoked from. Simplest option, and the
# right one for a modest transfer.
if (( LOGIN_NODE )); then
    echo "=== Copying ${MATERIAL} (single stream, login node, no allocation) ==="
    echo "  from: $SRC"
    echo "    to: $DST"
    mkdir -p "$DST"
    trap _fix_permissions EXIT
    rsync -a --copy-unsafe-links --info=progress2 \
        "${EXCLUDES[@]}" "${SRC}/" "${DST}/"
    RC=$?
    echo ""
    if (( RC != 0 )); then
        echo "=== ERROR: rsync failed (exit $RC) -- rerun this script to resume ==="
    else
        echo "=== Done: ${DST}/ ==="
    fi
    du -sh "$DST" 2>/dev/null
    exit $RC
fi

# Re-exec inside an interactive allocation. "--inside-salloc" is appended on
# the re-exec below to skip this the second time through. The coordinator
# itself runs on the submitting host; each rsync stream is dispatched to its
# own node via srun -w below.
if [[ " $* " != *" --inside-salloc "* ]]; then
    echo "=== Requesting interactive allocation for the transfer ==="
    if (( PATHFINDER )); then
        exec salloc -N 4 -n 4 -c 1 --mem-per-cpu=4G -p parallel -q normal -t 01:00:00 \
            bash "$0" "$@" --inside-salloc
    else
        exec salloc -N 4 -C cpu -t 01:00:00 --qos=interactive -A m526 \
            bash "$0" "$@" --inside-salloc
    fi
fi

mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
N_NODES=${#NODES[@]}

echo "=== Copying ${MATERIAL} (${N_NODES} parallel streams) ==="
echo "  from: $SRC"
echo "    to: $DST"

mkdir -p "$DST"

# Always fix permissions on exit, even if a stream fails partway through
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
