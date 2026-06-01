import os
import re
import glob
import subprocess
import time
import sys
import traceback
import yaml
from util import run_command, _fmt_time, _calc_duration, _ensure_dim_in_conf, _restore_z_lattice_vector
from util import write_status as _util_write_status, _STEP_HISTORY, _STEP_DESCRIPTIONS
import shutil

# ── NBANDS auto-scaling ───────────────────────────────────────────────────────
# Hardcoded NBANDS=64 in input/INCAR fails for large supercells (e.g., 5x5x1
# has NELECT=200, requiring at least 100 bands). This function dynamically
# calculates NBANDS from the primitive cell, POTCAR ZVAL, and phonopy.dim.
def _calculate_nbands(poscar_path, potcar_path, phonopy_dim, buffer_factor=1.3):
    """
    Calculate appropriate NBANDS for a supercell VASP calculation.

    Reads the primitive POSCAR to get atom counts per species, reads POTCAR to
    get ZVAL (valence electrons), then scales by supercell dimensions from
    phonopy.dim. Adds a buffer_factor (>1.0) to provide empty bands.

    Returns:
        int: Recommended NBANDS value, or None if parsing fails.
    """
    # ── Parse POSCAR for atom counts per species ──────────────────────────
    # VASP POSCAR format (without selective dynamics):
    #   line 1: comment
    #   line 2: scale factor
    #   lines 3-5: lattice vectors
    #   line 6: species names (optional)
    #   line 7: atom counts
    # With selective dynamics, an extra line appears after each lattice vector
    # and after the atom-counts line. We handle both cases by finding the first
    # line after the lattice vectors that contains only digits/whitespace.
    try:
        with open(poscar_path) as _f:
            _lines = _f.readlines()
    except (IOError, OSError) as _e:
        print(f"  [nbands] WARNING: Cannot read POSCAR '{poscar_path}': {_e}")
        return None

    # Find atom-counts line: first line after the 3 lattice vectors (lines 2-4,
    # 0-indexed) that contains only digits and whitespace.
    _atom_counts_line = None
    for _i in range(5, len(_lines)):
        _stripped = _lines[_i].strip()
        if _stripped and all(_c.isdigit() or _c.isspace() for _c in _stripped):
            _atom_counts_line = _stripped
            break

    if _atom_counts_line is None:
        print(f"  [nbands] WARNING: Could not find atom-counts line in POSCAR: {poscar_path}")
        return None

    _atom_counts = [int(x) for x in _atom_counts_line.split()]
    _total_atoms_primitive = sum(_atom_counts)

    # ── Parse POTCAR for ZVAL per species ─────────────────────────────────
    # POTCAR contains lines like:
    #   POMASS =   10.811; ZVAL   =    3.000    mass and valenz
    _zvals = []
    try:
        with open(potcar_path) as _f:
            for _line in _f:
                if 'ZVAL' in _line:
                    _match = re.search(r'ZVAL\s*=\s*([\d.]+)', _line)
                    if _match:
                        _zvals.append(float(_match.group(1)))
    except (IOError, OSError) as _e:
        print(f"  [nbands] WARNING: Cannot read POTCAR '{potcar_path}': {_e}")
        return None

    if len(_zvals) != len(_atom_counts):
        print(f"  [nbands] WARNING: Got {len(_zvals)} ZVAL values but {len(_atom_counts)} "
              f"species — cannot calculate NBANDS.")
        return None

    # ── Calculate NELECT in primitive cell ─────────────────────────────────
    _nelect_primitive = sum(_c * _z for _c, _z in zip(_atom_counts, _zvals))

    # ── Parse phonopy.dim ──────────────────────────────────────────────────
    _dim_parts = phonopy_dim.split()
    if len(_dim_parts) < 3:
        print(f"  [nbands] WARNING: phonopy dim '{phonopy_dim}' has < 3 components")
        return None
    try:
        _dim_mult = [int(x) for x in _dim_parts[:3]]
    except ValueError:
        print(f"  [nbands] WARNING: Could not parse phonopy dim components: '{phonopy_dim}'")
        return None
    _supercell_factor = _dim_mult[0] * _dim_mult[1] * _dim_mult[2]

    # ── Calculate NELECT in supercell ──────────────────────────────────────
    _nelect_supercell = _nelect_primitive * _supercell_factor

    # Each band holds 2 electrons (non-spin-polarized).  Add buffer for
    # empty bands (improves convergence and avoids "highest band occupied"
    # warnings).
    _min_nbands = int(_nelect_supercell / 2)
    _nbands = int(_min_nbands * buffer_factor)
    if _nbands <= _min_nbands:
        _nbands = _min_nbands + 1

    # Round up to nearest 16 (convenient divisor for parallelization)
    _nbands = ((_nbands + 15) // 16) * 16

    print(f"  [nbands] Primitive cell: {_total_atoms_primitive} atoms, "
          f"{_nelect_primitive:.0f} electrons")
    print(f"  [nbands] Phonopy dim: {phonopy_dim}  →  supercell factor = {_supercell_factor}")
    print(f"  [nbands] Supercell: ~{_total_atoms_primitive * _supercell_factor} atoms, "
          f"{_nelect_supercell:.0f} electrons → NBANDS = {_nbands} "
          f"(min required: {_min_nbands}, buffer: {buffer_factor})")

    return _nbands


# ── Command-line argument parsing ─────────────────────────────────────────────
# --restart : Delete all generated files and start the pipeline from scratch.
#             Keeps input/ and workflow_settings.yaml intact.
# --cpu     : Use CPU VASP binary and srun arguments instead of GPU defaults.
_RESTART_FLAG = "--restart" in sys.argv
_CPU_FLAG = "--cpu" in sys.argv
if _RESTART_FLAG:
    sys.argv = [a for a in sys.argv if a != "--restart"]
if _CPU_FLAG:
    sys.argv = [a for a in sys.argv if a != "--cpu"]

# --- Configuration ---
# All paths are configurable via environment variables set in ~/.bashrc.
# See CLAUDE.md for the full list of available variables.

# Base path for your projects on Perlmutter.
# Set the RAMAN_PROJECT_DIR environment variable in your ~/.bashrc.
# The script expects to be run from a material subdirectory inside BASE_PROJECT_DIR (e.g., .../MoS2).
_DEFAULT_PROJECT_DIR = ""
BASE_PROJECT_DIR = os.environ.get("RAMAN_PROJECT_DIR", _DEFAULT_PROJECT_DIR)

if not os.path.isdir(BASE_PROJECT_DIR):
    print(f"Error: BASE_PROJECT_DIR '{BASE_PROJECT_DIR}' does not exist.")
    print("Set the RAMAN_PROJECT_DIR environment variable to your project directory.")
    sys.exit(1)

# ── Bootstrap: infer material directory from CWD ──────────────────────────────
# The actual material name and label come from workflow_settings.yaml, but we need
# MATERIAL_DIR first to find that config file. Use the CWD basename as bootstrap.
_CWD_BASENAME = os.path.basename(os.getcwd())
if _CWD_BASENAME not in os.listdir(BASE_PROJECT_DIR):
    print(f"Error: Script must be run from a material directory (e.g., MoS2, WS2) inside {BASE_PROJECT_DIR}")
    sys.exit(1)
MATERIAL_DIR = os.path.join(BASE_PROJECT_DIR, _CWD_BASENAME)
HFFILES_DIR = os.path.join(MATERIAL_DIR, "hf")
RAMAN_DIR = os.path.join(MATERIAL_DIR, "raman")
# [DEEPSEEK 2026-05-27] Global workflow status file (plain text, verbose format)
# Written by write_status() after each major step. Monitor with:
#      watch -n 5 cat $MATERIAL_DIR/workflow_status.txt
STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(MATERIAL_DIR, "workflow_status.txt"))

# ── --restart: Clean all generated files and start fresh ─────────────────────
if _RESTART_FLAG:
    sep = "=" * 80
    print(f"\n{sep}")
    print("  --restart flag detected: Cleaning all generated files...")
    print(f"{sep}\n")

    # Files at MATERIAL_DIR root that may have been generated by previous runs
    _root_clean_files = [
        # Created by Step 3 (initial relaxation)
        "INCAR", "CONTCAR", "POSCAR",
        "CHG", "CHGCAR", "WAVECAR", "OUTCAR", "DOSCAR", "EIGENVAL",
        "vasprun.xml", "vaspout.h5", "REPORT", "OSZICAR",
        "PCDAT", "XDATCAR", "IBZKPT", "relaxation.stdout",
        # Phonopy-generated files at root
        "POSCAR-001", "POSCAR-002", "POSCAR-003", "POSCAR-004",
        "SPOSCAR", "phonopy_disp.yaml",
    ]
    for _f in _root_clean_files:
        _fp = os.path.join(MATERIAL_DIR, _f)
        if os.path.isfile(_fp) or os.path.islink(_fp):
            os.remove(_fp)
            print(f"  Removed: {MATERIAL_DIR}/{_f}")

    # Generated subdirectories (recreated by pipeline steps)
    _clean_dirs = [
        ("scf",   "Step 3 VASP output"),
        ("hf",    "Phonopy displacements + force constants"),
        ("raman", "Raman displacements + VASP + spectra"),
        ("output","Aggregated results (plots, summaries)"),
    ]
    for _dir_name, _purpose in _clean_dirs:
        _dp = os.path.join(MATERIAL_DIR, _dir_name)
        if os.path.exists(_dp):
            shutil.rmtree(_dp)
            print(f"  Removed directory: {MATERIAL_DIR}/{_dir_name}/  ({_purpose})")

    # workflow_status.txt
    if os.path.exists(STATUS_FILE):
        os.remove(STATUS_FILE)
        print(f"  Removed status file: {STATUS_FILE}")

    print(f"\n  [restart] Cleanup complete. input/ and workflow_settings.yaml preserved.")
    print(f"  [restart] Starting fresh pipeline from step 3...\n")

# [DEEPSEEK 2026-05-27] Global exception handler: captures full Python traceback
# into STATUS_FILE when the pipeline crashes with an unhandled exception.
# This ensures the full error (not just exit code) is recorded for debugging.
def _pipeline_excepthook(exc_type, exc_value, exc_tb):
    """Write full traceback to STATUS_FILE on any unhandled exception."""
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    # Print to stderr as usual so the user sees it in the terminal
    print(tb_text, file=sys.stderr)
    # Also append full traceback to the status file
    try:
        with open(STATUS_FILE, "a") as _f:
            _f.write("\n" + "=" * 80 + "\n")
            _f.write("  UNHANDLED EXCEPTION — Full Traceback\n")
            _f.write("=" * 80 + "\n")
            _f.write(tb_text)
            _f.write("=" * 80 + "\n")
    except Exception:
        pass  # best-effort; don't mask the original error

sys.excepthook = _pipeline_excepthook

# Path to your shared utility scripts and compiled binaries
# Set the BINARY_UTILITIES_DIR environment variable to override the default.
_DEFAULT_BINARY_UTILITIES_DIR = "/global/cfs/cdirs/m526/vasp_binaries/binary_utility"
BINARY_UTILITIES_DIR = os.environ.get("BINARY_UTILITIES_DIR", _DEFAULT_BINARY_UTILITIES_DIR)

# Absolute path to the VASP binary (as confirmed by you)
# Two separate env vars:
#   VASP_BINARY     — GPU binary (default)
#   VASP_BINARY_CPU — CPU binary (used with --cpu flag)
# Use --cpu flag to switch to the CPU-compiled binary.
_DEFAULT_VASP_BINARY_GPU = "/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std"
_DEFAULT_VASP_BINARY_CPU = "/global/cfs/cdirs/m526/liangbo/bin/cpu/vasp_std"
if _CPU_FLAG:
    VASP_BINARY_PATH = os.environ.get("VASP_BINARY_CPU", _DEFAULT_VASP_BINARY_CPU)
else:
    VASP_BINARY_PATH = os.environ.get("VASP_BINARY", _DEFAULT_VASP_BINARY_GPU)

# [DEEPSEEK 2026-05-27] Validate VASP binary and utilities directory exist
if not os.path.isfile(VASP_BINARY_PATH):
    print(f"Error: VASP binary not found at '{VASP_BINARY_PATH}'")
    print("Set the VASP_BINARY environment variable to a valid VASP binary path.")
    print(f"Expected location: {VASP_BINARY_PATH}")
    sys.exit(1)
print(f"VASP binary found: {VASP_BINARY_PATH}")
if _CPU_FLAG:
    print(f"  (CPU mode: --cpu flag set)")

if not os.path.isdir(BINARY_UTILITIES_DIR):
    print(f"Error: BINARY_UTILITIES_DIR '{BINARY_UTILITIES_DIR}' does not exist.")
    print("Set the BINARY_UTILITIES_DIR environment variable to a valid directory.")
    sys.exit(1)
print(f"Binary utilities directory found: {BINARY_UTILITIES_DIR}")

# [DEEPSEEK 2026-05-28] Load tunable settings from per-material workflow_settings.yaml
# Each material directory has its own YAML config file, so settings (including name/label)
# can differ per material without modifying the script or a global file.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(MATERIAL_DIR, "workflow_settings.yaml")
# Fallback: if per-material config doesn't exist, try the global one in SCRIPT_DIR
_FALLBACK_CONFIG_PATH = os.path.join(SCRIPT_DIR, "workflow_settings.yaml")

# Default settings (used if config file doesn't exist or a key is missing)
CONFIG = {
    "name": "",
    "material": "",
    "raman_tensor": {
        "incident_polarization": "1.0 0.0 0.0",
        "scattered_polarization": "1.0 0.0 0.0",
        "surface_normal": "z"
    },
    "desired_energies": ["1.96", "2.33"],
    "phonopy": {
        "dim": "4 4 1",
        "amplitude": 0.03
    },
    "vasp_srun": {
        "gpus": 4,
        "ntasks": 4,
        "cpus_per_task": 32,
        "constraint": "gpu"
    },
    "vasp_srun_cpu": {
        "gpus": 0,
        "ntasks": 32,
        "cpus_per_task": 4,
        "constraint": "cpu"
    },
    "vasp_loop": {
        "max_restarts": 3
    },
    "incar_raman": {
        "enabled": True,
        "loptics": True,
        "nbands": 64,
        "nedos": 50001,
        "omegamax": 50
    },
    "hf_kpoints": {
        "mesh": "6 6 1",
        "shift": "0 0 0"
    },
    "eigenvectors_band": {
        "path": "0.0 0.0 0.0  0.5 0.0 0.0  0.333333 0.333333 0.0  0.0 0.0 0.0",
        "labels": "GAMMA M K GAMMA",
        "points": 101
    }
}

# Try per-material config first, then fallback to global
_config_loaded_from = None
for _cfg_path in [CONFIG_PATH, _FALLBACK_CONFIG_PATH]:
    if os.path.exists(_cfg_path):
        try:
            with open(_cfg_path) as f:
                file_config = yaml.safe_load(f)
            if file_config is None:
                file_config = {}
            # Deep-merge: override defaults with file values
            for section, values in file_config.items():
                if section.startswith("_"):
                    continue  # skip metadata keys
                if section in CONFIG and isinstance(CONFIG[section], dict) and isinstance(values, dict):
                    CONFIG[section].update(values)
                elif section in CONFIG:
                    CONFIG[section] = values
            _config_loaded_from = _cfg_path
            print(f"Loaded settings from {_cfg_path}")
            break
        except Exception as e:
            print(f"Warning: Could not load {_cfg_path}: {e}")

if _config_loaded_from is None:
    print("No config file found — using hardcoded defaults.")

# ── Material identity from config ─────────────────────────────────────────────
# Use config's "name" (directory identifier) and "material" (display label).
# Falls back to CWD basename if config doesn't specify them.
MATERIAL_NAME = CONFIG.get("name") or _CWD_BASENAME
MATERIAL_LABEL = CONFIG.get("material") or MATERIAL_NAME

# Reconstruct MATERIAL_DIR from config name (in case it differs from CWD)
MATERIAL_DIR = os.path.join(BASE_PROJECT_DIR, MATERIAL_NAME)
HFFILES_DIR = os.path.join(MATERIAL_DIR, "hf")
RAMAN_DIR = os.path.join(MATERIAL_DIR, "raman")
STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(MATERIAL_DIR, "workflow_status.txt"))

if MATERIAL_NAME != _CWD_BASENAME:
    print(f"  [config] Config 'name' differs from CWD: '{MATERIAL_NAME}' vs '{_CWD_BASENAME}'")
    print(f"  [config] Using config name for paths: {MATERIAL_DIR}")
if not os.path.isdir(MATERIAL_DIR):
    print(f"Error: MATERIAL_DIR '{MATERIAL_DIR}' does not exist (from config name '{MATERIAL_NAME}').")
    sys.exit(1)

# Build srun argument string from config.
# In CPU mode (--cpu), use vasp_srun_cpu settings; otherwise use vasp_srun (GPU).
if _CPU_FLAG:
    _SRUN_GPUS = CONFIG["vasp_srun_cpu"]["gpus"]
    _SRUN_NTASKS = CONFIG["vasp_srun_cpu"]["ntasks"]
    _SRUN_CPUS = CONFIG["vasp_srun_cpu"]["cpus_per_task"]
    _SRUN_CONSTRAINT = CONFIG["vasp_srun_cpu"]["constraint"]
    SRUN_ARGS = (f"--cpu_bind=cores --ntasks {_SRUN_NTASKS} "
                 f"--cpus-per-task {_SRUN_CPUS}")
    print(f"  [cpu] CPU srun args: {SRUN_ARGS}  "
          f"(from config vasp_srun_cpu: ntasks={_SRUN_NTASKS}, "
          f"cpus={_SRUN_CPUS}, constraint={_SRUN_CONSTRAINT})")
else:
    _SRUN_GPUS = CONFIG["vasp_srun"]["gpus"]
    _SRUN_NTASKS = CONFIG["vasp_srun"]["ntasks"]
    _SRUN_CPUS = CONFIG["vasp_srun"]["cpus_per_task"]
    _SRUN_CONSTRAINT = CONFIG["vasp_srun"]["constraint"]
    SRUN_ARGS = (f"--cpu_bind=cores --gpus {_SRUN_GPUS} "
                 f"--ntasks {_SRUN_NTASKS} --cpus-per-task {_SRUN_CPUS} "
                 f"-C {_SRUN_CONSTRAINT}")

# Phonopy settings from config
PHONOPY_DIM = CONFIG["phonopy"]["dim"]
PHONOPY_AMPLITUDE = CONFIG["phonopy"]["amplitude"]
PHONOPY_BAND_PATH = CONFIG["phonopy"].get("band_path", "0 0 0  0.5 0 0  0.333333 0.333333 0  0 0 0")
PHONOPY_BAND_LABELS = CONFIG["phonopy"].get("band_labels", "GAMMA M K GAMMA")
PHONOPY_BAND_POINTS = CONFIG["phonopy"].get("band_points", 101)

# Energy list from config
DESIRED_ENERGIES = CONFIG["desired_energies"]

# Raman tensor polarization geometry (set via workflow_settings.yaml)
RAMAN_INCIDENT_POL = CONFIG["raman_tensor"]["incident_polarization"]
RAMAN_SCATTERED_POL = CONFIG["raman_tensor"]["scattered_polarization"]
RAMAN_SURFACE_NORMAL = CONFIG["raman_tensor"]["surface_normal"]

# VASP loop restart count
VASP_MAX_RESTARTS = CONFIG["vasp_loop"]["max_restarts"]

# INCAR Raman extra settings (LOPTICS, NBANDS, NEDOS, OMEGAMAX)
INCAR_RAMAN_ENABLED = CONFIG["incar_raman"]["enabled"]
INCAR_RAMAN_LOPTICS = CONFIG["incar_raman"]["loptics"]
INCAR_RAMAN_NBANDS = CONFIG["incar_raman"]["nbands"]
INCAR_RAMAN_NEDOS = CONFIG["incar_raman"]["nedos"]
INCAR_RAMAN_OMEGAMAX = CONFIG["incar_raman"]["omegamax"]

# HFfiles KPOINTS — coarse mesh for force-constant calculations
HF_KPOINTS_MESH = CONFIG["hf_kpoints"]["mesh"]
HF_KPOINTS_SHIFT = CONFIG["hf_kpoints"]["shift"]

# Eigenvectors band path — Gamma-only is sufficient for Raman (q=0 property)
EIGVEC_BAND_PATH = CONFIG["eigenvectors_band"]["path"]
EIGVEC_BAND_LABELS = CONFIG["eigenvectors_band"]["labels"]
EIGVEC_BAND_POINTS = CONFIG["eigenvectors_band"]["points"]



# [DEEPSEEK 2026-05-29] write_status() implementation lives in util.py.
# This thin wrapper binds the pipeline-specific global variables so that
# all existing callers (write_status(step, status, message)) continue to
# work without modification.
def write_status(step, status, message=""):
    _util_write_status(
        step, status, message,
        status_file=STATUS_FILE,
        material_label=MATERIAL_LABEL,
        material_name=MATERIAL_NAME,
        base_project_dir=BASE_PROJECT_DIR,
    )


def _is_vasprun_valid(filepath):
    """
    Check that a vasprun.xml file exists, is non-trivial, and was produced by a
    *successful* VASP run.

    VASP crash stubs (e.g., NBANDS insufficiency) can produce vasprun.xml files
    larger than 1000 bytes but truncated — missing the closing ``</modeling>`` tag.
    This function reads the last 4 KB of the file to verify the closing tag exists.

    Returns:
        True if the file exists, has size > 1000 bytes, and contains ``</modeling>``.
    """
    try:
        if not os.path.exists(filepath):
            return False
        size = os.path.getsize(filepath)
        if size <= 1000:
            return False
        # Read the last 4096 bytes where the closing tag should be
        with open(filepath, "rb") as _f:
            if size > 4096:
                _f.seek(-4096, 2)
            _tail = _f.read()
        return b"</modeling>" in _tail
    except (IOError, OSError):
        return False


def vasp_loop_check_and_restart(vasp_script_path, max_restarts=3):
    """
    Runs VASP in all hf_POSCAR-* directories and validates that ALL
    displacement runs produced a genuinely successful vasprun.xml
    (detecting crashes like NBANDS insufficiency via ``</modeling>`` tag).

    In CPU mode (--cpu), runs VASP directly with CPU binary and srun args,
    bypassing the GPU-hardcoded automate_hfiles.sh entirely.

    Retries up to max_restarts times if initial check fails.
    """
    for i in range(max_restarts):
        print(f"\n--- Running VASP iteration {i+1}/{max_restarts} ---")

        # Discover hf_POSCAR-* directories
        _all_hf = sorted([
            d for d in os.listdir(HFFILES_DIR)
            if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(HFFILES_DIR, d))
        ])

        if not _all_hf:
            # No dirs yet — run the orchestration script to create them
            print("  No hf_POSCAR-* dirs found. Running orchestration script to create them...")
            run_command(vasp_script_path, cwd=HFFILES_DIR)
            # Re-discover after script ran
            _all_hf = sorted([
                d for d in os.listdir(HFFILES_DIR)
                if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(HFFILES_DIR, d))
            ])
            if not _all_hf:
                print("  ERROR: orchestration script created no hf_POSCAR-* directories.")
                return False

        if _CPU_FLAG:
            print(f"  [cpu] Running VASP in {len(_all_hf)} directories (CPU mode)...")
            print(f"  [cpu] VASP binary: {VASP_BINARY_PATH}")
            print(f"  [cpu] srun args: {SRUN_ARGS}")
            for _dir in _all_hf:
                _d_path = os.path.join(HFFILES_DIR, _dir)
                print(f"    Running VASP in {_dir}...")
                run_command(
                    f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > stdout",
                    cwd=_d_path,
                )
        else:
            # GPU mode: use the existing orchestration script
            print(f"  [gpu] Running automate_hfiles.sh (GPU mode)...")
            run_command(vasp_script_path, cwd=HFFILES_DIR)

        # Check ALL hf_POSCAR-* directories have a valid vasprun.xml.
        # Checking only the first directory is insufficient: if any displacement VASP run
        # failed, phonopy would silently compute wrong force constants from incomplete data.
        hf_dirs = sorted(
            d for d in os.listdir(HFFILES_DIR)
            if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(HFFILES_DIR, d))
        )

        if not hf_dirs:
            print("No hf_POSCAR-* folders found. Check Phonopy displacement generation.")
            return False

        failed_dirs = [
            d for d in hf_dirs
            if not _is_vasprun_valid(os.path.join(HFFILES_DIR, d, "vasprun.xml"))
        ]

        if not failed_dirs:
            print(f"VASP runs completed in all {len(hf_dirs)} displacement directories.")
            return True
        else:
            print(f"VASP failed or incomplete in {len(failed_dirs)}/{len(hf_dirs)} directories: "
                  f"{', '.join(failed_dirs[:5])}{'...' if len(failed_dirs) > 5 else ''}")
            if i + 1 < max_restarts:
                print(f"Retrying ({i+2}/{max_restarts})...")

    print(f"--- VASP loop failed after {max_restarts} attempts. ---")
    return False

# --- Workflow Steps ---

print(f"Starting Raman automation for {MATERIAL_LABEL} ({MATERIAL_NAME}) in {MATERIAL_DIR}")
print(f"Current working directory: {os.getcwd()}")


# --- Conda Activation (REMOVED FROM HERE - MUST BE DONE MANUALLY BEFORE RUNNING SCRIPT) ---
# You MUST manually activate your Conda environment before running this script:
# Example:
#   module load conda
#   source /global/common/software/conda/new/init_bash
#   conda activate /global/common/software/m526/phonopy_env
# --- End Conda Activation ---

# [DEEPSEEK 2026-05-27] Resume-from-last-step logic:
# If a workflow_status.txt already exists in MATERIAL_DIR, parse it to find
# the last completed step so we don't re-run completed work.
START_STEP = 3  # default: start from step 3 (full pipeline; Steps 1-2 do not exist)

if os.path.exists(STATUS_FILE):
    try:
        with open(STATUS_FILE) as f:
            content = f.read()
        # Find all completed steps
        completed_steps = set()
        for match in re.finditer(r'STEP\s+(\d+)\s+\[\s*COMPLETED\]', content):
            completed_steps.add(int(match.group(1)))
            # Populate _STEP_HISTORY so write_status() knows these are done
            step_num = int(match.group(1))
            _STEP_HISTORY[step_num] = {
                "status": "completed",
                "start_ts": 0,
                "end_ts": 0,
                "message": "Resumed — completed in previous run",
            }
        # Check if any step was running (means it failed — retry from there)
        running_step = None
        for match in re.finditer(r'STEP\s+(\d+)\s+\[\s*RUNNING\]', content):
            running_step = int(match.group(1))
        if running_step is not None:
            START_STEP = running_step
            print(f"[resume] Step {running_step} was RUNNING (likely failed). Retrying from step {running_step}.")
        else:
            # Find the first non-completed step
            all_step_keys = sorted([k for k in _STEP_DESCRIPTIONS if isinstance(k, int)])
            next_step = None
            for s in all_step_keys:
                if s not in completed_steps:
                    next_step = s
                    break
            if next_step is not None:
                START_STEP = next_step
                print(f"[resume] Continuing from step {next_step} ({_STEP_DESCRIPTIONS.get(next_step, 'Unknown')}).")
            else:
                print("[resume] All steps already completed. Nothing to do.")
                sys.exit(0)
    except Exception as e:
        print(f"[resume] Warning: Could not parse {STATUS_FILE}: {e}")
        print("[resume] Starting from step 3 (full pipeline).")
else:
    print(f"[resume] No existing status file at {STATUS_FILE}. Starting from step 3 (full pipeline).")

print(f"[resume] START_STEP = {START_STEP} — starting pipeline execution.")

# ── Step 3: Initial VASP relaxation ──────────────────────────────────────────
if START_STEP <= 3:
    write_status(3, "running", "Initial VASP relaxation")

    _scf_dir = os.path.join(MATERIAL_DIR, "scf")
    run_command(f"mkdir -p {_scf_dir}", cwd=MATERIAL_DIR)

    # 3. Run VASP for initial structure relaxation
    print("\n--- Step 3: Initial VASP relaxation ---")
    # Check if dual-INCAR files exist (INCAR_relax + INCAR_dielec) in input/.
    # If both exist, use the dual-INCAR approach: swap in INCAR_relax for relaxation,
    # then swap back to INCAR_dielec for all subsequent VASP steps.
    # If only a single input/INCAR exists (legacy/John Ornl data), use it directly.
    # [DEEPSEEK 2026-05-28] Updated to read from MATERIAL_DIR/input/ (no root symlinks).
    _input_dir = os.path.join(MATERIAL_DIR, "input")
    has_incar_relax = os.path.exists(os.path.join(_input_dir, "INCAR_relax"))
    has_incar_dielec = os.path.exists(os.path.join(_input_dir, "INCAR_dielec"))
    # [DEEPSEEK 2026-05-28] Copy essential VASP input files from input/ before running.
    # Without this, VASP crashes with "ERROR: the following files does not exist POSCAR"
    # when starting fresh or after --restart (which cleans root but preserves input/).
    for _vasp_input in ("POSCAR", "POTCAR", "KPOINTS"):
        _src = os.path.join(_input_dir, _vasp_input)
        if os.path.exists(_src):
            run_command(f"cp input/{_vasp_input} {_vasp_input}", cwd=MATERIAL_DIR)
        else:
            print(f"  [setup] WARNING: input/{_vasp_input} not found — VASP may fail.")
    if has_incar_relax and has_incar_dielec:
        # [DEEPSEEK 2026-05-27] Dual-INCAR mode: swap in relaxation settings
        run_command(f"cp input/INCAR_relax INCAR", cwd=MATERIAL_DIR)
        run_command(f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > relaxation.stdout",
                    cwd=MATERIAL_DIR)
        # [DEEPSEEK 2026-05-27] Swap back to dielectric INCAR for displacement VASP runs
        run_command(f"cp input/INCAR_dielec INCAR", cwd=MATERIAL_DIR)
    else:
        # Legacy mode: copy single INCAR from input/ to CWD, then run VASP
        run_command(f"cp input/INCAR INCAR", cwd=MATERIAL_DIR)
        run_command(f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > relaxation.stdout",
                    cwd=MATERIAL_DIR)
    # [DEEPSEEK 2026-05-28] Restore original z lattice vector in CONTCAR
    # VASP's ISIF=4 relaxation can shrink the vacuum layer artificially.
    # This reads the original 3rd lattice vector from input/POSCAR and writes
    # it into CONTCAR, preserving the vacuum thickness.
    _restore_z_lattice_vector(MATERIAL_DIR)
    write_status(3, "completed", "Initial VASP relaxation finished")

    # ── Post-Step-3: Move generated VASP files to scf/ ─────────────────────────
    # After VASP relaxation, large output files accumulate at MATERIAL_DIR root.
    # Move all except CONTCAR (needed by Steps 4 and 10) and INCAR (needed by Steps 4 and 10).
    _gen_files = [
        "CHGCAR", "WAVECAR", "OUTCAR", "DOSCAR", "EIGENVAL",
        "vasprun.xml", "vaspout.h5", "CHG", "REPORT", "OSZICAR",
        "PCDAT", "XDATCAR", "IBZKPT", "relaxation.stdout"
    ]
    for _f in _gen_files:
        _src = os.path.join(MATERIAL_DIR, _f)
        if os.path.isfile(_src):
            _dst = os.path.join(_scf_dir, _f)
            run_command(f"mv {_f} scf/", cwd=MATERIAL_DIR)
    # Also move POSCAR if it exists (VASP doesn't overwrite it, but it's an input)
    _poscar = os.path.join(MATERIAL_DIR, "POSCAR")
    if os.path.isfile(_poscar):
        run_command(f"mv POSCAR scf/", cwd=MATERIAL_DIR)
    print("  [cleanup] Moved VASP output files to scf/ (CONTCAR + INCAR kept at root for later steps)")

# ── Step 4: Copy files to hf/ ───────────────────────────────────────────────
if START_STEP <= 4:
    # [DEEPSEEK 2026-05-27] Status tracking added at each step
    write_status(4, "running", "Copy files to hf/")

    # 4. Copy relaxed CONTCAR to hf/ folder (as CONTCAR and POSCAR_unitcell)
    print("\n--- Step 4: Copy CONTCAR as CONTCAR and POSCAR_unitcell to hf/ ---")
    # Create the hf/ directory if it doesn't already exist
    run_command(f"mkdir -p {HFFILES_DIR}", cwd=MATERIAL_DIR)
    # Copy CONTCAR from the main material directory to hf/ as CONTCAR
    run_command(f"cp CONTCAR {HFFILES_DIR}/CONTCAR", cwd=MATERIAL_DIR)
    # Copy CONTCAR from the main material directory to hf/, naming it POSCAR_unitcell
    run_command(f"cp CONTCAR {HFFILES_DIR}/POSCAR_unitcell", cwd=MATERIAL_DIR)
    # Copy VASP input files into hf/ for runHF and phonopy steps
    # [DEEPSEEK 2026-05-28] Read INCAR from MATERIAL_DIR (created by Step 3 swap);
    # read POTCAR + symmetry.conf from input/ (no root symlinks).
    run_command(f"cp INCAR {HFFILES_DIR}/", cwd=MATERIAL_DIR)
    # [DEEPSEEK 2026-05-27] Write coarse KPOINTS for hf/ — forces converge
    # quickly with k-mesh, so use a coarser grid (default 6×6×1) instead of
    # the dense relaxation/raman mesh. Configure via hf_kpoints in workflow_settings.yaml.
    _hf_kpoints_path = os.path.join(HFFILES_DIR, "KPOINTS")
    with open(_hf_kpoints_path, "w") as _hk:
        _hk.write("K-points for force-constant calculation (coarse mesh)\n")
        _hk.write("0\n")
        _hk.write("Gamma\n")
        _hk.write(f"{HF_KPOINTS_MESH}\n")
        _hk.write(f"{HF_KPOINTS_SHIFT}\n")
    print(f"  [setup] Wrote coarse KPOINTS ({HF_KPOINTS_MESH}) to hf/")
    run_command(f"cp input/POTCAR {HFFILES_DIR}/", cwd=MATERIAL_DIR)

    # [DEEPSEEK 2026-06-01] Auto-scale NBANDS for force-constants supercell.
    # The INCAR copied from root has NBANDS=64 (hardcoded for primitive cell).
    # Large supercells (5x5x1, 6x6x1) have many more electrons and need more bands.
    _nbands_hf = _calculate_nbands(
        os.path.join(HFFILES_DIR, "POSCAR_unitcell"),
        os.path.join(HFFILES_DIR, "POTCAR"),
        PHONOPY_DIM,
    )
    if _nbands_hf is not None:
        _hf_incar = os.path.join(HFFILES_DIR, "INCAR")
        with open(_hf_incar) as _f:
            _incar_content = _f.read()
        _incar_content = re.sub(
            r'^\s*NBANDS\s*=\s*\d+',
            f'NBANDS = {_nbands_hf}',
            _incar_content,
            flags=re.MULTILINE,
        )
        with open(_hf_incar, "w") as _f:
            _f.write(_incar_content)
        print(f"  [nbands] Updated NBANDS = {_nbands_hf} in hf/INCAR")

    # symmetry.conf is optional (needed for irrep analysis in Step 8b).
    # Use check_success=False so new materials without it don't crash here.
    if os.path.exists(os.path.join(MATERIAL_DIR, "input", "symmetry.conf")):
        run_command(f"cp input/symmetry.conf {HFFILES_DIR}/", cwd=MATERIAL_DIR)
    else:
        print("  [setup] input/symmetry.conf not found — symmetry/irrep analysis in Step 8b will be skipped.")
    write_status(4, "completed", "Files copied to hf/")

# ── Step 5-6: Phonopy displacements + runHF ──────────────────────────────────
if START_STEP <= 5:
    # [DEEPSEEK 2026-05-27] Status tracking added at each step
    write_status(5, "running", "Phonopy displacement generation")

    # 5. Run phonopy to generate displacements
    print("\n--- Step 5: Generate Phonopy displacements ---")
    # Phonopy will read POSCAR_unitcell in HFFILES_DIR and create POSCAR-001, etc., and phonopy_disp.yaml
    run_command(f"phonopy -d --dim=\"{PHONOPY_DIM}\" --amplitude={PHONOPY_AMPLITUDE} -c POSCAR_unitcell", cwd=HFFILES_DIR)

    # 6. Run "runHF" script to organize folders and setup later analysis
    print("\n--- Step 6: Run runHF to organize displacement folders ---")
    # This script is expected to create hf_POSCAR-XXX subdirectories and copy input files into them.
    run_command(f"{BINARY_UTILITIES_DIR}/runHF", cwd=HFFILES_DIR)
    write_status(5, "completed", "Phonopy displacements + runHF done")
    write_status(6, "completed", "runHF folder organization done")

# ── Step 7: VASP force constants ─────────────────────────────────────────────
if START_STEP <= 7:
    # [DEEPSEEK 2026-05-27] Status tracking added at each step
    write_status(7, "running", "VASP in all hf_POSCAR folders (force constants)")

    # 7. Run VASP in all hf_POSCAR folders
    print("\n--- Step 7: Run VASP in all hf_POSCAR folders ---")
    # This calls automate_hfiles.sh, which handles the srun commands for each displacement.
    # Ensure automate_hfiles.sh has its scancel line commented out for interactive use.
    _vasp7_ok = vasp_loop_check_and_restart(f"{BINARY_UTILITIES_DIR}/automate_hfiles.sh", max_restarts=VASP_MAX_RESTARTS)
    if not _vasp7_ok:
        write_status(7, "failed", "VASP force-constant runs did not complete — check hf_POSCAR-*/stdout files")
        raise RuntimeError(
            f"Step 7 failed: VASP force-constant runs incomplete after {VASP_MAX_RESTARTS} attempts. "
            "Proceeding would cause phonopy to compute wrong force constants from partial data."
        )
    write_status(7, "completed", "VASP force-constant runs finished")

# ── Step 8: Phonon postprocessing ────────────────────────────────────────────
if START_STEP <= 8:
    # [DEEPSEEK 2026-05-27] Status tracking added at each step
    write_status(8, "running", "Phonon postprocessing")

    # In Step 8 of automation_raman_analysis.py:

    # Step 8: phonon_postprocessing equivalent
    # The original phonon_postprocessing script has a hardcoded PATH to johnornl's home dir (inaccessible).
    # We replicate its functionality directly here with proper PATH handling.
    print("\n--- Step 8: Phonon postprocessing (force constants + eigenvectors) ---")

    # PART A: phonon_force — Run "phonopy -f" on all hf_POSCAR-XXX/vasprun.xml files
    print("  [8a] Extracting force constants from VASP runs...")
    # Find highest-numbered hf_POSCAR directory
    hf_dirs = sorted(glob.glob(os.path.join(HFFILES_DIR, "hf_POSCAR-*")))
    if not hf_dirs:
        print("ERROR: No hf_POSCAR-* directories found in hf/.")
        write_status(8, "failed", "No hf_POSCAR-* directories found")
        sys.exit(1)
    # Extract the numeric suffix of the last directory
    last_hf = os.path.basename(hf_dirs[-1])  # e.g. "hf_POSCAR-002"
    N = last_hf.split("-")[-1]               # e.g. "002"
    # Build the brace expansion pattern
    brace_pattern = f"hf_POSCAR-{{001..{N}}}"
    run_command(f"phonopy -f {brace_pattern}/vasprun.xml", cwd=HFFILES_DIR)

    # PART B: phonon_all — Eigenvectors, visualization, symmetry
    print("  [8b] Running phonopy eigenvectors + visualization + symmetry...")

    # ── eigenvectors.conf (band structure + eigenvectors) ──────────────────
    # Must include DIM (supercell size) and BAND (q-point path) so phonopy
    # produces a non-empty band.yaml that phonopy_visualization can read.
    eigen_conf = os.path.join(HFFILES_DIR, "eigenvectors.conf")
    _expected_band = f"BAND = {EIGVEC_BAND_PATH}"
    _needs_recreation = False
    if os.path.exists(eigen_conf):
        with open(eigen_conf) as _ec:
            content = _ec.read()
            if "DIM" not in content:
                _needs_recreation = True
                print("  [8b] eigenvectors.conf exists but lacks DIM — regenerating...")
            elif _expected_band not in content:
                _needs_recreation = True
                print(f"  [8b] eigenvectors.conf BAND has changed — regenerating...")
                print(f"       Expected: {_expected_band}")
            elif "BAND_POINTS" not in content:
                _needs_recreation = True
                print("  [8b] eigenvectors.conf lacks BAND_POINTS — regenerating...")
    else:
        _needs_recreation = True
        print("  [8b] eigenvectors.conf not found — creating...")
    if _needs_recreation:
        with open(eigen_conf, "w") as fc:
            fc.write("# eigenvectors.conf created by automation_raman_analysis.py\n")
            fc.write(f"DIM = {PHONOPY_DIM}\n")
            # Band path for band structure plotting. Configure via
            # eigenvectors_band in workflow_settings.yaml.
            fc.write(f"BAND = {EIGVEC_BAND_PATH}\n")
            if EIGVEC_BAND_LABELS:
                fc.write(f"BAND_LABELS = {EIGVEC_BAND_LABELS}\n")
            fc.write(f"BAND_POINTS = {EIGVEC_BAND_POINTS}\n")
            fc.write("EIGENVECTORS = .TRUE.\n")

    # Run phonopy eigenvectors
    run_command("phonopy -c CONTCAR eigenvectors.conf", cwd=HFFILES_DIR)

    # ── phonopy_visualization (reads band.yaml, writes all_mode.txt) ───────
    # NOTE: This Fortran binary links against CUDA (libcuda.so.1, libcudart.so.12).
    # On CPU nodes (--cpu), CUDA libraries are not available — skip gracefully.
    _pv_cmd = f"export PATH={BINARY_UTILITIES_DIR}:$PATH && echo -e '1\\nno' | phonopy_visualization"
    if _CPU_FLAG:
        run_command(_pv_cmd, cwd=HFFILES_DIR, check_success=False)
    else:
        run_command(_pv_cmd, cwd=HFFILES_DIR)

    # ── symmetry.conf (irreducible representations at Gamma) ────────────────
    # The original file only contains IRREPS = 0 0 0 but lacks DIM.
    # Fix it the same way: ensure DIM is present, preserving existing content.
    sym_conf = os.path.join(HFFILES_DIR, "symmetry.conf")
    if _ensure_dim_in_conf(sym_conf, "symmetry.conf", PHONOPY_DIM):
        run_command("phonopy -c CONTCAR symmetry.conf", cwd=HFFILES_DIR)
        # phonopy_symmetry reads all_mode.txt (produced by phonopy_visualization above).
        # In CPU mode, phonopy_visualization is skipped (CUDA dependency), so
        # all_mode.txt doesn't exist — skip phonopy_symmetry gracefully too.
        _all_mode_path = os.path.join(HFFILES_DIR, "all_mode.txt")
        if _CPU_FLAG and not os.path.exists(_all_mode_path):
            print("  [cpu] phonopy_symmetry skipped (all_mode.txt not available on CPU node)")
        else:
            run_command(f"{BINARY_UTILITIES_DIR}/phonopy_symmetry", cwd=HFFILES_DIR)
    else:
        print("  [8b] symmetry.conf not found — skipping symmetry analysis")

    if not os.path.exists(os.path.join(HFFILES_DIR, "all_mode.txt")):
        print("WARNING: all_mode.txt was not created by phonon postprocessing.")
    write_status(8, "completed", "Phonon postprocessing done")

# ── Step 9: Symmetry analysis ────────────────────────────────────────────────
if START_STEP <= 9:
    write_status(9, "running", "Phonopy symmetry analysis")
    print("\n--- Step 9: Symmetry analysis (completed as part of Step 8 phonon postprocessing) ---")
    # phonopy -c CONTCAR symmetry.conf and phonopy_symmetry were already run in Step 8b.
    write_status(9, "completed", "Symmetry analysis done (irreps.yaml)")

# ── Step 10: Copy CONTCAR + VASP inputs to raman dir ─────────────────────────
if START_STEP <= 10:
    # [DEEPSEEK 2026-05-27] Status tracking added at each step
    write_status(10, "running", "Copy CONTCAR to raman dir")

    # 10. Copy CONTCAR and VASP input files to raman dir
    # runRA creates ra_pos_* directories with symlinks to ../INCAR, ../KPOINTS, ../POTCAR,
    # so these files MUST exist in RAMAN_DIR for VASP to run correctly.
    print("\n--- Step 10: Copy CONTCAR + INCAR + KPOINTS + POTCAR to Raman dir ---")
    # Create the raman directory if it doesn't already exist
    run_command(f"mkdir -p {RAMAN_DIR}", cwd=MATERIAL_DIR)
    run_command(f"cp CONTCAR {RAMAN_DIR}/CONTCAR", cwd=MATERIAL_DIR)
    # [DEEPSEEK 2026-05-28] Copy VASP input files needed for resonant Raman VASP runs
    # INCAR is at MATERIAL_DIR (created by Step 3 swap); KPOINTS + POTCAR are in input/
    run_command(f"cp INCAR {RAMAN_DIR}/", cwd=MATERIAL_DIR)
    run_command(f"cp input/KPOINTS {RAMAN_DIR}/", cwd=MATERIAL_DIR)
    run_command(f"cp input/POTCAR {RAMAN_DIR}/", cwd=MATERIAL_DIR)
    print("  [setup] Copied INCAR (from root), KPOINTS + POTCAR (from input/) to RAMAN_DIR.")

    # [DEEPSEEK 2026-06-01] Auto-scale NBANDS for resonant Raman supercell.
    # The INCAR from root has NBANDS=64 (hardcoded).  Resonant Raman runs VASP on
    # the same supercell as the force-constants step, so the same NBANDS applies.
    _nbands_raman = _calculate_nbands(
        os.path.join(RAMAN_DIR, "CONTCAR"),
        os.path.join(RAMAN_DIR, "POTCAR"),
        PHONOPY_DIM,
    )
    if _nbands_raman is not None:
        _raman_incar = os.path.join(RAMAN_DIR, "INCAR")
        with open(_raman_incar) as _f:
            _incar_content = _f.read()
        _incar_content = re.sub(
            r'^\s*NBANDS\s*=\s*\d+',
            f'NBANDS = {_nbands_raman}',
            _incar_content,
            flags=re.MULTILINE,
        )
        with open(_raman_incar, "w") as _f:
            _f.write(_incar_content)
        print(f"  [nbands] Updated NBANDS = {_nbands_raman} in raman/INCAR")

    # [DEEPSEEK 2026-05-28] Append LOPTICS settings to RAMAN_DIR/INCAR if enabled.
    # Resonant Raman needs LOPTICS=.TRUE. (dielectric function) and sufficient NBANDS.
    if INCAR_RAMAN_ENABLED:
        _raman_incar = os.path.join(RAMAN_DIR, "INCAR")
        _loptics_lines = []
        if INCAR_RAMAN_LOPTICS:
            _loptics_lines.append("LOPTICS = .TRUE.")
        if INCAR_RAMAN_NBANDS:
            _loptics_lines.append(f"NBANDS = {INCAR_RAMAN_NBANDS}")
        if INCAR_RAMAN_NEDOS:
            _loptics_lines.append(f"NEDOS = {INCAR_RAMAN_NEDOS}")
        if INCAR_RAMAN_OMEGAMAX:
            _loptics_lines.append(f"OMEGAMAX = {INCAR_RAMAN_OMEGAMAX}")
        if _loptics_lines:
            # Read existing INCAR
            with open(_raman_incar) as _ri:
                _incar_content = _ri.read()
            # Only append settings whose keys are not already in the INCAR.
            # Use per-line regex to match the key at the start of a line, avoiding
            # false positives from substring matches (e.g., LOPTICS = .FALSE. present
            # but .TRUE. needed would be silently skipped by a plain substring check).
            _lines_to_append = [
                line for line in _loptics_lines
                if not re.search(
                    rf'^\s*{re.escape(line.split("=")[0].strip())}\s*=',
                    _incar_content, re.MULTILINE
                )
            ]
            if _lines_to_append:
                with open(_raman_incar, "a") as _ri:
                    _ri.write("\n")
                    _ri.write("# Resonant Raman settings (appended by automation_raman_analysis.py)\n")
                    for _line in _lines_to_append:
                        _ri.write(f" {_line}\n")
                print(f"  [setup] Appended resonant Raman settings to INCAR in RAMAN_DIR:")
                for _line in _lines_to_append:
                    print(f"           {_line}")
            else:
                print("  [setup] INCAR in RAMAN_DIR already has resonant Raman settings — skipping append.")
    write_status(10, "completed", "CONTCAR and INCAR copied to raman dir")

# ── Step 11: Navigate to Raman dir (always execute — needed for CWD) ────────
# This step must always run because in a fresh job the CWD starts at MATERIAL_DIR.
# We only write the status if we haven't done step 11 yet (START_STEP <= 11).
if START_STEP <= 11:
    # [DEEPSEEK 2026-05-27] Status tracking for Step 11
    write_status(11, "completed", "Navigated to Raman dir")

# 11. Navigate to raman dir for subsequent steps
print("\n--- Step 11: Navigate to Raman dir ---")
os.chdir(RAMAN_DIR)
print(f"Current working directory: {os.getcwd()}")

# ── Step 12: Generate Raman displacements and organize ──────────────────────
if START_STEP <= 12:
    # [DEEPSEEK 2026-05-27] Status tracking for Step 12
    write_status(12, "running", "Generate Raman displacements and organize")

    # 12. Run "ramdiscar", "genRApos610", and "runRA"
    print("\n--- Step 12: Generate Raman displacements and organize ---")

    # Helper: run a CUDA-linked binary in CPU mode without crashing
    def _run_cpu_resilient(cmd, cwd=None, label="binary"):
        if _CPU_FLAG:
            run_command(cmd, cwd=cwd, check_success=False)
        else:
            run_command(cmd, cwd=cwd)

    # ramdiscar: (No explicit input required according to your list)
    # NOTE: ramdiscar links CUDA (libcudart.so.12) — may fail on CPU nodes.
    _run_cpu_resilient(f"{BINARY_UTILITIES_DIR}/ramdiscar", label="ramdiscar")

    # genRApos610: (Requires input 'go')
    # Use absolute path in redirect so the binary runs in RAMAN_DIR (current dir after
    # os.chdir in Step 11), placing ra_pos_* directories where Step 13 expects them.
    # NOTE: genRApos610 links CUDA (libcudart.so.12) — may fail on CPU nodes.
    _go_file = os.path.join(RAMAN_DIR, ".go_input")
    with open(_go_file, "w") as _gf:
        _gf.write("go\n")
    _run_cpu_resilient(f"{BINARY_UTILITIES_DIR}/genRApos610 < {_go_file}", label="genRApos610")
    os.remove(_go_file)

    # runRA: (No explicit input required according to your list, NO CUDA deps)
    run_command(f"{BINARY_UTILITIES_DIR}/runRA")
    write_status(12, "completed", "Raman displacements generated and organized")

# ── Step 13: Resonant VASP ───────────────────────────────────────────────────
if START_STEP <= 13:
    write_status(13, "running", "Resonant VASP runs in all ra_pos_* folders")
    # 13. Run resonant raman calculations (VASP for resonant displacements)
    print("\n--- Step 13: Run resonant Raman calculations ---")
    # This calls run_all_vasp_folders.sh, which handles srun for resonant displacements.
    # [DEEPSEEK 2026-05-27] Use local fixed copy that has scancel commented out.
    # The original at BINARY_UTILITIES_DIR/run_all_vasp_folders.sh has scancel on line 59
    # which would kill the entire batch job prematurely.
    # The fixed copy is in raman_workflow/run_all_vasp_folders_fixed.sh
    # (SCRIPT_DIR already defined at top of script for config loading)
    LOCAL_RUN_ALL_VASP = os.path.join(SCRIPT_DIR, "run_all_vasp_folders_fixed.sh")
    run_command(f"bash {LOCAL_RUN_ALL_VASP}")
    write_status(13, "completed", "Resonant VASP runs finished")

# ── Step 14: Kopia post-processing ───────────────────────────────────────────
if START_STEP <= 14:
    # [DEEPSEEK 2026-05-28] Permanently fixed: dynamically generate kopia script
    # instead of assuming it already exists in RAMAN_DIR (which fails for new
    # materials like PBEsol). The script just copies vasprun.xml from each
    # ra_pos_* directory into AXML/.
    write_status(14, "running", "Kopia post-processing")

    print("\n--- Step 14: Run kopia post-processing ---")
    # Find all ra_pos_* directories that contain vasprun.xml
    _ra_dirs = sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if not _ra_dirs:
        write_status(14, "failed", "No ra_pos_* directories found — Step 12 (runRA) likely failed")
        raise RuntimeError(
            "Step 14 failed: no ra_pos_* directories found in RAMAN_DIR. "
            "Step 12 (runRA) must create these before kopia can run."
        )
    else:
        # Dynamically generate the kopia script
        _kopia_path = os.path.join(RAMAN_DIR, "kopia")
        with open(_kopia_path, "w") as _kf:
            _kf.write("#!/bin/bash\n")
            _kf.write("# Dynamically generated by automation_raman_analysis.py Step 14\n")
            _kf.write("mkdir -p AXML\n")
            for _d in _ra_dirs:
                _dir_name = os.path.basename(_d)
                # genRAram610_dynamic expects "B1a.xml" not "ra_pos_B1a.xml"
                _xml_name = _dir_name[len("ra_pos_"):] if _dir_name.startswith("ra_pos_") else _dir_name
                _kf.write(f'cp "{_dir_name}/vasprun.xml" "AXML/{_xml_name}.xml"\n')
        # Make executable and run
        run_command(f"chmod +x kopia && ./kopia", cwd=RAMAN_DIR)
        write_status(14, "completed", "Kopia post-processing done")

# ── Step 15: RAMFILE generation ──────────────────────────────────────────────
if START_STEP <= 15:
    # [DEEPSEEK 2026-05-29] Uses ramfile_dynamic.sh as the mechanism, but dynamically
    # generates a config-driven copy with laser frequencies from workflow_settings.yaml.
    # This avoids hardcoded energies while keeping the original script structure.
    # File-based stdin redirection is used for reliability with Fortran binaries.
    write_status(15, "running", "RAMFILE generation")

    print("\n--- Step 15: Generate RAMFILE for each desired energy ---")

    # Locate the template script
    _ramfile_script_src = os.path.join(BINARY_UTILITIES_DIR, "ramfile_dynamic.sh")
    if not os.path.exists(_ramfile_script_src):
        raise RuntimeError(
            f"ramfile_dynamic.sh not found at {_ramfile_script_src}. "
            f"Check BINARY_UTILITIES_DIR."
        )

    # Create store directories
    _store_ram = os.path.join(RAMAN_DIR, "store_ramfile")
    _store_eps = os.path.join(RAMAN_DIR, "store_epsilon")
    os.makedirs(_store_ram, exist_ok=True)
    os.makedirs(_store_eps, exist_ok=True)

    # Read the template script
    with open(_ramfile_script_src) as _f:
        _script_template = _f.read()

    # Build the energies array from workflow_settings.yaml config
    _energies_str = " ".join(f'"{e}"' for e in DESIRED_ENERGIES)
    _TEMPLATE_ENERGY_LINE = 'desired_energies=("1.96" "2.33")'
    if _TEMPLATE_ENERGY_LINE not in _script_template:
        raise RuntimeError(
            f"ramfile_dynamic.sh does not contain the expected "
            f"'{_TEMPLATE_ENERGY_LINE}' line — "
            "cannot inject custom energies from workflow_settings.yaml. "
            "Update the expected string in automation_raman_analysis.py if the template changed."
        )
    _script_content = _script_template.replace(
        _TEMPLATE_ENERGY_LINE,
        f"desired_energies=({_energies_str})"
    )

    # Write the configured script to RAMAN_DIR
    _ramfile_script_dst = os.path.join(RAMAN_DIR, "ramfile_dynamic.sh")
    with open(_ramfile_script_dst, "w") as _f:
        _f.write(_script_content)
    os.chmod(_ramfile_script_dst, 0o755)
    print(f"  [setup] Generated ramfile_dynamic.sh with energies: {_energies_str}")

    # Run the script with BINARY_UTILITIES_DIR on PATH so genRAram610_dynamic is found.
    run_command(f"export PATH={BINARY_UTILITIES_DIR}:$PATH && bash ramfile_dynamic.sh", cwd=RAMAN_DIR)

    # Verify all expected RAMFILEs exist
    for _energy in DESIRED_ENERGIES:
        _ramfile = os.path.join(_store_ram, f"RAMFILE_{_energy}")
        if not os.path.exists(_ramfile):
            raise RuntimeError(
                f"Step 15 failed: ramfile_dynamic.sh produced no RAMFILE_{_energy}. "
                f"Cannot continue without required RAMFILE for energy {_energy} eV."
            )

    write_status(15, "completed", "RAMFILE generation done")

# ── Step 16: RAMFILE confirmation ────────────────────────────────────────────
if START_STEP <= 16:
    # [DEEPSEEK 2026-05-27] Status tracking added at each step
    write_status(16, "completed", "RAMFILE confirmation")

    # 16. Confirmation for RAMFILEs
    print("\n--- Step 16: RAMFILE_X.X files should be in 'store_ramfile' within Raman dir. ---")

# ── Step 17: Copy static files to Raman dir + output dir ─────────────────────
if START_STEP <= 17:
    # --- Step 17: Copy static Band/Irreps files to Raman dir (only once) ---
    # Moved these commands outside the energy loop, as they are static copies regardless of energy.
    print("\n--- Step 17: Copying static Band/Irreps files to Raman dir + output dir ---")
    # Ensure these files were created in HFFILES_DIR by Step 9.
    # Using check_success=False to prevent script failure if files already exist from a previous partial run.
    run_command(f"cp {HFFILES_DIR}/band.yaml .", cwd=RAMAN_DIR, check_success=False)
    run_command(f"cp {HFFILES_DIR}/irreps.yaml .", cwd=RAMAN_DIR, check_success=False)
    # [DEEPSEEK 2026-05-28] Also copy band.yaml to output/ for easy access
    _output_dir = os.path.join(MATERIAL_DIR, "output")
    run_command(f"mkdir -p {_output_dir}", cwd=MATERIAL_DIR)
    run_command(f"cp {HFFILES_DIR}/band.yaml {_output_dir}/", cwd=MATERIAL_DIR, check_success=False)
    write_status(17, "completed", "Static band/irreps files copied; band.yaml placed in output/")

# ── Steps 18-20: Energy processing loop ──────────────────────────────────────
if START_STEP <= 18:
    # [DEEPSEEK 2026-05-27] Status tracking — mark Step 18 as running before the energy loop

    # --- Step 18-20: Process each energy from ramfile_dynamic.sh output ---
    print("\n--- Step 18-20: Processing Raman results for each energy ---")
    # [DEEPSEEK 2026-05-27] Per-energy results accumulated and written after the loop
    # (Avoids overwriting status for each energy iteration)
    write_status(18, "running", f"Processing energies: {', '.join(DESIRED_ENERGIES)} eV")

    # Define the list of desired energies. This must match what ramfile_dynamic.sh produces.
    # Update this list if ramfile_dynamic.sh changes its output energies.
    # We'll expect these to be in RAMAN_DIR/store_ramfile/
    desired_energies = DESIRED_ENERGIES

    # [DEEPSEEK 2026-05-29] Validate RAMFILEs exist before processing.
    # If any are missing, fail immediately — Step 15 is responsible for
    # generating them, and silent failures should crash the pipeline.
    _store_ram_step18 = os.path.join(RAMAN_DIR, "store_ramfile")
    for _energy in desired_energies:
        _ramfile_check = os.path.join(_store_ram_step18, f"RAMFILE_{_energy}")
        if not os.path.exists(_ramfile_check):
            raise RuntimeError(
                f"RAMFILE_{_energy} not found in store_ramfile/. "
                f"Step 15 did not produce the required RAMFILE for energy {_energy} eV. "
                f"Cannot continue."
            )

    for energy in desired_energies:
        print(f"\n--- Processing for energy: {energy}eV ---")

        # A. Copy the energy-specific RAMFILE to working directory
        run_command("rm -f RAMFILE", cwd=RAMAN_DIR, check_success=False)
        run_command(f"cp store_ramfile/RAMFILE_{energy} RAMFILE", cwd=RAMAN_DIR)

        # B. Run raman_tensor script
        # [DEEPSEEK 2026-05-27] raman_tensor prompts for:
        #   1. Polarization of incident light  (3 floats)
        #   2. Polarization of scattered light (3 floats)
        #   3. Surface normal direction (x, y, or z)
        # Values come from workflow_settings.yaml — edit that file to change geometry.
        # [DEEPSEEK 2026-05-28] Use file-based stdin redirection for reliability
        # with Fortran binaries (avoid subprocess.PIPE buffering issues).
        _raman_input = f"{RAMAN_INCIDENT_POL}\n{RAMAN_SCATTERED_POL}\n{RAMAN_SURFACE_NORMAL}\n"
        _rt_file = os.path.join(RAMAN_DIR, ".raman_tensor_input")
        with open(_rt_file, "w") as _rf:
            _rf.write(_raman_input)
        # Suppress stdout only (to hide the interactive-prompt echoes), but keep
        # stderr visible so genuine error messages from the binary reach the log.
        # NOTE: raman_tensor links CUDA (libcudart.so.12) — may fail on CPU nodes.
        if _CPU_FLAG:
            run_command(
                f"{BINARY_UTILITIES_DIR}/raman_tensor < .raman_tensor_input > /dev/null",
                cwd=RAMAN_DIR, check_success=False
            )
        else:
            run_command(
                f"{BINARY_UTILITIES_DIR}/raman_tensor < .raman_tensor_input > /dev/null",
                cwd=RAMAN_DIR
            )
        os.remove(_rt_file)
        # [DEEPSEEK 2026-05-27] Per-energy result printed (status written once after loop)
        print(f"    [energy {energy}eV] Raman tensor computed with pol=({RAMAN_INCIDENT_POL},{RAMAN_SCATTERED_POL})")

        # C. Run broadening script
        # [DEEPSEEK 2026-05-28] Fix: raman_tensor creates broadening_input but leaves it empty (0 bytes).
        # The broadening binary reads this file for its configuration. Write the correct content here.
        _b_input = os.path.join(RAMAN_DIR, "broadening_input")
        _b_content = (
            "Raman_intensity_complex  !!! the file name\n"
            "2            !!! peak broadening mode (1 for Gaussian, 2 for Lorentzian)\n"
            "1            !!! half width at half maximum (cm-1)\n"
            "200          !!! number of data points inserted between two old data points\n"
            "2            !!! normalization\n"
        )
        with open(_b_input, "w") as _bf:
            _bf.write(_b_content)
        print(f"  [setup] Wrote broadening_input for {energy}eV")
        run_command(f"{BINARY_UTILITIES_DIR}/broadening", cwd=RAMAN_DIR)

        # D. Rename the output files with energy suffix
        # Check if files exist before renaming to avoid errors if a previous step failed.
        if os.path.exists(os.path.join(RAMAN_DIR, "Raman_intensity_complex")):
            run_command(f"mv Raman_intensity_complex Raman_intensity_complex_{energy}eV", cwd=RAMAN_DIR)
        else:
            print(f"WARNING: Raman_intensity_complex not found for {energy}eV.")

        if os.path.exists(os.path.join(RAMAN_DIR, "Raman_intensity_complex_broadening")):
            run_command(f"mv Raman_intensity_complex_broadening Raman_intensity_complex_broadening_{energy}eV", cwd=RAMAN_DIR)
        else:
            print(f"WARNING: Raman_intensity_complex_broadening not found for {energy}eV.")

    print("\n--- Automation workflow complete. ---")

    # [DEEPSEEK 2026-05-27] Mark Step 18 completed (all energies processed in loop)
    write_status(18, "completed", f"Raman tensor computed for all energies: {', '.join(DESIRED_ENERGIES)} eV")
    write_status(20, "completed", f"All energies processed: {', '.join(DESIRED_ENERGIES)} eV")

    # [DEEPSEEK 2026-05-27] Mark overall pipeline as completed
    write_status("final", "completed", "Automation workflow complete")

# --- NEW STEP: Self-cancel salloc job ---
# This attempts to terminate the salloc job automatically.
# Be aware of potential issues with self-cancellation as discussed.
# [DEEPSEEK 2026-05-27] Only run end_salloc.sh if not in a batch job
# In batch mode, the job exits naturally and our trap in run_raman_pipeline.sbatch handles cleanup.
if "SLURM_JOB_ID" in os.environ and os.environ.get("SLURM_SUBMIT_HOST", "") == "":
    # Running in salloc interactive mode — try to self-cancel
    run_command(f"{BINARY_UTILITIES_DIR}/end_salloc.sh", check_success=False)
else:
    print("Batch job mode detected — skipping end_salloc.sh (job will exit naturally).")

# The script might exit immediately after this, or print the final message if scancel takes a moment.
