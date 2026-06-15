#!/bin/bash
# =============================================================================
#  sbatch_raman_dir.sh — Run one ra_pos_* VASP calculation via sbatch
# =============================================================================
#  All resource args (--nodes, --gpus-per-node, --ntasks-per-node,
#  --cpus-per-task, --time, --qos, --constraint) are passed as sbatch CLI
#  overrides by submit_many() in util/compute.py — sourced from the
#  sbatch_per_dir key in the compute_modes config block.  srun args come
#  from $SRUN_ARGS (set in --export by submit_many from srun_per_dir config).
#
#  Usage (submitted by pipeline, not run directly):
#    sbatch [resource-args] --export=...,DIR=/path/to/ra_pos_B1a \
#           scripts/sbatch_raman_dir.sh
# =============================================================================
#SBATCH --account=m526
#SBATCH --requeue
#SBATCH --export=ALL

set -euo pipefail

DIR="${DIR:-$1}"
if [ -z "${DIR:-}" ]; then
    echo "ERROR: DIR environment variable or first argument required"
    exit 1
fi

source ~/.bashrc 2>/dev/null || true
if [ -n "${CONDA_INIT:-}" ]; then source "$CONDA_INIT" 2>/dev/null; fi
if [ -n "${CONDA_ENV:-}" ]; then conda activate "$CONDA_ENV" 2>/dev/null; fi
if [ -n "${VASP_MODULES:-}" ]; then module load $VASP_MODULES 2>/dev/null; fi

cd "$DIR" || exit 1
srun ${SRUN_ARGS:-} "${VASP_BINARY:-/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std}" > relaxation.stdout
