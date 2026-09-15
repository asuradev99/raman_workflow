# =============================================================================
#  clusters/nersc.bashrc — NERSC Perlmutter system_paths for raman_workflow.
#
#  'install.sh nersc' sources this file for its defaults and appends the
#  resulting export lines into ~/.bashrc's raman_workflow block. This is the
#  only file new/common.sh's and new/generate.py's env vars ultimately come
#  from for this cluster — edit here to change every future 'install.sh
#  nersc' run, or edit the ~/.bashrc block directly to fix one already
#  installed.
# =============================================================================
export CONDA_INIT="/global/common/software/m3035/conda/etc/profile.d/conda.sh"
export CONDA_ENV="/global/common/software/m526/phonopy_env"
export LIANGBO_SHARED_DIR="/global/cfs/cdirs/m526/liangbo"
export VASP_MODULES="PrgEnv-nvidia gpu cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu"
export VASP_BINARY="$LIANGBO_SHARED_DIR/bin/gpu/vasp_std"
export VASP_BINARY_CPU="$LIANGBO_SHARED_DIR/bin/cpu/vasp_std"
export VASP_BINARY_GAM="$LIANGBO_SHARED_DIR/bin/gpu/vasp_gam"
export VASP_BINARY_GAM_CPU="$LIANGBO_SHARED_DIR/bin/cpu/vasp_gam"
export BINARY_UTILITIES_DIR="/global/cfs/cdirs/m526/vasp_binaries/binary_utility"
# Optional — only read for --debug mode's VESTA visualization step; a missing
# value just skips that step (see build_bake's VIZ_ENABLED check). This is a
# user home path, not a shared cluster path: point it at your own SpectroPy
# checkout if you use --debug.
export SPECTROPY_DIR="/global/homes/e/easuresh/SpectroPy"
# Where share_material.sh copies a finished material to for Liangbo. NOT yet
# forwarded by install.sh's ~/.bashrc writer -- export it by hand, or add it
# to that writer, before relying on it.
export SHARE_MATERIAL_DIR="$LIANGBO_SHARED_DIR"
