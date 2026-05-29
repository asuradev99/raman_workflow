#!/bin/bash

# This script should be run *inside* an interactive salloc session
# obtained via your 'job_bash' script.
#
# [DEEPSEEK 2026-05-27] Fixed copy of the original run_all_vasp_folders.sh
# - Commented out scancel at line 59 (was killing the entire job when run inside batch)
# - The original lives at: /global/cfs/cdirs/m526/vasp_binaries/binary_utility/run_all_vasp_folders.sh

# Use env vars (set in ~/.bashrc) so this script respects the same configuration
# as automate_hfiles.sh and the main pipeline, rather than hardcoding paths.
VASP_BINARY_PATH="${VASP_BINARY:-/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std}"
MODULES_TO_LOAD="${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}"

# srun parameters — keep in sync with vasp_srun settings in workflow_settings.yaml.
SRUN_PARAMS="--cpu_bind=cores --gpus 4 --ntasks 4 --cpus-per-task 32"

echo "Starting VASP runs in subdirectories..."
echo "VASP binary: $VASP_BINARY_PATH"
echo "srun parameters: $SRUN_PARAMS"
echo "-------------------------------------"

# Load modules once before the loop (already loaded by the parent sbatch/interactive
# script, but reloading here ensures this script also works standalone).
echo "Loading required modules..."
module load $MODULES_TO_LOAD

# Loop through all directories matching the pattern 'ra_pos_*'
for dir in ra_pos_*; do
    if [ -d "$dir" ]; then
        echo "Entering directory: $dir"
        cd "$dir" || { echo "Failed to change directory to $dir. Exiting."; exit 1; }

        echo "Running VASP command..."
        srun $SRUN_PARAMS "$VASP_BINARY_PATH" > stdout

        if [ $? -ne 0 ]; then
            echo "VASP run failed in $dir. Check stdout for details."
            # Optionally, you can uncomment the next line to stop the script on the first error.
            # exit 1
        else
            echo "VASP run completed successfully in $dir."
        fi

        cd .. || { echo "Failed to return to parent directory. Exiting."; exit 1; }
        echo "-------------------------------------"
    fi
done

echo "All VASP runs completed in subdirectories."
echo "Check 'stdout' files in each ra_pos_* folder for VASP output."
# [FIXED] DO NOT scancel here — the pipeline continues with kopia/ramfile/raman_tensor/broadening after this.
# Original had: scancel "$SLURM_JOB_ID"
# which would kill the entire batch job prematurely.
