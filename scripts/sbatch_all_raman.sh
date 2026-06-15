#!/bin/bash
# =============================================================================
#  sbatch_all_raman.sh — Run all ra_pos_* VASP serially on 1 GPU
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
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail

RAMAN_DIR="${RAMAN_DIR:?RAMAN_DIR must be set (exported by pipeline via --export)}"
VASP="${VASP_BINARY:-/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std}"

source ~/.bashrc 2>/dev/null || true
if [ -n "${CONDA_INIT:-}" ]; then source "$CONDA_INIT" 2>/dev/null; fi
if [ -n "${CONDA_ENV:-}" ]; then conda activate "$CONDA_ENV" 2>/dev/null; fi
if [ -n "${VASP_MODULES:-}" ]; then module load $VASP_MODULES 2>/dev/null; fi

echo "=== sbatch_all_raman: $RAMAN_DIR ==="
cd "$RAMAN_DIR" || exit 1

for d in ra_pos_*; do
    if [ ! -d "$d" ]; then continue; fi
    if [ -f "$d/OUTCAR" ] && grep -q "General timing" "$d/OUTCAR" 2>/dev/null; then
        echo "--- $d (cached) ---"
        continue
    fi
    echo "--- $d ---"
    cd "$d" && srun --gpus=1 --ntasks=1 "$VASP" > relaxation.stdout && cd ..
done

echo "=== sbatch_all_raman: DONE ==="
