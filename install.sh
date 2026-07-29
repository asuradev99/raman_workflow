#!/bin/bash
# =============================================================================
#  install.sh — one-time setup for the raman_workflow pipeline on a new
#  cluster/account. Idempotent: safe to re-run, never overwrites existing
#  config or duplicates .bashrc entries.
#
#  What it does:
#    1. Creates $RAMAN_PROJECT_DIR (default ~/vasp_calculations) and a
#       starter shared_workflow_settings.yaml if one isn't already there.
#    2. Sources <cluster>.bashrc (repo root) for that cluster's system paths,
#       then appends PATH/RAMAN_PROJECT_DIR plus those paths to ~/.bashrc
#       (guarded by a marker so re-running doesn't duplicate them).
#    3. Checks the phonopy conda env, the VASP binary, and PyYAML are
#       actually reachable, and reports clearly what's missing.
#    4. Makes generate.py/check_*.py/runHF executable.
#
#  What it deliberately does NOT do:
#    - Create or modify any per-material directory — that's generate.py's
#      job, per material, on demand.
#    - Overwrite an existing shared_workflow_settings.yaml.
#    - Put any of these paths in shared_workflow_settings.yaml — common.sh
#      and generate.py both read them from ~/.bashrc / the environment
#      only. See <cluster>.bashrc (repo root), the single source of truth
#      per cluster.
#
#  Usage:
#    ./install.sh <nersc|pathfinder> [--project-dir DIR] [--conda-env PATH]
#                  [--conda-init PATH] [--vasp-binary PATH]
#                  [--vasp-modules "mod1 mod2 ..."] [--binary-utils DIR]
#
#  The cluster argument selects <cluster>.bashrc (repo root) for defaults; any
#  flag passed overrides just that one value, for a one-off install without
#  editing the template. To change a cluster's defaults for good, edit
#  <cluster>.bashrc instead.
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTERS_DIR="$REPO_DIR"

if [ $# -eq 0 ] || [[ "$1" == -h || "$1" == --help ]]; then
    sed -n '2,35p' "${BASH_SOURCE[0]}"
    echo
    echo "Available clusters: $(cd "$CLUSTERS_DIR" && ls -- *.bashrc | sed 's/\.bashrc$//' | tr '\n' ' ')"
    exit 0
fi

CLUSTER="$1"; shift
CLUSTER_FILE="$CLUSTERS_DIR/$CLUSTER.bashrc"
if [ ! -f "$CLUSTER_FILE" ]; then
    echo "ERROR: unknown cluster '$CLUSTER' — no $CLUSTER_FILE" >&2
    echo "Available clusters: $(cd "$CLUSTERS_DIR" && ls -- *.bashrc | sed 's/\.bashrc$//' | tr '\n' ' ')" >&2
    exit 1
fi

# ── Defaults for this cluster, from <cluster>.bashrc (repo root) ──────────
PROJECT_DIR="$HOME/vasp_calculations"
source "$CLUSTER_FILE"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-dir)   PROJECT_DIR="$2"; shift 2 ;;
        --conda-env)     CONDA_ENV="$2"; shift 2 ;;
        --conda-init)    CONDA_INIT="$2"; shift 2 ;;
        --vasp-binary)   VASP_BINARY="$2"; shift 2 ;;
        --vasp-modules)  VASP_MODULES="$2"; shift 2 ;;
        --binary-utils)  BINARY_UTILITIES_DIR="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,35p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

echo "=== raman_workflow install ==="
echo "  repo:        $REPO_DIR"
echo "  cluster:     $CLUSTER"
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
# Per-material files override anything here. See generate.py's
# merge_config() for the exact merge rule (dicts recurse, scalars/lists replace).
#
# No system_paths section here: conda/VASP/binary-utility paths live only in
# ~/.bashrc, written by 'install.sh $CLUSTER' from $CLUSTER.bashrc (repo root).
# Both common.sh (bash, job-runtime) and generate.py (Python,
# codegen-time) read them from the environment, not from this file.

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
    echo "      wrote starter $SHARED_CFG — edit steps: for this cluster/material set"
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
        echo "# cluster: $CLUSTER — see raman_workflow/$CLUSTER.bashrc"
        echo "export PATH=\"\$PATH:$REPO_DIR/scripts\""
        echo "export RAMAN_PROJECT_DIR=\"$PROJECT_DIR\""
        echo "export CONDA_INIT=\"$CONDA_INIT\""
        echo "export CONDA_ENV=\"$CONDA_ENV\""
        echo "export VASP_MODULES=\"$VASP_MODULES\""
        echo "export VASP_BINARY=\"$VASP_BINARY\""
        echo "export VASP_BINARY_CPU=\"$VASP_BINARY_CPU\""
        echo "export VASP_BINARY_GAM=\"$VASP_BINARY_GAM\""
        echo "export VASP_BINARY_GAM_CPU=\"$VASP_BINARY_GAM_CPU\""
        echo "export BINARY_UTILITIES_DIR=\"$BINARY_UTILITIES_DIR\""
        echo "export SPECTROPY_DIR=\"$SPECTROPY_DIR\""
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
    echo "      MISSING conda_init: $CONDA_INIT (edit $CLUSTER.bashrc / rerun with --conda-init)"
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
    echo "      MISSING conda env: $CONDA_ENV (edit $CLUSTER.bashrc / rerun with --conda-env)"
    problems=$((problems + 1))
fi

if [ -f "$VASP_BINARY" ]; then
    echo "      OK   VASP binary found: $VASP_BINARY"
else
    echo "      MISSING VASP binary: $VASP_BINARY (edit $CLUSTER.bashrc / rerun with --vasp-binary)"
    problems=$((problems + 1))
fi

if [ -d "$BINARY_UTILITIES_DIR" ]; then
    echo "      OK   binary_utilities_dir found: $BINARY_UTILITIES_DIR"
else
    echo "      MISSING binary_utilities_dir: $BINARY_UTILITIES_DIR (edit $CLUSTER.bashrc / rerun with --binary-utils)"
    problems=$((problems + 1))
fi

command -v sbatch >/dev/null 2>&1 \
    && echo "      OK   sbatch on PATH" \
    || { echo "      MISSING sbatch — is this a Slurm cluster login node?"; problems=$((problems + 1)); }

# ── 4. Permissions ───────────────────────────────────────────────────────
chmod +x "$REPO_DIR"/generate.py "$REPO_DIR"/check_convergence.py "$REPO_DIR"/check_dielectric.py "$REPO_DIR"/runHF 2>/dev/null || true
echo "[4/4] generate.py, check_*.py, runHF executable"

echo
if [ "$problems" -eq 0 ]; then
    echo "=== install OK ==="
else
    echo "=== install finished with $problems problem(s) above — fix before running generate.py ==="
fi
echo
echo "Next steps:"
echo "  1. source ~/.bashrc  (if the env block was just added)"
echo "  2. Edit $SHARED_CFG — steps: templates for this cluster/material set"
echo "  3. For each material: mkdir -p \$RAMAN_PROJECT_DIR/<name>/input, add"
echo "     POSCAR/POTCAR and input/workflow_settings.yaml, then:"
echo "       $CONDA_ENV/bin/python3 $REPO_DIR/generate.py \$RAMAN_PROJECT_DIR/<name>"
