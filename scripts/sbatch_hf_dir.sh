#!/bin/bash
# =============================================================================
#  sbatch_hf_dir.sh — Run one hf_POSCAR-* VASP calculation via sbatch
# =============================================================================
#  Usage (submitted by pipeline, not run directly):
#    sbatch --export=DIR=/path/to/hf_POSCAR-001 scripts/sbatch_hf_dir.sh
# =============================================================================
#SBATCH --gpus=1
#SBATCH --ntasks=1
#SBATCH --nodes=1
#SBATCH --cpus-per-task=8
#SBATCH --time=02:00:00
#SBATCH --qos=preempt
#SBATCH --constraint=gpu
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
srun --gpus=1 --ntasks=1 "${VASP_BINARY:-/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std}" > relaxation.stdout
