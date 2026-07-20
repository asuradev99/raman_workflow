#!/bin/bash -l
source ~/.bashrc 2>/dev/null || true
module load PrgEnv-nvidia gpu cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu 2>/dev/null
source /global/common/software/m3035/conda/etc/profile.d/conda.sh 2>/dev/null
conda activate /global/common/software/m526/phonopy_env 2>/dev/null
echo ""
echo "=== Pipeline starting at $(date) ==="
echo "Flags: --restart --scratch"
cd "/global/homes/e/easuresh/vasp_calculations/hBN_LDA"
python "/global/homes/e/easuresh/raman_workflow/automation_raman_analysis.py" --restart --scratch
PIPELINE_EXIT=$?
echo ""
echo "=== Pipeline finished at $(date) (exit=$PIPELINE_EXIT) ==="
exit $PIPELINE_EXIT
