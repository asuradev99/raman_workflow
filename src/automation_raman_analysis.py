import argparse
import os
import glob
import subprocess
import sys
import time
from util import (
    Tee,
    run_command,
    fmt_time,
    calc_duration,
    ensure_dim_in_conf,
    check_no_selective_dynamics,
    is_calculation_complete,
    merge_config,
    parse_resume_step,
    load_config,
    build_srun_args,
    write_eigenvectors_conf,
    update_wavecar_symlinks,
    update_chgcar_symlinks,
    check_vasp_convergence,
    check_dielectric_complete,
    make_pipeline_excepthook,
    write_kpoints,
    write_incar,
    count_ionic_steps,
    generate_kopia_script,
    inject_ramfile_energies,
    run_relaxation_with_zbrent_retry,
    print_step_header,
    print_step_result,
    split_srun_args,
)
from util import make_write_status, STEP_HISTORY, STEP_DESCRIPTIONS
import shutil

# ── CLI flags via argparse ──────────────────────────────────────────────────
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument(
    "--restart",
    action="store_true",
    help="Clean all generated files and restart pipeline from scratch",
)
parser.add_argument(
    "--cpu", action="store_true", help="Use CPU VASP binary instead of GPU"
)
parser.add_argument(
    "--scratch", action="store_true", help="Run VASP stages on SCRATCH filesystem"
)
args, remaining = parser.parse_known_args()
# Strip parsed flags, keep everything else for any downstream consumer
sys.argv = [sys.argv[0]] + remaining

RESTART_FLAG = args.restart
CPU_FLAG = args.cpu
SCRATCH_FLAG = args.scratch

# ── Config paths (env vars in ~/.bashrc; see CLAUDE.md) ──────────────────────
BASE_PROJECT_DIR = os.environ.get("RAMAN_PROJECT_DIR", "")

if not os.path.isdir(BASE_PROJECT_DIR):
    print(f"Error: BASE_PROJECT_DIR '{BASE_PROJECT_DIR}' does not exist.")
    print("Set the RAMAN_PROJECT_DIR environment variable to your project directory.")
    sys.exit(1)

# ── Bootstrap material dir from CWD ──────────────────────────────────────────
CWD_BASENAME = os.path.basename(os.getcwd())
if CWD_BASENAME not in os.listdir(BASE_PROJECT_DIR):
    print(
        f"Error: Script must be run from a material directory (e.g., MoS2, WS2) inside {BASE_PROJECT_DIR}"
    )
    sys.exit(1)
MATERIAL_DIR = os.path.join(BASE_PROJECT_DIR, CWD_BASENAME)

# ── Preliminary WORK_DIR (refined after config load below) ──────────────────
SCRATCH_BASE = os.environ.get("SCRATCH", "")
if SCRATCH_FLAG:
    if not SCRATCH_BASE:
        print("Error: --scratch flag requires $SCRATCH environment variable.")
        sys.exit(1)
    WORK_DIR = os.path.join(SCRATCH_BASE, "vasp_calculations", CWD_BASENAME)
else:
    WORK_DIR = MATERIAL_DIR

# HFFILES_DIR / RAMAN_DIR — assigned after config load
HFFILES_DIR = None
RAMAN_DIR = None

# workflow.log ALWAYS lives on HOME — it's tiny text, and keeping it on HOME:
#  - survives SCRATCH purges (90-day NERSC retention)
#  - is visible to monitoring scripts (show_status.sh, tail)
#  - makes config-staleness checks meaningful (same filesystem as configs)
STATUS_FILE = os.path.join(MATERIAL_DIR, "workflow.log")
OUTPUT_FILE = os.path.join(MATERIAL_DIR, "workflow.out")

# ── --restart: delete all generated directories, keep input/ + config ────────
if RESTART_FLAG:
    # Clean generated dirs from WORK_DIR (SCRATCH or HOME)
    for dirname in ("scf", "hf", "raman", "output"):
        dp = os.path.join(WORK_DIR, dirname)
        if os.path.exists(dp) and not os.path.islink(dp):
            shutil.rmtree(dp)
            print(f"  [restart] Removed: {dp}/")

    # --scratch: also clean HOME/output/ (final copy destination)
    if SCRATCH_FLAG:
        home_output = os.path.join(MATERIAL_DIR, "output")
        if os.path.exists(home_output) and not os.path.islink(home_output):
            shutil.rmtree(home_output)
            print(f"  [restart] Removed HOME output/: {home_output}")

    # Remove all log files (clean start)
    for log_name in ("workflow.log", "workflow.out", "salloc_output.log"):
        log_path = os.path.join(MATERIAL_DIR, log_name)
        if os.path.exists(log_path):
            os.remove(log_path)
            print(f"  [restart] Removed: {log_path}")

    print(f"  [restart] Done — input/ (including workflow_settings.yaml) preserved.")
    print(f"  [restart] Starting fresh pipeline from step 3...")

# Redirect ALL output AFTER restart cleanup so logs are fresh.
# (Must come after cleanup — otherwise Tee holds file handles open,
#  preventing log files from being properly replaced.)
sys.stdout = Tee(STATUS_FILE, OUTPUT_FILE)

if RESTART_FLAG:
    print("")  # blank line after restart messages

# Binary utilities dir (override via env var)
DEFAULT_BINARY_UTILITIES_DIR = "/global/cfs/cdirs/m526/vasp_binaries/binary_utility"
BINARY_UTILITIES_DIR = os.environ.get(
    "BINARY_UTILITIES_DIR", DEFAULT_BINARY_UTILITIES_DIR
)

# VASP binary path (GPU default; VASP_BINARY_CPU with --cpu)
DEFAULT_VASP_BINARY_GPU = "/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std"
DEFAULT_VASP_BINARY_CPU = "/global/cfs/cdirs/m526/liangbo/bin/cpu/vasp_std"
if CPU_FLAG:
    VASP_BINARY_PATH = os.environ.get("VASP_BINARY_CPU", DEFAULT_VASP_BINARY_CPU)
else:
    VASP_BINARY_PATH = os.environ.get("VASP_BINARY", DEFAULT_VASP_BINARY_GPU)

# Validate VASP binary
if not os.path.isfile(VASP_BINARY_PATH):
    print(f"Error: VASP binary not found at '{VASP_BINARY_PATH}'")
    print("Set the VASP_BINARY environment variable to a valid VASP binary path.")
    print(f"Expected location: {VASP_BINARY_PATH}")
    sys.exit(1)
print(f"VASP binary found: {VASP_BINARY_PATH}")
if CPU_FLAG:
    print(f"  (CPU mode: --cpu flag set)")

if not os.path.isdir(BINARY_UTILITIES_DIR):
    print(f"Error: BINARY_UTILITIES_DIR '{BINARY_UTILITIES_DIR}' does not exist.")
    print("Set the BINARY_UTILITIES_DIR environment variable to a valid directory.")
    sys.exit(1)
print(f"Binary utilities directory found: {BINARY_UTILITIES_DIR}")

# Config inheritance: fallback template → shared YAML → per-material YAML
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(MATERIAL_DIR, "input", "workflow_settings.yaml")
SHARED_CONFIG_PATH = os.path.join(BASE_PROJECT_DIR, "shared_workflow_settings.yaml")
# Fallback template (replaces hardcoded defaults)
FALLBACK_CONFIG_PATH = os.path.join(SCRIPT_DIR, "workflow_settings.yaml")

CONFIG = load_config(
    [
        (FALLBACK_CONFIG_PATH, "fallback"),
        (SHARED_CONFIG_PATH, "shared"),
        (CONFIG_PATH, "per-material"),
    ]
)

# ── Resolve material identity from config ────────────────────────────────────
MATERIAL_NAME = CONFIG.get("name") or CWD_BASENAME
MATERIAL_LABEL = CONFIG.get("material") or MATERIAL_NAME

# Reconstruct MATERIAL_DIR from config name (in case it differs from CWD)
MATERIAL_DIR = os.path.join(BASE_PROJECT_DIR, MATERIAL_NAME)
# Finalize WORK_DIR (--scratch: VASP on $SCRATCH, config on $HOME)
#
# Under --scratch:
#  - input/ is a symlink → $MATERIAL_DIR/input  (read-only, no copy needed)
#  - scf/, hf/, raman/, output/ live on $SCRATCH for fast VASP I/O
#  - workflow.log is ALWAYS on HOME (tiny text, survives SCRATCH purges)
#  - output/ is copied HOME at pipeline end (the important final results)
if SCRATCH_FLAG:
    WORK_DIR = os.path.join(SCRATCH_BASE, "vasp_calculations", MATERIAL_NAME)
    HFFILES_DIR = os.path.join(WORK_DIR, "hf")
    RAMAN_DIR = os.path.join(WORK_DIR, "raman")
    print(f"  [scratch] WORK_DIR = {WORK_DIR}")
    print(f"  [scratch] input/ will be symlinked from HOME (not copied)")
else:
    WORK_DIR = MATERIAL_DIR
    HFFILES_DIR = os.path.join(MATERIAL_DIR, "hf")
    RAMAN_DIR = os.path.join(MATERIAL_DIR, "raman")

# STATUS_FILE always on HOME — see comment at preliminary assignment above
sys.excepthook = make_pipeline_excepthook(STATUS_FILE)

# ── Job start header ──────────────────────────────────────────────────────────
_now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
_cmd = " ".join(sys.argv)
_node = os.uname().nodename
_sep = "\u2550" * 78
print(f"\n{_sep}")
print(f"  RAMAN PIPELINE START")
print(f"{_sep}")
print(f"  Date      : {_now}")
print(f"  Host      : {_node}")
print(f"  Material  : {MATERIAL_LABEL}  ({MATERIAL_NAME})")
print(f"  Work dir  : {WORK_DIR}")
print(f"  Log file  : {STATUS_FILE}")
print(
    f"  Flags     : scratch={'on' if SCRATCH_FLAG else 'off'}  "
    f"restart={'on' if RESTART_FLAG else 'off'}  "
    f"cpu={'on' if CPU_FLAG else 'off'}"
)
print(f"  Command   : python {_cmd}")
print(f"{_sep}\n")

if MATERIAL_NAME != CWD_BASENAME:
    print(
        f"  [config] Config 'name' differs from CWD: '{MATERIAL_NAME}' vs '{CWD_BASENAME}'"
    )
    print(f"  [config] Using config name for paths: {MATERIAL_DIR}")
if not os.path.isdir(MATERIAL_DIR):
    print(
        f"Error: MATERIAL_DIR '{MATERIAL_DIR}' does not exist (from config name '{MATERIAL_NAME}')."
    )
    sys.exit(1)

SRUN_ARGS = build_srun_args(CONFIG, CPU_FLAG)

# ── Unpack config into module-level constants ─────────────────────────────────
PHONOPY_DIM = CONFIG["phonopy"]["dim"]
PHONOPY_AMPLITUDE = CONFIG["phonopy"]["amplitude"]
PHONOPY_BAND_PATH = CONFIG["phonopy"].get(
    "band_path", "0 0 0  0.5 0 0  0.333333 0.333333 0  0 0 0"
)
PHONOPY_BAND_LABELS = CONFIG["phonopy"].get("band_labels", "GAMMA M K GAMMA")
PHONOPY_BAND_POINTS = CONFIG["phonopy"].get("band_points", 101)

DESIRED_ENERGIES = CONFIG["desired_energies"]

RAMAN_INCIDENT_POL = CONFIG["raman_tensor"]["incident_polarization"]
RAMAN_SCATTERED_POL = CONFIG["raman_tensor"]["scattered_polarization"]
RAMAN_SURFACE_NORMAL = CONFIG["raman_tensor"]["surface_normal"]

VASP_MAX_RESTARTS = CONFIG["vasp_loop"]["max_restarts"]
HF_PARALLEL = CONFIG.get("hf_parallel", False)
START_FROM_SUPERCELL = CONFIG.get("start_from_supercell", False)

SCF_KPOINTS_MESH = CONFIG["scf_kpoints"]["mesh"]
SCF_KPOINTS_SHIFT = CONFIG["scf_kpoints"]["shift"]

SUP_RELAX_KPOINTS_MESH = CONFIG["sup_relax_kpoints"]["mesh"]
SUP_RELAX_KPOINTS_SHIFT = CONFIG["sup_relax_kpoints"]["shift"]

HF_KPOINTS_MESH = CONFIG["hf_kpoints"]["mesh"]
HF_KPOINTS_SHIFT = CONFIG["hf_kpoints"]["shift"]

RAMAN_KPOINTS_MESH = CONFIG["raman_kpoints"]["mesh"]
RAMAN_KPOINTS_SHIFT = CONFIG["raman_kpoints"]["shift"]

EIGVEC_BAND_PATH = CONFIG["eigenvectors_band"]["path"]
EIGVEC_BAND_LABELS = CONFIG["eigenvectors_band"]["labels"]
EIGVEC_BAND_POINTS = CONFIG["eigenvectors_band"]["points"]


# Pre-bound write_status (created by util.make_write_status)
write_status = make_write_status(
    STATUS_FILE,
    MATERIAL_LABEL,
    MATERIAL_NAME,
    BASE_PROJECT_DIR,
)


def vasp_loop_check_and_restart(vasp_script_path, max_restarts=3):
    """Run VASP in all hf_POSCAR-* dirs, retry up to max_restarts times."""
    for i in range(max_restarts):
        print(f"\n--- Running VASP iteration {i+1}/{max_restarts} ---")

        all_hf = sorted(
            [
                d
                for d in os.listdir(HFFILES_DIR)
                if d.startswith("hf_POSCAR-")
                and os.path.isdir(os.path.join(HFFILES_DIR, d))
            ]
        )

        if not all_hf:
            # Run orchestration script to create them
            print(
                "  No hf_POSCAR-* dirs found. Running orchestration script to create them..."
            )
            run_command(vasp_script_path, cwd=HFFILES_DIR)
            all_hf = sorted(
                [
                    d
                    for d in os.listdir(HFFILES_DIR)
                    if d.startswith("hf_POSCAR-")
                    and os.path.isdir(os.path.join(HFFILES_DIR, d))
                ]
            )
            if not all_hf:
                print(
                    "  ERROR: orchestration script created no hf_POSCAR-* directories."
                )
                return False

        if CPU_FLAG:
            print(f"  [cpu] Running VASP in {len(all_hf)} directories (CPU mode)...")
            print(f"  [cpu] VASP binary: {VASP_BINARY_PATH}")
            print(f"  [cpu] srun args: {SRUN_ARGS}")
            for d in all_hf:
                dpath = os.path.join(HFFILES_DIR, d)
                print(f"    Running VASP in {d}...")
                run_command(
                    f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > stdout",
                    cwd=dpath,
                )
        elif HF_PARALLEL:
            print(
                f"  [gpu:hf_parallel] Running {len(all_hf)} directories "
                f"in parallel..."
            )
            split_args = split_srun_args(SRUN_ARGS, len(all_hf))
            if not split_args:
                print(
                    "  [gpu:hf_parallel] split_srun_args failed "
                    "— falling back to serial"
                )
                run_command(
                    f"export SRUN_ARGS='{SRUN_ARGS}' && " f"bash {vasp_script_path}",
                    cwd=HFFILES_DIR,
                )
            else:
                procs = []
                for d, sargs in zip(all_hf, split_args):
                    dpath = os.path.join(HFFILES_DIR, d)
                    cmd = f"srun --overlap {sargs} {VASP_BINARY_PATH} > stdout"
                    print(f"    [{d}] srun --overlap {sargs} {VASP_BINARY_PATH}")
                    procs.append(subprocess.Popen(cmd, shell=True, cwd=dpath))
                failed = []
                for d, p in zip(all_hf, procs):
                    rc = p.wait()
                    if rc != 0:
                        failed.append(d)
                        print(f"    [{d}] ERROR: VASP exited with code {rc}")
                if failed:
                    print(
                        f"  [gpu:hf_parallel] {len(failed)}/{len(all_hf)} "
                        f"directories FAILED: {', '.join(failed)}"
                    )
                else:
                    print(
                        f"  [gpu:hf_parallel] All {len(all_hf)} directories "
                        f"completed successfully."
                    )
        else:
            print(f"  [gpu] Running automate_hfiles_fixed.sh (GPU mode)...")
            print(f"  [gpu] srun args: {SRUN_ARGS}")
            run_command(
                f"export SRUN_ARGS='{SRUN_ARGS}' && bash {vasp_script_path}",
                cwd=HFFILES_DIR,
            )

        # Validate ALL displacement runs (not just first)
        hf_dirs = sorted(
            d
            for d in os.listdir(HFFILES_DIR)
            if d.startswith("hf_POSCAR-")
            and os.path.isdir(os.path.join(HFFILES_DIR, d))
        )

        if not hf_dirs:
            print(
                "No hf_POSCAR-* folders found. Check Phonopy displacement generation."
            )
            return False

        failed_dirs = [
            d
            for d in hf_dirs
            if not is_calculation_complete(os.path.join(HFFILES_DIR, d))
        ]
        if not failed_dirs:
            print(
                f"VASP runs completed in all {len(hf_dirs)} displacement directories."
            )
            return True
        else:
            print(
                f"VASP failed or incomplete in {len(failed_dirs)}/{len(hf_dirs)} directories: "
                f"{', '.join(failed_dirs[:5])}{'...' if len(failed_dirs) > 5 else ''}"
            )
            if i + 1 < max_restarts:
                print(f"Retrying ({i+2}/{max_restarts})...")

    print(f"--- VASP loop failed after {max_restarts} attempts. ---")
    return False


# --- Workflow Steps ---

print(
    f"Starting Raman automation for {MATERIAL_LABEL} ({MATERIAL_NAME}) in {MATERIAL_DIR}"
)
print(f"Current working directory: {os.getcwd()}")


# ── Resume: skip completed steps via workflow.log ──────────────────────────
START_STEP = parse_resume_step(STATUS_FILE, STEP_HISTORY, STEP_DESCRIPTIONS)
if START_STEP is None:
    sys.exit(0)

print(f"[resume] START_STEP = {START_STEP} — starting pipeline execution.")

# ── Config staleness warning ─────────────────────────────────────────────────
if START_STEP > 1 and os.path.exists(STATUS_FILE):
    log_mtime = os.path.getmtime(STATUS_FILE)
    for cfg_path, cfg_label in [
        (SHARED_CONFIG_PATH, "shared"),
        (CONFIG_PATH, "per-material"),
    ]:
        if os.path.exists(cfg_path):
            if os.path.getmtime(cfg_path) > log_mtime:
                print(
                    f"WARNING: {cfg_label} config ({os.path.basename(cfg_path)}) "
                    f"was modified after the last pipeline run."
                )

# ── --scratch: symlink input/ from HOME → SCRATCH ───────────────────────────
if SCRATCH_FLAG:
    print(f"\n  [scratch] Linking input/ from HOME to SCRATCH...")
    run_command(f"mkdir -p {WORK_DIR}", cwd=MATERIAL_DIR)
    scratch_input = os.path.join(WORK_DIR, "input")
    if os.path.islink(scratch_input):
        os.unlink(scratch_input)
    elif os.path.isdir(scratch_input):
        shutil.rmtree(scratch_input)
    os.symlink(os.path.join(MATERIAL_DIR, "input"), scratch_input)
    print(f"  [scratch] Symlink created. VASP stages will run in: {WORK_DIR}")
    stale_on_home = []
    for d in ("scf", "hf", "raman"):
        dp = os.path.join(MATERIAL_DIR, d)
        if os.path.exists(dp) and not os.path.islink(dp):
            stale_on_home.append(d)
    if stale_on_home:
        print(f"  [scratch] WARNING: Stale intermediate directories found on HOME:")
        for d in stale_on_home:
            print(f"  [scratch]   {MATERIAL_DIR}/{d}/")

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKFLOW STEPS — dispatched to steps/ modules
# ═══════════════════════════════════════════════════════════════════════════════

from src import STEP_FUNCTIONS, build_context

ctx = build_context(
    write_status,
    CONFIG,
    MATERIAL_DIR,
    MATERIAL_NAME,
    WORK_DIR,
    SRUN_ARGS,
    VASP_BINARY_PATH,
    HFFILES_DIR,
    RAMAN_DIR,
    SCRIPT_DIR,
    BINARY_UTILITIES_DIR,
    CPU_FLAG,
    SCRATCH_FLAG,
    run_relaxation_with_zbrent_retry,
    vasp_loop_check_and_restart,
)

for step_num in sorted(STEP_FUNCTIONS):
    if step_num < START_STEP:
        continue
    fn = STEP_FUNCTIONS[step_num]
    fn_name = fn.__name__.split(".")[-1]
    print(f"\n  [dispatch] Running step {step_num} ({fn_name})")
    fn.run(ctx)
