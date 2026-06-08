import argparse
import os
import glob
import subprocess
import sys
import time
from util import (Tee, run_command, fmt_time, calc_duration, ensure_dim_in_conf,
                  check_no_selective_dynamics,
                  is_calculation_complete, merge_config,
                  parse_resume_step, load_config, build_srun_args,
                  write_eigenvectors_conf, update_wavecar_symlinks,
                  update_chgcar_symlinks, check_vasp_convergence,
                  check_dielectric_complete,
                  make_pipeline_excepthook, write_kpoints, write_incar,
                  count_ionic_steps,
                  generate_kopia_script, inject_ramfile_energies,
                  run_relaxation_with_zbrent_retry,
                  print_step_header, print_step_result,
                  split_srun_args)
from util import make_write_status, STEP_HISTORY, STEP_DESCRIPTIONS
import shutil

# ── CLI flags via argparse ──────────────────────────────────────────────────
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--restart', action='store_true',
                    help='Clean all generated files and restart pipeline from scratch')
parser.add_argument('--cpu', action='store_true',
                    help='Use CPU VASP binary instead of GPU')
parser.add_argument('--scratch', action='store_true',
                    help='Run VASP stages on SCRATCH filesystem')
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
    print(f"Error: Script must be run from a material directory (e.g., MoS2, WS2) inside {BASE_PROJECT_DIR}")
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

# Redirect ALL output early so workflow.out captures everything printed
# (config loading, --restart messages, scratch setup, etc.).
sys.stdout = Tee(STATUS_FILE, OUTPUT_FILE)

# ── --restart: delete all generated directories, keep input/ + config ────────
if RESTART_FLAG:
    sep = "=" * 80
    print(f"\n{sep}")
    print("  --restart flag detected: Deleting all generated output...")
    print(f"{sep}\n")

    # Clean generated dirs from WORK_DIR (SCRATCH or HOME)
    for dirname in ("scf", "hf", "raman", "output"):
        dp = os.path.join(WORK_DIR, dirname)
        if os.path.exists(dp) and not os.path.islink(dp):
            shutil.rmtree(dp)
            print(f"  Removed: {dp}/")

    # --scratch: also clean HOME/output/ (final copy destination)
    if SCRATCH_FLAG:
        home_output = os.path.join(MATERIAL_DIR, "output")
        if os.path.exists(home_output) and not os.path.islink(home_output):
            shutil.rmtree(home_output)
            print(f"  Removed HOME output/: {home_output}")

    # Remove workflow.log from HOME (always there now)
    if os.path.exists(STATUS_FILE):
        os.remove(STATUS_FILE)
        print(f"  Removed: {STATUS_FILE}")

    print(f"\n  [restart] Done — input/ (including workflow_settings.yaml) preserved.")
    print(f"  [restart] Starting fresh pipeline from step 3...\n")

# Binary utilities dir (override via env var)
DEFAULT_BINARY_UTILITIES_DIR = "/global/cfs/cdirs/m526/vasp_binaries/binary_utility"
BINARY_UTILITIES_DIR = os.environ.get("BINARY_UTILITIES_DIR", DEFAULT_BINARY_UTILITIES_DIR)

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

CONFIG = load_config([
    (FALLBACK_CONFIG_PATH, "fallback"),
    (SHARED_CONFIG_PATH,   "shared"),
    (CONFIG_PATH,          "per-material"),
])

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
print(f"  Flags     : scratch={'on' if SCRATCH_FLAG else 'off'}  "
      f"restart={'on' if RESTART_FLAG else 'off'}  "
      f"cpu={'on' if CPU_FLAG else 'off'}")
print(f"  Command   : python {_cmd}")
print(f"{_sep}\n")

if MATERIAL_NAME != CWD_BASENAME:
    print(f"  [config] Config 'name' differs from CWD: '{MATERIAL_NAME}' vs '{CWD_BASENAME}'")
    print(f"  [config] Using config name for paths: {MATERIAL_DIR}")
if not os.path.isdir(MATERIAL_DIR):
    print(f"Error: MATERIAL_DIR '{MATERIAL_DIR}' does not exist (from config name '{MATERIAL_NAME}').")
    sys.exit(1)

SRUN_ARGS = build_srun_args(CONFIG, CPU_FLAG)

# ── Unpack config into module-level constants ─────────────────────────────────
PHONOPY_DIM = CONFIG["phonopy"]["dim"]
PHONOPY_AMPLITUDE = CONFIG["phonopy"]["amplitude"]
PHONOPY_BAND_PATH = CONFIG["phonopy"].get("band_path", "0 0 0  0.5 0 0  0.333333 0.333333 0  0 0 0")
PHONOPY_BAND_LABELS = CONFIG["phonopy"].get("band_labels", "GAMMA M K GAMMA")
PHONOPY_BAND_POINTS = CONFIG["phonopy"].get("band_points", 101)

DESIRED_ENERGIES = CONFIG["desired_energies"]

RAMAN_INCIDENT_POL = CONFIG["raman_tensor"]["incident_polarization"]
RAMAN_SCATTERED_POL = CONFIG["raman_tensor"]["scattered_polarization"]
RAMAN_SURFACE_NORMAL = CONFIG["raman_tensor"]["surface_normal"]

VASP_MAX_RESTARTS = CONFIG["vasp_loop"]["max_restarts"]
HF_PARALLEL = CONFIG.get("hf_parallel", False)

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
    STATUS_FILE, MATERIAL_LABEL, MATERIAL_NAME, BASE_PROJECT_DIR,
)



def vasp_loop_check_and_restart(vasp_script_path, max_restarts=3):
    """Run VASP in all hf_POSCAR-* dirs, retry up to max_restarts times."""
    for i in range(max_restarts):
        print(f"\n--- Running VASP iteration {i+1}/{max_restarts} ---")

        all_hf = sorted([
            d for d in os.listdir(HFFILES_DIR)
            if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(HFFILES_DIR, d))
        ])

        if not all_hf:
            # Run orchestration script to create them
            print("  No hf_POSCAR-* dirs found. Running orchestration script to create them...")
            run_command(vasp_script_path, cwd=HFFILES_DIR)
            all_hf = sorted([
                d for d in os.listdir(HFFILES_DIR)
                if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(HFFILES_DIR, d))
            ])
            if not all_hf:
                print("  ERROR: orchestration script created no hf_POSCAR-* directories.")
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
            print(f"  [gpu:hf_parallel] Running {len(all_hf)} directories "
                  f"in parallel...")
            split_args = split_srun_args(SRUN_ARGS, len(all_hf))
            if not split_args:
                print("  [gpu:hf_parallel] split_srun_args failed "
                      "— falling back to serial")
                run_command(
                    f"export SRUN_ARGS='{SRUN_ARGS}' && "
                    f"bash {vasp_script_path}",
                    cwd=HFFILES_DIR,
                )
            else:
                procs = []
                for d, sargs in zip(all_hf, split_args):
                    dpath = os.path.join(HFFILES_DIR, d)
                    cmd = f"srun --overlap {sargs} {VASP_BINARY_PATH} > stdout"
                    print(f"    [{d}] srun --overlap {sargs} {VASP_BINARY_PATH}")
                    procs.append(subprocess.Popen(
                        cmd, shell=True, cwd=dpath))
                failed = []
                for d, p in zip(all_hf, procs):
                    rc = p.wait()
                    if rc != 0:
                        failed.append(d)
                        print(f"    [{d}] ERROR: VASP exited with code {rc}")
                if failed:
                    print(f"  [gpu:hf_parallel] {len(failed)}/{len(all_hf)} "
                          f"directories FAILED: {', '.join(failed)}")
                else:
                    print(f"  [gpu:hf_parallel] All {len(all_hf)} directories "
                          f"completed successfully.")
        else:
            print(f"  [gpu] Running automate_hfiles_fixed.sh (GPU mode)...")
            print(f"  [gpu] srun args: {SRUN_ARGS}")
            run_command(
                f"export SRUN_ARGS='{SRUN_ARGS}' && bash {vasp_script_path}",
                cwd=HFFILES_DIR,
            )

        # Validate ALL displacement runs (not just first)
        hf_dirs = sorted(
            d for d in os.listdir(HFFILES_DIR)
            if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(HFFILES_DIR, d))
        )

        if not hf_dirs:
            print("No hf_POSCAR-* folders found. Check Phonopy displacement generation.")
            return False

        failed_dirs = [
            d for d in hf_dirs
            if not is_calculation_complete(os.path.join(HFFILES_DIR, d))
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


# ── Resume: skip completed steps via workflow_status.txt ──────────────────────
START_STEP = parse_resume_step(STATUS_FILE, STEP_HISTORY, STEP_DESCRIPTIONS)
if START_STEP is None:
    sys.exit(0)

print(f"[resume] START_STEP = {START_STEP} — starting pipeline execution.")

# ── Config staleness warning ─────────────────────────────────────────────────
# Warn if either shared or per-material config was modified after the last run.
if START_STEP > 3 and os.path.exists(STATUS_FILE):
    log_mtime = os.path.getmtime(STATUS_FILE)
    for cfg_path, cfg_label in [(SHARED_CONFIG_PATH, "shared"),
                                  (CONFIG_PATH, "per-material")]:
        if os.path.exists(cfg_path):
            if os.path.getmtime(cfg_path) > log_mtime:
                print(f"WARNING: {cfg_label} config ({os.path.basename(cfg_path)}) "
                      f"was modified after the last pipeline run.")
                print(f"         Verify settings are consistent with completed steps, or use --restart.")

# ── --scratch: symlink input/ from HOME → SCRATCH ───────────────────────────
if SCRATCH_FLAG:
    print(f"\n  [scratch] Linking input/ from HOME to SCRATCH...")
    print(f"  [scratch] Source: {MATERIAL_DIR}/input")
    print(f"  [scratch] Target: {WORK_DIR}/input (symlink)")
    run_command(f"mkdir -p {WORK_DIR}", cwd=MATERIAL_DIR)

    # Remove stale symlink or directory if it exists (from previous run or old copy)
    scratch_input = os.path.join(WORK_DIR, "input")
    if os.path.islink(scratch_input):
        os.unlink(scratch_input)
    elif os.path.isdir(scratch_input):
        shutil.rmtree(scratch_input)

    os.symlink(os.path.join(MATERIAL_DIR, "input"), scratch_input)
    print(f"  [scratch] Symlink created. VASP stages will run in: {WORK_DIR}")

    # Guard: warn if any intermediate dirs exist on HOME (stale from old runs)
    # Under --scratch, scf/, hf/, and raman/ should only exist on $SCRATCH.
    stale_on_home = []
    for d in ("scf", "hf", "raman"):
        dp = os.path.join(MATERIAL_DIR, d)
        if os.path.exists(dp) and not os.path.islink(dp):
            stale_on_home.append(d)
    if stale_on_home:
        print(f"  [scratch] WARNING: Stale intermediate directories found on HOME:")
        for d in stale_on_home:
            print(f"  [scratch]   {MATERIAL_DIR}/{d}/")
        print(f"  [scratch] These are NOT used by the pipeline (all VASP I/O is on $SCRATCH).")
        print(f"  [scratch] Remove with: rm -rf {' '.join(os.path.join(MATERIAL_DIR, d) for d in stale_on_home)}")

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKFLOW STEPS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Step 3: VASP relaxation ──────────────────────────────────────────────────
if START_STEP <= 3:
    print_step_header(3)
    write_status(3, "running", "Initial VASP relaxation")
    _t0 = time.time()

    # VASP runs in scf/; no INCAR swap needed — downstream steps generate fresh
    scf_dir = os.path.join(WORK_DIR, "scf")
    run_command(f"mkdir -p {scf_dir}", cwd=WORK_DIR)

    input_dir = os.path.join(MATERIAL_DIR, "input")
    # Validate and copy POSCAR + POTCAR from input/ (KPOINTS generated from config below)
    for vasp_input in ("POSCAR", "POTCAR"):
        src = os.path.join(input_dir, vasp_input)
        if not os.path.exists(src):
            raise FileNotFoundError(
                f"input/{vasp_input} not found at {src}. "
                f"Place the file in the material's input/ directory before running the pipeline."
            )
        run_command(f"cp input/{vasp_input} scf/{vasp_input}", cwd=WORK_DIR)
    write_kpoints(os.path.join(scf_dir, "KPOINTS"),
                  "K-points for unit cell SCF", SCF_KPOINTS_MESH, SCF_KPOINTS_SHIFT)
    print(f"  [setup] Wrote unit cell KPOINTS ({SCF_KPOINTS_MESH}) to scf/")
    write_incar(os.path.join(WORK_DIR, "scf", "INCAR"), CONFIG, "relax")
    # Check POTCAR exists before running VASP
    if not os.path.exists(os.path.join(scf_dir, "POTCAR")):
        raise FileNotFoundError(
            f"POTCAR not found in {scf_dir}. Cannot run VASP without a pseudopotential."
        )
    if not run_relaxation_with_zbrent_retry(
        scf_dir, SRUN_ARGS, VASP_BINARY_PATH,
        stage_label="step-3",
    ):
        msg = (f"VASP relaxation failed after max retries in {scf_dir}. "
               f"Check {scf_dir}/relaxation.stdout for details.")
        print_step_result(3, ok=False, duration_s=time.time() - _t0, message="Relaxation failed")
        raise RuntimeError(msg)
    write_status(3, "completed", "Initial VASP relaxation finished")
    print_step_result(3, ok=True, duration_s=time.time() - _t0)


# ── Step 4: Generate supercell + ionic relaxation ────────────────────────────
# The supercell generated from the relaxed unit cell (Step 3) should be near
# equilibrium.  This step runs a single VASP ionic relaxation (NSW=100, ISIF=3
# with LATTICE_CONSTRAINTS fixing z) to verify the supercell is in a stable
# state and to produce CHGCAR + WAVECAR for seeding downstream force-constant
# calculations.
if START_STEP <= 4:
    print_step_header(4)
    write_status(4, "running", "Supercell generation + ionic relaxation")
    _t0 = time.time()

    groundstate_dir = os.path.join(HFFILES_DIR, "groundstate")
    run_command(f"mkdir -p {groundstate_dir}", cwd=WORK_DIR)

    # 4a. Generate supercell from relaxed unit cell (in hf/ — shared with Step 6)
    print("  [setup] Creating supercell in hf/ via phonopy (shared with force-constant step)...")
    run_command(f"cp scf/CONTCAR {HFFILES_DIR}/POSCAR_unitcell", cwd=WORK_DIR)
    check_no_selective_dynamics(
        os.path.join(HFFILES_DIR, "POSCAR_unitcell"),
        "POSCAR_unitcell — source for all phonopy supercell displacements"
    )
    run_command(
        f"phonopy -d --dim=\"{PHONOPY_DIM}\" --amplitude={PHONOPY_AMPLITUDE} "
        f"-c POSCAR_unitcell",
        cwd=HFFILES_DIR,
    )
    # SPOSCAR = perfect supercell; copy to groundstate/ as starting structure
    sposcar_src = os.path.join(HFFILES_DIR, "SPOSCAR")
    if not os.path.exists(sposcar_src):
        raise FileNotFoundError(
            f"SPOSCAR not found in {HFFILES_DIR}. "
            f"Phonopy supercell generation (phonopy -d) failed in Step 4a."
        )
    run_command(f"cp SPOSCAR {groundstate_dir}/POSCAR", cwd=HFFILES_DIR)
    print("  [setup] POSCAR-* displacement files + SPOSCAR now in hf/ — Step 6 will reuse them")

    # 4b. Set up + run supercell ionic relaxation (NSW=100, ISIF=3, 2D-constrained)
    print("  [vasp] Setting up supercell ionic relaxation (NSW=100, ISIF=3, 2D-constrained)...")
    write_incar(os.path.join(groundstate_dir, "INCAR"), CONFIG, "supercell_relax")
    write_kpoints(os.path.join(groundstate_dir, "KPOINTS"),
                  "K-points for supercell ionic relaxation",
                  SUP_RELAX_KPOINTS_MESH, SUP_RELAX_KPOINTS_SHIFT)
    print(f"  [setup] Wrote supercell KPOINTS ({SUP_RELAX_KPOINTS_MESH}) to hf/groundstate/")
    run_command(f"cp input/POTCAR {groundstate_dir}/", cwd=WORK_DIR)

    print("  [vasp] Running supercell ionic relaxation...")
    # check_success=False: ZBRENT crashes are expected when near convergence
    run_command(
        f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > supercell_relax.stdout",
        cwd=groundstate_dir,
        check_success=False,
    )
    check_vasp_convergence(groundstate_dir, "step-4")

    # Count completed ionic steps and warn if supercell hit NSW limit
    n_ionic = count_ionic_steps(groundstate_dir)
    if n_ionic:
        print(f"  [info] Supercell ionic relaxation: {n_ionic} step(s) completed (max NSW=100)")
        if n_ionic >= 100:
            print(f"")
            print(f"  ╔{'═'*60}╗")
            print(f"  ║  WARNING: Supercell relaxation reached NSW=100 limit     ║")
            print(f"  ║  The supercell may not be at equilibrium.               ║")
            print(f"  ║  Check per-atom forces above vs EDIFFG threshold.       ║")
            print(f"  ║  Consider: larger NSW, or review Step 3 relaxation.     ║")
            print(f"  ╚{'═'*60}╝")
            print(f"")

    # 4c. Use SPOSCAR (exact tiling) as reference structure for downstream.
    # The ISIF=2 relaxation produced CHGCAR + WAVECAR in groundstate/ for
    # seeding, but the reference structure is the phonopy tiling of the
    # relaxed unit cell — NOT the relaxed supercell (cell params are
    # k-point-converged from Step 3; re-relaxing at coarse k-mesh would
    # degrade them).
    run_command(f"cp SPOSCAR {HFFILES_DIR}/CONTCAR_supercell_relaxed", cwd=HFFILES_DIR)
    run_command(f"cp SPOSCAR {HFFILES_DIR}/CONTCAR", cwd=HFFILES_DIR)
    # Also keep the actual relaxed CONTCAR in groundstate/ for reference
    run_command(f"cp CONTCAR {groundstate_dir}/CONTCAR_relaxed", cwd=groundstate_dir, check_success=False)

    write_status(4, "completed", "Supercell relaxed — "
                 "CHGCAR/WAVECAR in hf/groundstate/")
    print_step_result(4, ok=True, duration_s=time.time() - _t0)


# ── Step 5: Copy files to hf/ ───────────────────────────────────────────────
if START_STEP <= 5:
    print_step_header(5)
    write_status(5, "running", "Copy files to hf/")
    _t0 = time.time()
    run_command(f"mkdir -p {HFFILES_DIR}", cwd=WORK_DIR)
    # POSCAR_unitcell = relaxed unit cell (primitive) for phonopy displacement generation
    run_command(f"cp scf/CONTCAR {HFFILES_DIR}/POSCAR_unitcell", cwd=WORK_DIR)
    # Force-constant INCAR (NSW=0, no LOPTICS) from YAML: incar_templates.hf + incar_settings.hf
    write_incar(os.path.join(HFFILES_DIR, "INCAR"), CONFIG, "hf")
    write_kpoints(os.path.join(HFFILES_DIR, "KPOINTS"),
                  "K-points for force-constant calculation (coarse mesh)",
                  HF_KPOINTS_MESH, HF_KPOINTS_SHIFT)
    print(f"  [setup] Wrote coarse KPOINTS ({HF_KPOINTS_MESH}) to hf/")
    run_command(f"cp input/POTCAR {HFFILES_DIR}/", cwd=WORK_DIR)

    # symmetry.conf for phonopy (DIM from config, IRREPS=0 0 0)
    ensure_dim_in_conf(os.path.join(HFFILES_DIR, "symmetry.conf"), "symmetry.conf", PHONOPY_DIM)
    write_status(5, "completed", "Files copied to hf/")
    print_step_result(5, ok=True, duration_s=time.time() - _t0)

# ── Step 6-7: Phonopy displacements + runHF ──────────────────────────────────
if START_STEP <= 6:
    print_step_header(6)
    write_status(6, "running", "Verify supercell displacements (from Step 4)")
    _t0 = time.time()
    # Displacement files (SPOSCAR + POSCAR-*) were already generated in hf/ by Step 4
    sposcar_path = os.path.join(HFFILES_DIR, "SPOSCAR")
    if not os.path.exists(sposcar_path):
        raise FileNotFoundError(
            f"SPOSCAR not found in {HFFILES_DIR}. Step 4 should have generated it "
            f"from CONTCAR_unitcell via phonopy -d."
        )
    print(f"  [verify] SPOSCAR found in hf/ — reusing supercell displacements from Step 4")
    write_status(6, "completed", "Displacements verified (from Step 4)")
    print_step_result(6, ok=True, duration_s=time.time() - _t0)

if START_STEP <= 7:
    print_step_header(7)
    write_status(7, "running", "runHF folder organization")
    _t0 = time.time()
    run_hf_script = os.path.join(BINARY_UTILITIES_DIR, "runHF")
    if not os.path.exists(run_hf_script):
        raise FileNotFoundError(
            f"runHF not found at {run_hf_script}. Check BINARY_UTILITIES_DIR."
        )
    run_command(run_hf_script, cwd=HFFILES_DIR)
    write_status(7, "completed", "runHF folder organization done")
    print_step_result(7, ok=True, duration_s=time.time() - _t0)

# ── Step 8: Populate groundstate/ config + update displacement symlinks ────────
if START_STEP <= 8:
    print_step_header(8)
    write_status(8, "running", "Populate groundstate/ config + update displacement symlinks")
    _t0 = time.time()

    groundstate_dir = os.path.join(HFFILES_DIR, "groundstate")

    # Ensure groundstate/ exists
    if not os.path.isdir(groundstate_dir):
        print("  groundstate/ not found — Step 4 may not have run; creating it")
        run_command(f"mkdir -p {groundstate_dir}", cwd=WORK_DIR)

    # Copy INCAR, KPOINTS, POSCAR for record-keeping
    run_command(f"cp INCAR {groundstate_dir}/", cwd=HFFILES_DIR)
    run_command(f"cp KPOINTS {groundstate_dir}/", cwd=HFFILES_DIR)
    run_command(f"cp POSCAR_unitcell {groundstate_dir}/POSCAR", cwd=HFFILES_DIR)

    # Replace dangling runHF symlinks with direct groundstate/WAVECAR links
    update_wavecar_symlinks(HFFILES_DIR)
    update_chgcar_symlinks(HFFILES_DIR)

    write_status(8, "completed", "groundstate/ config populated — "
                 "displacement runs seeded from hf/groundstate/")
    print_step_result(8, ok=True, duration_s=time.time() - _t0)

# ── Step 9: VASP force constants ─────────────────────────────────────────────
if START_STEP <= 9:
    print_step_header(9)
    write_status(9, "running", "VASP in all hf_POSCAR folders (force constants)")
    _t0 = time.time()

    # ── Use local env-aware copy so SRUN_ARGS from config is respected ────
    # The original at BINARY_UTILITIES_DIR/automate_hfiles.sh has hardcoded
    # srun params. Our local copy reads $SRUN_ARGS from the environment.
    automate_script = os.path.join(SCRIPT_DIR, "automate_hfiles_fixed.sh")
    if not os.path.exists(automate_script):
        # Fall back to the binary-utilities original (single-node only)
        automate_script = os.path.join(BINARY_UTILITIES_DIR, "automate_hfiles.sh")
    if not os.path.exists(automate_script):
        raise FileNotFoundError(
            f"automate_hfiles_fixed.sh not found at {SCRIPT_DIR}/automate_hfiles_fixed.sh "
            f"and automate_hfiles.sh not found at {BINARY_UTILITIES_DIR}/automate_hfiles.sh. "
            f"Check SCRIPT_DIR and BINARY_UTILITIES_DIR."
        )
    check_no_selective_dynamics(
        os.path.join(HFFILES_DIR, "SPOSCAR"),
        "SPOSCAR — source for all force-constant displacement POSCAR files"
    )
    vasp9_ok = vasp_loop_check_and_restart(automate_script, max_restarts=VASP_MAX_RESTARTS)
    if not vasp9_ok:
        write_status(9, "failed", "VASP force-constant runs did not complete — check hf_POSCAR-*/stdout files")
        print_step_result(9, ok=False, duration_s=time.time() - _t0,
                          message="Force-constant VASP runs incomplete")
        raise RuntimeError(
            f"Step 9 failed: VASP force-constant runs incomplete after {VASP_MAX_RESTARTS} attempts. "
            "Proceeding would cause phonopy to compute wrong force constants from partial data."
        )
    write_status(9, "completed", "VASP force-constant runs finished")
    print_step_result(9, ok=True, duration_s=time.time() - _t0)

# ── Step 10: Phonon postprocessing ───────────────────────────────────────────
if START_STEP <= 10:
    print_step_header(10)
    write_status(10, "running", "Phonon postprocessing")
    _t0 = time.time()

    # PART A: phonon_force — Run "phonopy -f" on all hf_POSCAR-XXX/vasprun.xml files
    print("  [10a] Extracting force constants from VASP runs...")
    hf_dirs = sorted(glob.glob(os.path.join(HFFILES_DIR, "hf_POSCAR-*")))
    if not hf_dirs:
        print("ERROR: No hf_POSCAR-* directories found in hf/.")
        write_status(10, "failed", "No hf_POSCAR-* directories found")
        sys.exit(1)
    last_hf = os.path.basename(hf_dirs[-1])
    N = last_hf.split("-")[-1]
    brace_pattern = f"hf_POSCAR-{{001..{N}}}"
    run_command(f"phonopy -f {brace_pattern}/vasprun.xml", cwd=HFFILES_DIR)

    # 10b: eigenvectors + visualization + symmetry
    print("  [10b] Running phonopy eigenvectors + visualization + symmetry...")

    # eigenvectors.conf (needs DIM + BAND for non-empty band.yaml)
    eigen_conf = os.path.join(HFFILES_DIR, "eigenvectors.conf")
    write_eigenvectors_conf(eigen_conf, PHONOPY_DIM, EIGVEC_BAND_PATH,
                            EIGVEC_BAND_LABELS, EIGVEC_BAND_POINTS)

    # Run phonopy eigenvectors (uses POSCAR_unitcell — the relaxed unit cell from scf/CONTCAR)
    run_command("phonopy -c POSCAR_unitcell eigenvectors.conf", cwd=HFFILES_DIR)

    # phonopy symmetry (irreps at Gamma) — uses POSCAR_unitcell (relaxed unit cell)
    ensure_dim_in_conf(os.path.join(HFFILES_DIR, "symmetry.conf"), "symmetry.conf", PHONOPY_DIM)
    run_command("phonopy -c POSCAR_unitcell symmetry.conf", cwd=HFFILES_DIR)

    # phonopy_visualization + phonopy_symmetry are only meaningful for full
    # band-path calculations (multiple q-points).  With Gamma-only
    # (band_points=1) the compiled Fortran binaries can't parse the
    # single-point band.yaml format, and irreps.yaml (generated above by
    # phonopy symmetry.conf) already contains the Gamma-point irrep labels.
    # Skip the entire block for Gamma-only — no information is lost.
    IS_FULL_BAND = int(PHONOPY_BAND_POINTS) > 1
    if IS_FULL_BAND:
        # phonopy_visualization — reads band.yaml + CONTCAR, outputs all_mode.txt
        print("  [10c] Full band-path detected — running phonopy_visualization + symmetry...")
        hf_contcar = os.path.join(HFFILES_DIR, "CONTCAR")
        if not (os.path.exists(hf_contcar) and os.path.getsize(hf_contcar) > 0):
            relax_contcar = os.path.join(HFFILES_DIR, "relax", "CONTCAR")
            if os.path.exists(relax_contcar) and os.path.getsize(relax_contcar) > 0:
                shutil.copy2(relax_contcar, hf_contcar)
            else:
                sposcar_path = os.path.join(HFFILES_DIR, "SPOSCAR")
                if os.path.exists(sposcar_path) and os.path.getsize(sposcar_path) > 0:
                    shutil.copy2(sposcar_path, hf_contcar)

        pv_cmd = (f"export PATH={BINARY_UTILITIES_DIR}:$PATH && "
                  f"echo -e '1\\nno' | phonopy_visualization")
        run_command(pv_cmd, cwd=HFFILES_DIR, check_success=False)

        all_mode_path = os.path.join(HFFILES_DIR, "all_mode.txt")
        if os.path.exists(all_mode_path) and os.path.getsize(all_mode_path) > 0:
            phonopy_sym = os.path.join(BINARY_UTILITIES_DIR, "phonopy_symmetry")
            if os.path.exists(phonopy_sym):
                run_command(phonopy_sym, cwd=HFFILES_DIR)
            else:
                print(f"  WARNING: phonopy_symmetry not found at {phonopy_sym}")
        else:
            print("  WARNING: all_mode.txt not produced — phonopy_symmetry skipped")
    else:
        print("  [10c] Gamma-only mode — phonopy_visualization + symmetry skipped "
              "(irreps.yaml already has mode irrep labels)")

    if not os.path.exists(os.path.join(HFFILES_DIR, "all_mode.txt")):
        print("WARNING: all_mode.txt was not created by phonon postprocessing.")
    write_status(10, "completed", "Phonon postprocessing done")
    print_step_result(10, ok=True, duration_s=time.time() - _t0)

# ── Step 11: Copy CONTCAR + VASP inputs to raman dir ─────────────────────────
if START_STEP <= 11:
    print_step_header(11)
    write_status(11, "running", "Copy CONTCAR to raman dir")
    _t0 = time.time()

    run_command(f"mkdir -p {RAMAN_DIR}", cwd=WORK_DIR)
    run_command(f"cp scf/CONTCAR {RAMAN_DIR}/CONTCAR", cwd=WORK_DIR)
    for f in ("CHGCAR", "WAVECAR"):
        src = os.path.join(WORK_DIR, "scf", f)
        dst = os.path.join(RAMAN_DIR, f)
        if os.path.exists(src):
            os.symlink(src, dst)
            print(f"  [setup] Symlinked {f} from scf/ → raman/")
        else:
            print(f"  [setup] WARNING: {f} not found in scf/ — RA_POSCAR-* runs will start cold")
    write_incar(os.path.join(RAMAN_DIR, "INCAR"), CONFIG, "dielec")
    write_kpoints(os.path.join(RAMAN_DIR, "KPOINTS"),
                  "K-points for resonant Raman calculation (dense mesh)",
                  RAMAN_KPOINTS_MESH, RAMAN_KPOINTS_SHIFT)
    print(f"  [setup] Wrote Raman KPOINTS ({RAMAN_KPOINTS_MESH}) to raman/")
    run_command(f"cp input/POTCAR {RAMAN_DIR}/", cwd=WORK_DIR)
    print("  [setup] Copied CONTCAR + INCAR (from YAML config), POTCAR (from input/) to RAMAN_DIR.")
    print(f"  [setup] KPOINTS generated from config raman_kpoints.mesh={RAMAN_KPOINTS_MESH}.")
    write_status(11, "completed", "CONTCAR and INCAR copied to raman dir")
    print_step_result(11, ok=True, duration_s=time.time() - _t0)

# ── Step 12: chdir to RAMAN_DIR ────────────────────────────────────────────
if START_STEP <= 12:
    write_status(12, "completed", "Navigated to Raman dir")

print_step_header(12)
os.chdir(RAMAN_DIR)
print(f"Working directory: {os.getcwd()}")
print_step_result(12, ok=True, duration_s=0)

# ── Step 13: Generate Raman displacements and organize ──────────────────────
if START_STEP <= 13:
    print_step_header(13)
    write_status(13, "running", "Generate Raman displacements and organize")
    _t0 = time.time()

    # Validate binary utilities exist before attempting to run them
    for bin_name in ("ramdiscar", "genRApos610", "runRA"):
        bin_path = os.path.join(BINARY_UTILITIES_DIR, bin_name)
        if not os.path.exists(bin_path):
            raise FileNotFoundError(
                f"{bin_name} not found at {bin_path}. "
                f"Cannot generate Raman displacements without this binary."
            )

    # CUDA-linked binaries — skip gracefully on CPU (check_success=not CPU_FLAG)
    run_command(f"{BINARY_UTILITIES_DIR}/ramdiscar", check_success=not CPU_FLAG)

    go_file = os.path.join(RAMAN_DIR, ".go_input")
    with open(go_file, "w") as gf:
        gf.write("go\n")
    run_command(f"{BINARY_UTILITIES_DIR}/genRApos610 < {go_file}", check_success=not CPU_FLAG)
    os.remove(go_file)

    run_command(f"{BINARY_UTILITIES_DIR}/runRA")

    # Propagate CHGCAR/WAVECAR symlinks into each RA_POSCAR-* directory
    # (symlinks point directly to scf/CHGCAR and scf/WAVECAR — no copies needed)
    print("  [setup] Propagating CHGCAR + WAVECAR symlinks into each ra_pos_* directory...")
    chgcar_src = os.path.join(WORK_DIR, "scf", "CHGCAR")
    wavecar_src = os.path.join(WORK_DIR, "scf", "WAVECAR")
    seeded_count = 0
    for d in sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*"))):
        for f_name, src_path in [("CHGCAR", chgcar_src), ("WAVECAR", wavecar_src)]:
            dst = os.path.join(d, f_name)
            if os.path.exists(src_path) and not os.path.exists(dst):
                os.symlink(src_path, dst)
                seeded_count += 1
    n_ra = len(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if seeded_count:
        print(f"  [setup] Created {seeded_count} symlinks across {n_ra} ra_pos_* directories")
    else:
        print(f"  [setup] No CHGCAR/WAVECAR to seed ({n_ra} ra_pos_* dirs found)")

    write_status(13, "completed", "Raman displacements generated and organized")
    print_step_result(13, ok=True, duration_s=time.time() - _t0)

# ── Step 14: Resonant VASP ───────────────────────────────────────────────────
if START_STEP <= 14:
    print_step_header(14)
    write_status(14, "running", "Resonant VASP runs in all ra_pos_* folders")
    _t0 = time.time()

    LOCAL_RUN_ALL_VASP = os.path.join(SCRIPT_DIR, "run_all_vasp_folders_fixed.sh")
    if not os.path.exists(LOCAL_RUN_ALL_VASP):
        raise FileNotFoundError(
            f"run_all_vasp_folders_fixed.sh not found at {LOCAL_RUN_ALL_VASP}. "
            f"This script is required for Step 14 resonant VASP runs."
        )
    ra_pos_dirs = sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if ra_pos_dirs:
        check_no_selective_dynamics(
            os.path.join(ra_pos_dirs[0], "POSCAR"),
            "ra_pos_* POSCAR — Raman displacement file"
        )
    run_command(f"export SRUN_ARGS='{SRUN_ARGS}' && bash {LOCAL_RUN_ALL_VASP}")
    ra_dirs_step14 = sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if not ra_dirs_step14:
        print_step_result(14, ok=False, duration_s=time.time() - _t0,
                          message="No ra_pos_* directories produced")
        raise RuntimeError(
            "Step 14 produced no ra_pos_* directories — "
            "run_all_vasp_folders_fixed.sh likely failed silently."
        )
    for d in ra_dirs_step14:
        check_vasp_convergence(d, "step-14")
        check_dielectric_complete(d, "step-14")
    write_status(14, "completed", "Resonant VASP runs finished — "
                 f"{len(ra_dirs_step14)} directories validated")
    print_step_result(14, ok=True, duration_s=time.time() - _t0,
                      message=f"{len(ra_dirs_step14)} directories")

# ── Step 15: Kopia post-processing ───────────────────────────────────────────
if START_STEP <= 15:
    print_step_header(15)
    write_status(15, "running", "Kopia post-processing")
    _t0 = time.time()

    ra_dirs = sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if not ra_dirs:
        write_status(15, "failed", "No ra_pos_* directories found")
        print_step_result(15, ok=False, duration_s=time.time() - _t0,
                          message="No ra_pos_* directories")
        raise RuntimeError(
            "Step 15 failed: no ra_pos_* directories found in RAMAN_DIR. "
            "Step 13 (runRA) must create these before kopia can run."
        )
    else:
        generate_kopia_script(RAMAN_DIR, ra_dirs)
        axml_dir = os.path.join(RAMAN_DIR, "AXML")
        if not os.path.isdir(axml_dir):
            print_step_result(15, ok=False, duration_s=time.time() - _t0,
                              message="AXML/ not created")
            raise RuntimeError(
                "Step 15 failed: kopia ran but AXML/ directory was not created."
            )
        axml_files = [f for f in os.listdir(axml_dir) if f.endswith(".xml")]
        if not axml_files:
            print_step_result(15, ok=False, duration_s=time.time() - _t0,
                              message="AXML/ empty")
            raise RuntimeError(
                "Step 15 failed: AXML/ exists but contains no .xml files."
            )
        empty_xml = [f for f in axml_files
                     if os.path.getsize(os.path.join(axml_dir, f)) == 0]
        if empty_xml:
            print_step_result(15, ok=False, duration_s=time.time() - _t0,
                              message=f"{len(empty_xml)} empty XML files")
            raise RuntimeError(
                f"Step 15 failed: {len(empty_xml)} AXML/*.xml file(s) are empty."
            )
        print(f"  [verify] AXML/ contains {len(axml_files)} valid XML files")
        write_status(15, "completed", "Kopia post-processing done")
        print_step_result(15, ok=True, duration_s=time.time() - _t0,
                          message=f"{len(axml_files)} XML files")

# ── Step 16: RAMFILE generation ──────────────────────────────────────────────
if START_STEP <= 16:
    print_step_header(16)
    write_status(16, "running", "RAMFILE generation")
    _t0 = time.time()

    ramfile_script_src = os.path.join(BINARY_UTILITIES_DIR, "ramfile_dynamic.sh")
    if not os.path.exists(ramfile_script_src):
        raise RuntimeError(
            f"ramfile_dynamic.sh not found at {ramfile_script_src}. "
            f"Check BINARY_UTILITIES_DIR."
        )

    store_ram = os.path.join(RAMAN_DIR, "store_ramfile")
    store_eps = os.path.join(RAMAN_DIR, "store_epsilon")
    os.makedirs(store_ram, exist_ok=True)
    os.makedirs(store_eps, exist_ok=True)

    ramfile_script_dst = os.path.join(RAMAN_DIR, "ramfile_dynamic.sh")
    inject_ramfile_energies(ramfile_script_src, ramfile_script_dst, DESIRED_ENERGIES)

    run_command(f"export PATH={BINARY_UTILITIES_DIR}:$PATH && bash ramfile_dynamic.sh", cwd=RAMAN_DIR)

    for energy in DESIRED_ENERGIES:
        ramfile = os.path.join(store_ram, f"RAMFILE_{energy}")
        if not os.path.exists(ramfile):
            print_step_result(16, ok=False, duration_s=time.time() - _t0,
                              message=f"Missing RAMFILE_{energy}")
            raise RuntimeError(
                f"Step 16 failed: ramfile_dynamic.sh produced no RAMFILE_{energy}."
            )

    write_status(16, "completed", "RAMFILE generation done")
    print_step_result(16, ok=True, duration_s=time.time() - _t0,
                      message=f"{len(DESIRED_ENERGIES)} energies")

# ── Step 17: Copy static files to Raman dir + output dir ─────────────────────
if START_STEP <= 17:
    print_step_header(17)
    _t0 = time.time()
    run_command(f"cp {HFFILES_DIR}/band.yaml .", cwd=RAMAN_DIR, check_success=False)
    run_command(f"cp {HFFILES_DIR}/irreps.yaml .", cwd=RAMAN_DIR, check_success=False)
    output_dir = os.path.join(WORK_DIR, "output")
    run_command(f"mkdir -p {output_dir}", cwd=WORK_DIR)
    for src_base in (
        "band.yaml", "irreps.yaml", "POSCAR_unitcell", "SPOSCAR",
        "FORCE_SETS", "phonopy.yaml", "CONTCAR",
        "eigenvectors.conf", "symmetry.conf",
    ):
        src = os.path.join(HFFILES_DIR, src_base)
        if os.path.exists(src) and os.path.getsize(src) > 0:
            run_command(f"cp {src} {output_dir}/", cwd=WORK_DIR, check_success=False)
    all_mode = os.path.join(HFFILES_DIR, "all_mode.txt")
    if os.path.exists(all_mode) and os.path.getsize(all_mode) > 0:
        run_command(f"cp {all_mode} {output_dir}/", cwd=WORK_DIR, check_success=False)
    for mf in glob.glob(os.path.join(HFFILES_DIR, "mode*")):
        if os.path.getsize(mf) > 0:
            run_command(f"cp {mf} {output_dir}/", cwd=WORK_DIR, check_success=False)

    # ── Archive INCARs for auditability ──────────────────────────────────
    # Copies the actual VASP INCAR files used at each stage to output/incar/
    # so someone unfamiliar with the YAML config can inspect the settings.
    incar_dir = os.path.join(output_dir, "incar")
    run_command(f"mkdir -p {incar_dir}", cwd=WORK_DIR)
    incar_sources = [
        (os.path.join(WORK_DIR, "scf", "INCAR"),              "relax.incar"),
        (os.path.join(HFFILES_DIR, "groundstate", "INCAR"),   "supercell_relax.incar"),
        (os.path.join(HFFILES_DIR, "INCAR"),                  "hf_force_constants.incar"),
        (os.path.join(RAMAN_DIR, "INCAR"),                    "dielec_raman.incar"),
    ]
    for src, dst_name in incar_sources:
        dst = os.path.join(incar_dir, dst_name)
        if os.path.exists(src) and os.path.getsize(src) > 0:
            shutil.copy2(src, dst)
            print(f"  [output] incar/{dst_name}")

    write_status(17, "completed", f"Static files copied to output/ ({output_dir})")
    print_step_result(17, ok=True, duration_s=time.time() - _t0)

# ── Steps 18-21: Energy processing loop + SpectroPy plots ────────────────────
if START_STEP <= 18:
    print_step_header(18)
    write_status(18, "running", f"Processing energies: {', '.join(DESIRED_ENERGIES)} eV")
    _t0 = time.time()

    for bin_name in ("raman_tensor", "broadening"):
        bin_path = os.path.join(BINARY_UTILITIES_DIR, bin_name)
        if not os.path.exists(bin_path):
            raise FileNotFoundError(
                f"{bin_name} not found at {bin_path}."
            )

    store_ram_step18 = os.path.join(RAMAN_DIR, "store_ramfile")
    for energy in DESIRED_ENERGIES:
        ramfile_check = os.path.join(store_ram_step18, f"RAMFILE_{energy}")
        if not os.path.exists(ramfile_check):
            raise RuntimeError(
                f"RAMFILE_{energy} not found in store_ramfile/."
            )

    for energy in DESIRED_ENERGIES:
        print(f"\n  [energy] Processing {energy} eV —")
        run_command("rm -f RAMFILE", cwd=RAMAN_DIR, check_success=False)
        run_command(f"cp store_ramfile/RAMFILE_{energy} RAMFILE", cwd=RAMAN_DIR)

        raman_input = f"{RAMAN_INCIDENT_POL}\n{RAMAN_SCATTERED_POL}\n{RAMAN_SURFACE_NORMAL}\n"
        rt_file = os.path.join(RAMAN_DIR, ".raman_tensor_input")
        with open(rt_file, "w") as rf:
            rf.write(raman_input)
        run_command(
            f"{BINARY_UTILITIES_DIR}/raman_tensor < .raman_tensor_input > /dev/null",
            cwd=RAMAN_DIR, check_success=not CPU_FLAG
        )
        os.remove(rt_file)
        print(f"    Raman tensor: pol=({RAMAN_INCIDENT_POL},{RAMAN_SCATTERED_POL})")

        _b = CONFIG.get("broadening", {})
        b_input = os.path.join(RAMAN_DIR, "broadening_input")
        b_content = (
            f"Raman_intensity_complex  !!! the file name\n"
            f"{int(_b.get('mode', 2))}            !!! peak broadening mode\n"
            f"{int(_b.get('hwhm', 1))}            !!! half width at half maximum (cm-1)\n"
            f"{int(_b.get('interpolation', 200))}  !!! interpolation points\n"
            f"{int(_b.get('normalization', 2))}    !!! normalization\n"
        )
        with open(b_input, "w") as bf:
            bf.write(b_content)
        run_command(f"{BINARY_UTILITIES_DIR}/broadening", cwd=RAMAN_DIR)

        if os.path.exists(os.path.join(RAMAN_DIR, "Raman_intensity_complex")):
            run_command(f"mv Raman_intensity_complex Raman_intensity_complex_{energy}eV", cwd=RAMAN_DIR)
        else:
            print(f"WARNING: Raman_intensity_complex not found for {energy}eV.")
        if os.path.exists(os.path.join(RAMAN_DIR, "Raman_intensity_complex_broadening")):
            run_command(f"mv Raman_intensity_complex_broadening Raman_intensity_complex_broadening_{energy}eV", cwd=RAMAN_DIR)

    write_status(18, "completed", f"Raman tensor computed for: {', '.join(DESIRED_ENERGIES)} eV")
    write_status(20, "completed", f"All energies processed: {', '.join(DESIRED_ENERGIES)} eV")
    print_step_result(18, ok=True, duration_s=time.time() - _t0,
                      message=f"{len(DESIRED_ENERGIES)} energies processed")

    # ---- Step 21: Generate Raman spectra plots via SpectroPy -----------------
    print_step_header(21)
    write_status(21, "running", "Generating publication-style Raman plots via SpectroPy")
    _t0 = time.time()

    spectropy_dir = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "SpectroPy"))
    generate_plots = os.path.join(spectropy_dir, "generate_raman_plots.py")

    for energy in DESIRED_ENERGIES:
        energy_label = f"{energy}eV"
        energy_dir = os.path.join(RAMAN_DIR, energy_label)
        os.makedirs(energy_dir, exist_ok=True)
        src_file = os.path.join(RAMAN_DIR, f"Raman_intensity_complex_{energy_label}")
        dst_file = os.path.join(energy_dir, "Raman_intensity_specific.dat")
        if os.path.exists(src_file):
            with open(dst_file, "w") as f:
                f.write("# Freq(cm-1)   Intensity(arb.)   Irrep.\n")
                with open(src_file) as src:
                    f.write(src.read())
            print(f"  Prepared: {energy_label}/Raman_intensity_specific.dat")

    if os.path.exists(generate_plots):
        run_command(
            f"echo -e '5.0\\nl' | python3 {generate_plots}",
            cwd=RAMAN_DIR,
            check_success=False
        )
        write_status(21, "completed", "Raman spectra plots generated")
        print_step_result(21, ok=True, duration_s=time.time() - _t0)
    else:
        print(f"  WARNING: SpectroPy plotter not found at {generate_plots}")
        write_status(21, "failed", f"SpectroPy plotter not found at {generate_plots}")
        print_step_result(21, ok=False, duration_s=time.time() - _t0,
                          message="SpectroPy not found")

    # ---- Aggregate output ───────────────────────────────────────────────────
    output_dir = os.path.join(WORK_DIR, "output")
    raman_plots_out = os.path.join(output_dir, "raman_spectra")
    raman_data_out = os.path.join(output_dir, "raman_data")
    os.makedirs(raman_plots_out, exist_ok=True)
    os.makedirs(raman_data_out, exist_ok=True)

    for energy in DESIRED_ENERGIES:
        energy_label = f"{energy}eV"
        png_src = os.path.join(RAMAN_DIR, energy_label, "Raman_plot_styled.png")
        if os.path.exists(png_src):
            shutil.copy2(png_src, os.path.join(raman_plots_out, f"{energy_label}.png"))
            print(f"  [output] raman_spectra/{energy_label}.png")

    for pattern in ("Raman_intensity_complex_*eV", "Raman_intensity_complex_broadening_*eV"):
        for f in glob.glob(os.path.join(RAMAN_DIR, pattern)):
            shutil.copy2(f, os.path.join(raman_data_out, os.path.basename(f)))
            print(f"  [output] raman_data/{os.path.basename(f)}")

    write_status("final", "completed", "Automation workflow complete")

    # --scratch: copy output/ from WORK_DIR to HOME
    if SCRATCH_FLAG:
        home_output = os.path.join(MATERIAL_DIR, "output")
        scratch_output = os.path.join(WORK_DIR, "output")
        if os.path.exists(scratch_output):
            print(f"\n  [scratch] Copying output/ from SCRATCH to HOME...")
            run_command(f"mkdir -p {home_output}", cwd=MATERIAL_DIR)
            run_command(f"cp -r {scratch_output}/* {home_output}/", cwd=MATERIAL_DIR,
                        check_success=False)
            print(f"  [scratch] Results saved to: {home_output}")

# end_salloc.sh is not called — tmux + exit handles teardown correctly.
