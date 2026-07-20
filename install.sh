#!/bin/bash
# =============================================================================
#  install.sh — one-time setup for the raman_workflow pipeline on a new
#  cluster/account. Idempotent: safe to re-run, never overwrites existing
#  config or duplicates .bashrc entries.
#
#  What it does:
#    1. Creates $RAMAN_PROJECT_DIR (default ~/vasp_calculations) and a
#       starter shared_workflow_settings.yaml if one isn't already there.
#    2. Appends the required env vars to ~/.bashrc (guarded by a marker so
#       re-running doesn't duplicate them), with cluster-specific values
#       either passed as flags or left as placeholders to edit by hand.
#    3. Checks the phonopy conda env, the VASP module, and PyYAML are
#       actually reachable, and reports clearly what's missing.
#    4. Makes new/*.py and new/*.sh executable.
#
#  What it deliberately does NOT do:
#    - Touch src/, util/, scripts/ (the old pipeline) — this only sets up
#      the new/ pipeline.
#    - Create or modify any per-material directory — that's generate.py's
#      job, per material, on demand.
#    - Overwrite an existing shared_workflow_settings.yaml.
#
#  Usage:
#    ./install.sh [--project-dir DIR] [--conda-env PATH] [--conda-init PATH]
#                  [--vasp-binary PATH] [--vasp-modules "mod1 mod2 ..."]
#                  [--binary-utils DIR]
#
#  Every flag has a Perlmutter-shaped default; pass your cluster's actual
#  values when porting elsewhere, or edit the .bashrc block install.sh
#  writes afterward.
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Defaults (Perlmutter-shaped; override via flags) ────────────────────────
PROJECT_DIR="$HOME/vasp_calculations"
CONDA_INIT="/global/common/software/m3035/conda/etc/profile.d/conda.sh"
CONDA_ENV="/global/common/software/m526/phonopy_env"
VASP_BINARY="/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std"
VASP_MODULES="PrgEnv-nvidia gpu cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu"
BINARY_UTILS="/global/cfs/cdirs/m526/vasp_binaries/binary_utility"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-dir)   PROJECT_DIR="$2"; shift 2 ;;
        --conda-env)     CONDA_ENV="$2"; shift 2 ;;
        --conda-init)    CONDA_INIT="$2"; shift 2 ;;
        --vasp-binary)   VASP_BINARY="$2"; shift 2 ;;
        --vasp-modules)  VASP_MODULES="$2"; shift 2 ;;
        --binary-utils)  BINARY_UTILS="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

echo "=== raman_workflow install ==="
echo "  repo:        $REPO_DIR"
echo "  project dir: $PROJECT_DIR"
echo

# ── 1. Directories ───────────────────────────────────────────────────────
mkdir -p "$PROJECT_DIR"
echo "[1/4] $PROJECT_DIR ready"

SHARED_CFG="$PROJECT_DIR/shared_workflow_settings.yaml"
if [ -s "$SHARED_CFG" ]; then
    echo "      shared_workflow_settings.yaml already exists — leaving it alone"
else
    cat > "$SHARED_CFG" <<EOF
# Shared config, layered under every material's input/workflow_settings.yaml.
# Per-material files override anything here. See new/generate.py's
# merge_config() for the exact merge rule (dicts recurse, scalars/lists replace).

system_paths:
  conda_init: "$CONDA_INIT"
  conda_env: "$CONDA_ENV"
  vasp_modules: "$VASP_MODULES"
  binary_utilities_dir: "$BINARY_UTILS"
  vasp_binary: "$VASP_BINARY"
  # vasp_binary_cpu / vasp_binary_gam / vasp_binary_gam_cpu: fill in if used.

# compute_mode: "interactive" | "sbatch_mix"  -- set per-material, not here,
# unless every material on this cluster shares one mode.

steps: {}
  # Fill in per-step INCAR templates here if most materials share them, e.g.:
  # scf_relax:
  #   kpoints: {mesh: "3 3 1", shift: "0 0 0"}
  #   incar: |
  #     ISTART = 0
  #     ...
EOF
    echo "      wrote starter $SHARED_CFG — edit system_paths and steps for this cluster"
fi

# ── 2. .bashrc env vars ──────────────────────────────────────────────────
BASHRC="$HOME/.bashrc"
MARKER_BEGIN="# >>> raman_workflow install.sh >>>"
MARKER_END="# <<< raman_workflow install.sh <<<"

if [ -f "$BASHRC" ] && grep -qF "$MARKER_BEGIN" "$BASHRC"; then
    echo "[2/4] .bashrc block already present — leaving it alone (edit by hand, or remove the"
    echo "      block between '$MARKER_BEGIN' / '$MARKER_END' and re-run to regenerate)"
else
    {
        echo "$MARKER_BEGIN"
        echo "export PATH=\"\$PATH:$REPO_DIR/scripts\""
        echo "export RAMAN_PROJECT_DIR=\"$PROJECT_DIR\""
        echo "export BINARY_UTILITIES_DIR=\"$BINARY_UTILS\""
        echo "export VASP_BINARY=\"$VASP_BINARY\""
        echo "export VASP_MODULES=\"$VASP_MODULES\""
        echo "# Optional overrides new/generate.py also understands (env wins over config):"
        echo "#   VASP_BINARY_CPU, VASP_BINARY_GAM, VASP_BINARY_GAM_CPU"
        echo "$MARKER_END"
    } >> "$BASHRC"
    echo "[2/4] appended env vars to $BASHRC — run 'source ~/.bashrc' or start a new shell"
fi

# ── 3. Sanity checks ─────────────────────────────────────────────────────
echo "[3/4] checking environment..."
problems=0

if [ -f "$CONDA_INIT" ]; then
    echo "      OK   conda_init found: $CONDA_INIT"
else
    echo "      MISSING conda_init: $CONDA_INIT (edit shared_workflow_settings.yaml / rerun with --conda-init)"
    problems=$((problems + 1))
fi

if [ -d "$CONDA_ENV" ]; then
    echo "      OK   conda env found: $CONDA_ENV"
    if [ -x "$CONDA_ENV/bin/python3" ]; then
        pyver=$("$CONDA_ENV/bin/python3" --version 2>&1)
        echo "      OK   $pyver"
        "$CONDA_ENV/bin/python3" -c "import yaml" 2>/dev/null \
            && echo "      OK   PyYAML importable" \
            || { echo "      MISSING PyYAML in $CONDA_ENV — pip/conda install pyyaml there"; problems=$((problems + 1)); }
    fi
else
    echo "      MISSING conda env: $CONDA_ENV (rerun with --conda-env)"
    problems=$((problems + 1))
fi

if [ -f "$VASP_BINARY" ]; then
    echo "      OK   VASP binary found: $VASP_BINARY"
else
    echo "      MISSING VASP binary: $VASP_BINARY (rerun with --vasp-binary)"
    problems=$((problems + 1))
fi

if [ -d "$BINARY_UTILS" ]; then
    echo "      OK   binary_utilities_dir found: $BINARY_UTILS"
else
    echo "      MISSING binary_utilities_dir: $BINARY_UTILS (rerun with --binary-utils)"
    problems=$((problems + 1))
fi

command -v sbatch >/dev/null 2>&1 \
    && echo "      OK   sbatch on PATH" \
    || { echo "      MISSING sbatch — is this a Slurm cluster login node?"; problems=$((problems + 1)); }

# ── 4. Permissions ───────────────────────────────────────────────────────
chmod +x "$REPO_DIR"/new/*.py "$REPO_DIR"/new/*.sh 2>/dev/null || true
echo "[4/4] new/*.py, new/*.sh executable"

echo
if [ "$problems" -eq 0 ]; then
    echo "=== install OK ==="
else
    echo "=== install finished with $problems problem(s) above — fix before running generate.py ==="
fi
echo
echo "Next steps:"
echo "  1. source ~/.bashrc  (if the env block was just added)"
echo "  2. Edit $SHARED_CFG — system_paths and any shared steps: templates"
echo "  3. For each material: mkdir -p \$RAMAN_PROJECT_DIR/<name>/input, add"
echo "     POSCAR/POTCAR and input/workflow_settings.yaml, then:"
echo "       $CONDA_ENV/bin/python3 $REPO_DIR/new/generate.py \$RAMAN_PROJECT_DIR/<name>"
