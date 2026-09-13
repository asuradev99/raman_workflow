# =============================================================================
#  clusters/pathfinder.bashrc — ORNL CADES Pathfinder system_paths for
#  raman_workflow.
#
#  'install.sh pathfinder' sources this file for its defaults and appends
#  the resulting export lines into ~/.bashrc's raman_workflow block.
#
#  Confirmed 2026-07-27 by inspecting /projects/hpcl-mat269/proj-shared/liangbo/
#  directly (ldd on the VASP binaries, `module avail`/`module show`) and from
#  Liangbo's working job script (below). Two things remain unresolved --
#  see the TODOs on CONDA_ENV and VASP_BINARY/VASP_BINARY_GAM.
# =============================================================================

# Miniforge3 is a centrally-installed Lmod module (not a project path); its
# conda.sh is real and importable as-is.
export CONDA_INIT="/software/baseline/nsp/miniforge3/24.11.3-0/etc/profile.d/conda.sh"

# Built at ~/phonopy_env (a plain venv, not a conda env despite the var name --
# conda-meta/ absence is how common.sh/generate.py's CONDA_ENV activation
# logic detects this and falls back to `source $CONDA_ENV/bin/activate`).
# Has phonopy + PyYAML + SpectroPy (pip install '.[all]' from ~/SpectroPy) --
# raman_prep/post_process depend on SpectroPy since it replaced the Fortran
# raman_utility displacement/derivative/spectrum chain.
export CONDA_ENV="$HOME/phonopy_env"

# Liangbo's working job script (2026-07-27) loads exactly this, to run the
# CPU/MPI vasp_std below -- confirmed by `ldd vasp_std` needing
# libscalapack/libopenblas/libfftw3(_omp), all satisfied by these modules:
#   module load gcc/12.4.0 openmpi/5.0.5 fftw/3.3.10-omp openblas/0.3.28-omp netlib-scalapack/2.2.0-mpi
# Note: a centrally-installed `vasp/6.5.1`/`vasp/6.6.0` module also exists
# (`module avail vasp`), but Liangbo's script explicitly builds/runs the
# proj-shared binary below instead -- don't substitute the central module
# without checking with him first.
export VASP_MODULES="gcc/12.4.0 openmpi/5.0.5 fftw/3.3.10-omp openblas/0.3.28-omp netlib-scalapack/2.2.0-mpi"

# vasp_bin/ has vasp_gam, vasp_ncl, vasp_std -- `ldd` on vasp_std confirms
# OpenMPI linkage and NO CUDA/ROCm/NVIDIA libs anywhere: this is a CPU/MPI
# build only, no GPU variant exists yet. Liangbo's job script runs vasp_std
# this way: plain `srun --cpu-bind=cores` inside an sbatch allocation sized
# by --nodes/--ntasks/-c (no GPU flags at all) -- e.g. 2 nodes x 256 tasks,
# -c 1, --mem-per-cpu=4G. He notes: max ~15 nodes, ~24h wall time on this
# account/partition (-p parallel -q normal).
# TODO: VASP_BINARY / VASP_BINARY_GAM (the GPU slots) are left unset on
# purpose -- there's no GPU build to point them at yet, so generate.py fails
# loudly if you forget --cpu, instead of silently running the CPU binary
# under a GPU-shaped config. Use `generate.py --cpu` until a GPU build exists.
export VASP_BINARY_CPU="/projects/hpcl-mat269/proj-shared/liangbo/vasp_bin/vasp_std"
export VASP_BINARY_GAM_CPU="/projects/hpcl-mat269/proj-shared/liangbo/vasp_bin/vasp_gam"
export VASP_BINARY=""
export VASP_BINARY_GAM=""

# Confirmed present (2026-07-27): raman_symmetry_mapping, raman_dis,
# raman_dis_nosym, raman_poscar, epsilon_derivative, raman_tensor -- all six
# generate.py's raman_prep/post_process steps call. NOT present here:
# runHF, used by hf_setup (untouched by Liangbo's raman-step rewrite) -- that
# one is still unaccounted for and will need locating or building separately.
export BINARY_UTILITIES_DIR="/projects/hpcl-mat269/proj-shared/liangbo/raman_utility"

# Confirmed present at ~/SpectroPy (real checkout, not a placeholder).
export SPECTROPY_DIR="$HOME/SpectroPy"

# Liangbo's vasp_std needs pure-MPI parallelism (1 task per core, -c 1 in his
# job script) rather than the OpenMP threading fftw/3.3.10-omp and
# openblas/0.3.28-omp are also capable of -- set OMP_NUM_THREADS=1 so those
# libraries don't spawn threads underneath each MPI rank.
# TODO: install.sh's ~/.bashrc writer only forwards the fixed set of vars it
# already lists (CONDA_INIT/CONDA_ENV/VASP_MODULES/VASP_BINARY*/
# BINARY_UTILITIES_DIR/SPECTROPY_DIR) -- it does not yet forward this one, so
# setting it here alone won't reach a real ~/.bashrc via install.sh pathfinder.
# Add it to the writer, or export it by hand, before relying on it.
export OMP_NUM_THREADS=1

# Where share_material.sh --pathfinder copies a finished material to. NOT yet
# forwarded by install.sh's ~/.bashrc writer (same caveat as OMP_NUM_THREADS/
# SCRATCH below) -- export it by hand, or add it to that writer, before
# relying on it. This is e4z's own proj-shared subdirectory (created
# 2026-07-29, e4z has full read/write since it's the owner) -- NOT
# proj-shared/liangbo, which e4z does not have write access to.
export SHARE_MATERIAL_DIR="/projects/hpcl-mat269/proj-shared/easuresh"

# ~/scratch is a real symlink to /scratch/hpcl-mat269/e4z (confirmed on disk).
# generate.py's default scratch-mode writes to $SCRATCH/vasp_calculations/<name>/
# -- but the material dirs already on this scratch (hBN_VB-_6x6, hBN_VB-_9x9,
# etc.) sit directly at $SCRATCH/<name>/, with no vasp_calculations/ layer.
# Exporting SCRATCH here doesn't reconcile that mismatch -- it just makes
# generate.py's existing (NERSC-shaped) default resolve to a real path instead
# of erroring "SCRATCH is unset". Same install.sh-forwarding caveat as
# OMP_NUM_THREADS above: this needs adding to the writer, or exporting by hand.
export SCRATCH="$HOME/scratch"
