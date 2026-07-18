export PATH="$HOME/.local/bin:$PATH"
export PATH="$PATH:$HOME/raman_workflow/scripts"
for script in "$HOME/raman_workflow/scripts"/*.sh; do
    name=$(basename "$script" .sh)
    alias "$name"="bash $script"
done

export RAMAN_PROJECT_DIR=/global/homes/e/easuresh/vasp_calculations
export BINARY_UTILITIES_DIR=/global/cfs/cdirs/m526/vasp_binaries/binary_utility
export VASP_BINARY=/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std

# VASP binary is linked against nccl/2.18.3; the OFI plugin (libnccl-net.so)
# lives in the same module and is activated via NCCL_NET env var below.
export VASP_MODULES="PrgEnv-nvidia gpu cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu"
export VASP_MODULES_CPU="PrgEnv-gnu cpu cray-hdf5 cray-fftw vasp/6.4.3-cpu"
# Commented out so the CPU binary is chosen by each material's config
# (system_paths.vasp_binary_cpu); env would otherwise override config.
# Default (vasp_std) still comes from shared_workflow_settings.yaml.
# export VASP_BINARY_CPU="/global/cfs/cdirs/m526/liangbo/bin/cpu/vasp_std"

# Gamma-only binaries (real-arithmetic, for 1x1x1 k-point runs). Brand new
# variable names -- no legacy stale-shell risk like VASP_BINARY_CPU above.
# Selected per-material via `use_gam: true` in workflow_settings.yaml
# (system_paths.vasp_binary_gam / vasp_binary_gam_cpu); env still wins over
# config if exported, same precedence as VASP_BINARY/VASP_BINARY_CPU.
export VASP_BINARY_GAM="/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_gam"
export VASP_BINARY_GAM_CPU="/global/cfs/cdirs/m526/liangbo/bin/cpu/vasp_gam"

LS_COLORS=$LS_COLORS:'di=1;94:' ; export LS_COLORS

alias sq='squeue -o "%.10i %.30j %.8T %.12M %.12l %.6D %R"'

# ── Multi-node NCCL is configured by the nccl/2.18.3-cu12 module ──────────
# The module sets: NCCL_SOCKET_IFNAME, NCCL_NET, NCCL_NET_GDR_LEVEL,
# NCCL_CROSS_NIC, FI_CXI_DISABLE_HOST_REGISTER, and LD_LIBRARY_PATH.