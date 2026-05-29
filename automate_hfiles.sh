#!/bin/bash

# --- Script Purpose ---
# This script automates running VASP calculations within each hf_POSCAR-* subdirectory.
# These calculations are essential for extracting forces on displaced atoms,
# which are later used to compute phonon frequencies.
# It is designed to be executed from within an active Slurm interactive session (salloc).

# I usually run this from the full automation code, it calls this code and the raman automation one

# --- Configuration ---
# All paths are configurable via environment variables set in ~/.bashrc.
# See CLAUDE.md for the full list of available variables.

# Absolute path to the VASP executable on the system.
# Set the VASP_BINARY environment variable to override the default.
VASP_BINARY_PATH="${VASP_BINARY:-/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std}"

# Modules required to set up the VASP environment.
# These modules must be available on the Perlmutter compute nodes.
# Set the VASP_MODULES environment variable to override the default.
MODULES_TO_LOAD="${VASP_MODULES:-gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu}"

# srun parameters for launching each individual VASP task.
# These parameters should align with the resources requested by the parent salloc job.
SRUN_PARAMS="--cpu_bind=cores --gpus 4 --ntasks 4 --cpus-per-task 32 -C gpu" # -C gpu ensures GPU node selection


# --- Script Execution ---
echo "--- Starting VASP Runs for Phonon Displacements ---"
echo "VASP Binary Path: $VASP_BINARY_PATH"
echo "Modules to Load: $MODULES_TO_LOAD"
echo "srun Parameters: $SRUN_PARAMS"
echo "---------------------------------------------------"

# Load all necessary modules at the beginning of this script's execution.
echo "Loading required modules for VASP..."
module load $MODULES_TO_LOAD


# Loop through all directories matching the pattern 'hf_POSCAR-*'
# This ensures all generated displacement folders are processed.
for displacement_dir in hf_POSCAR-*; do
    # Check if the item found by the wildcard is actually a directory.
    if [ -d "$displacement_dir" ]; then
        echo "---------------------------------------------------"
        echo "Entering directory: $displacement_dir"
        
        # Navigate into the displacement directory.
        cd "$displacement_dir" || { echo "Error: Failed to change directory to $displacement_dir. Exiting."; exit 1; }

        echo "Running VASP calculation..."
        # Execute VASP using srun. Output is redirected to 'stdout' within each folder.
        srun $SRUN_PARAMS "$VASP_BINARY_PATH" > stdout
        
        # Check the exit code of the srun command to see if VASP ran successfully.
        if [ $? -ne 0 ]; then
            echo "Warning: VASP run FAILED in $displacement_dir. Check 'stdout' file for details."
            # In an automated workflow, you might choose to 'exit 1' here to stop
            # the entire pipeline on the first VASP failure, or log and continue.
            # For this script, it logs a warning and continues by default.
        else
            echo "VASP run completed successfully in $displacement_dir."
        fi

        echo "Returning to parent directory..."
        # Navigate back to the HFfiles directory.
        cd .. || { echo "Error: Failed to return to parent directory. Exiting."; exit 1; }
    else
        echo "Skipping '$displacement_dir': Not a valid directory."
    fi
done

echo "---------------------------------------------------"
echo "All VASP runs for hf_POSCAR-* directories finished."
echo "--- VASP Displacement Calculation Complete ---"

# Note on Job Cancellation:
# This script is intended to be run within an existing interactive Slurm allocation (salloc).
# The salloc job is typically terminated manually by typing 'exit' in the terminal.
# The 'scancel "$SLURM_JOB_ID"' command is generally used at the end of non-interactive
# batch scripts (submitted with 'sbatch') or in specific scenarios for forced termination.
# It is commented out here as it's typically not needed for scripts run within salloc.
# scancel "$SLURM_JOB_ID"