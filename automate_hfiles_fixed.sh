#!/bin/bash

# =============================================================================
#  automate_hfiles_fixed.sh — env-aware local copy
# =============================================================================
#  Runs VASP in each hf_POSCAR-* subdirectory for phonon force-constant
#  calculations. Unlike the original at $BINARY_UTILITIES_DIR, this copy
#  reads $SRUN_ARGS from the environment, so it respects whatever srun
#  configuration the Python pipeline has set (single-node, multi-node, etc.).
#
#  The env var $SRUN_ARGS is exported by the pipeline before calling this
#  script. Falls back to the legacy 4-GPU hardcoded params if not set.
# =============================================================================

# --- Configuration ---
VASP_BINARY_PATH="${VASP_BINARY:-/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std}"
MODULES_TO_LOAD="${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}"

# Read srun params from the environment (set by the Python pipeline via
# build_srun_args()). Falls back to legacy 4-GPU params for standalone use.
SRUN_PARAMS="${SRUN_ARGS:---cpu_bind=cores --gpus 4 --ntasks 4 --cpus-per-task 32 -C gpu}"

# --- Script Execution ---
echo "--- Starting VASP Runs for Phonon Displacements ---"
echo "VASP Binary Path: $VASP_BINARY_PATH"
echo "Modules to Load: $MODULES_TO_LOAD"
echo "srun Parameters: $SRUN_PARAMS"
echo "---------------------------------------------------"

echo "Loading required modules for VASP..."
module load $MODULES_TO_LOAD

for displacement_dir in hf_POSCAR-*; do
    if [ -d "$displacement_dir" ]; then
        echo "---------------------------------------------------"
        echo "Entering directory: $displacement_dir"

        cd "$displacement_dir" || { echo "Error: Failed to change directory to $displacement_dir. Exiting."; exit 1; }

        echo "Running VASP calculation..."
        srun $SRUN_PARAMS "$VASP_BINARY_PATH" > stdout

        if [ $? -ne 0 ]; then
            echo "Warning: VASP run FAILED in $displacement_dir. Check 'stdout' file for details."
        else
            echo "VASP run completed successfully in $displacement_dir."
        fi

        cd .. || { echo "Error: Failed to return to parent directory. Exiting."; exit 1; }
    else
        echo "Skipping '$displacement_dir': Not a valid directory."
    fi
done

echo "---------------------------------------------------"
echo "All VASP runs for hf_POSCAR-* directories finished."
echo "--- VASP Displacement Calculation Complete ---"
