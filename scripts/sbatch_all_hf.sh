#!/bin/bash
# =============================================================================
#  sbatch_all_hf.sh — Run all hf_POSCAR-* + groundstate/ VASP serially on 1 GPU
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

HF_DIR="${HF_DIR:?HF_DIR must be set (exported by pipeline via --export)}"
VASP="${VASP_BINARY:-/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std}"

source ~/.bashrc 2>/dev/null || true
if [ -n "${CONDA_INIT:-}" ]; then source "$CONDA_INIT" 2>/dev/null; fi
if [ -n "${CONDA_ENV:-}" ]; then conda activate "$CONDA_ENV" 2>/dev/null; fi
if [ -n "${VASP_MODULES:-}" ]; then module load $VASP_MODULES 2>/dev/null; fi

echo "=== sbatch_all_hf: $HF_DIR ==="
cd "$HF_DIR" || exit 1

# Run groundstate first (needed for CHGCAR/WAVECAR in displacement dirs)
if [ -d groundstate ] && { [ ! -f groundstate/OUTCAR ] || ! grep -q "General timing" groundstate/OUTCAR 2>/dev/null; }; then
    echo "--- groundstate ---"
    cd groundstate && srun ${SRUN_ARGS:---gpus=1 --ntasks=1} "$VASP" > relaxation.stdout && cd ..
fi

# Run each hf_POSCAR-* serially
for d in hf_POSCAR-*; do
    if [ ! -d "$d" ]; then continue; fi
    if [ -f "$d/OUTCAR" ] && grep -q "General timing" "$d/OUTCAR" 2>/dev/null; then
        echo "--- $d (cached) ---"
        continue
    fi
    echo "--- $d ---"
    cd "$d" && srun ${SRUN_ARGS:---gpus=1 --ntasks=1} "$VASP" > relaxation.stdout && cd ..
done

echo "=== sbatch_all_hf: DONE ==="
