import argparse
import os
import glob
import sys
from util import (Tee, run_command, fmt_time, calc_duration, ensure_dim_in_conf,
                  check_no_selective_dynamics,
                  is_vasprun_valid, merge_config,
                  parse_resume_step, load_config, build_srun_args,
                  write_eigenvectors_conf, update_wavecar_symlinks,
                  update_chgcar_symlinks, check_vasp_convergence,
                  make_pipeline_excepthook, write_kpoints, write_incar,
                  count_ionic_steps,
                  generate_kopia_script, inject_ramfile_energies)
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

# Preliminary WORK_DIR (refined after config load below)
if SCRATCH_FLAG:
    SCRATCH_BASE = os.environ.get("SCRATCH", "")
    if not SCRATCH_BASE:
        print("Error: --scratch flag requires $SCRATCH environment variable.")
        sys.exit(1)
    WORK_DIR = os.path.join(SCRATCH_BASE, "vasp_calculations", CWD_BASENAME)
else:
    WORK_DIR = MATERIAL_DIR
# HFFILES_DIR / RAMAN_DIR — assigned after config load (see line ~144)
HFFILES_DIR = None
RAMAN_DIR = None
# Preliminary status/log path (used by --restart cleanup below; refined after config load)
if SCRATCH_FLAG:
    STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(WORK_DIR, "workflow.log"))
else:
    STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(MATERIAL_DIR, "workflow.log"))

# ── --restart: delete all generated directories, keep input/ + config ────────
if RESTART_FLAG:
    sep = "=" * 80
    print(f"\n{sep}")
    print("  --restart flag detected: Deleting all generated output...")
    print(f"{sep}\n")

    clean_base = WORK_DIR if SCRATCH_FLAG else MATERIAL_DIR

    for dirname in ("scf", "hf", "raman", "output"):
        dp = os.path.join(clean_base, dirname)
        if os.path.exists(dp):
            shutil.rmtree(dp)
            print(f"  Removed: {dp}/")

    # With --scratch, also clean HOME/output/
    if SCRATCH_FLAG:
        home_output = os.path.join(MATERIAL_DIR, "output")
        if os.path.exists(home_output):
            shutil.rmtree(home_output)
            print(f"  Removed HOME output/: {home_output}")

    # Remove combined workflow log (on scratch under --scratch, HOME otherwise)
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
# Under --scratch, intermediate dirs (scf/, hf/, raman/) live exclusively on
# $SCRATCH — HOME only contains input/ and output/.  The combined status/log
# file (workflow.log) is also on scratch for faster I/O.
if SCRATCH_FLAG:
    WORK_DIR = os.path.join(os.environ["SCRATCH"], "vasp_calculations", MATERIAL_NAME)
    HFFILES_DIR = os.path.join(WORK_DIR, "hf")
    RAMAN_DIR = os.path.join(WORK_DIR, "raman")
    STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(WORK_DIR, "workflow.log"))
    print(f"  [scratch] WORK_DIR = {WORK_DIR}")
    print(f"  [scratch] Status/log: {STATUS_FILE}")
else:
    WORK_DIR = MATERIAL_DIR
    HFFILES_DIR = os.path.join(MATERIAL_DIR, "hf")
    RAMAN_DIR = os.path.join(MATERIAL_DIR, "raman")
    STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(MATERIAL_DIR, "workflow.log"))
# Install exception hook now that STATUS_FILE is finalised
sys.excepthook = make_pipeline_excepthook(STATUS_FILE)

# Redirect all print() output to the combined workflow log (which also serves
# as the status file — write_status() appends formatted update blocks to it).
sys.stdout = Tee(STATUS_FILE)
print(f"  [log] Workflow log: {STATUS_FILE}")

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
        else:
            print(f"  [gpu] Running automate_hfiles.sh (GPU mode)...")
            run_command(vasp_script_path, cwd=HFFILES_DIR)

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
            if not is_vasprun_valid(os.path.join(HFFILES_DIR, d, "vasprun.xml"))
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

# ── --scratch: sync input/ + config to SCRATCH ──────────────────────────────
if SCRATCH_FLAG:
    print(f"\n  [scratch] Syncing input/ from HOME to SCRATCH...")
    print(f"  [scratch] Source: {MATERIAL_DIR}/input")
    print(f"  [scratch] Target: {WORK_DIR}")
    run_command(f"mkdir -p {WORK_DIR}", cwd=MATERIAL_DIR)
    run_command(f"cp -r input {WORK_DIR}/", cwd=MATERIAL_DIR)
    print(f"  [scratch] Sync complete. VASP stages will run in: {WORK_DIR}")

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKFLOW STEPS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Step 3: VASP relaxation ──────────────────────────────────────────────────
if START_STEP <= 3:
    write_status(3, "running", "Initial VASP relaxation")

    # VASP runs in scf/; no INCAR swap needed — downstream steps generate fresh
    scf_dir = os.path.join(WORK_DIR, "scf")
    run_command(f"mkdir -p {scf_dir}", cwd=WORK_DIR)

    print("\n--- Step 3: Initial VASP relaxation ---")
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
    write_incar(os.path.join(WORK_DIR, "scf", "INCAR"), CONFIG, "relax", CPU_FLAG)
    # Check POTCAR exists before running VASP
    if not os.path.exists(os.path.join(scf_dir, "POTCAR")):
        raise FileNotFoundError(
            f"POTCAR not found in {scf_dir}. Cannot run VASP without a pseudopotential."
        )
    run_command(f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > relaxation.stdout",
                cwd=scf_dir, check_success=False)
    check_vasp_convergence(scf_dir, "step-3")
    write_status(3, "completed", "Initial VASP relaxation finished")


# ── Step 4: Generate supercell + quick electronic convergence check ──────────
# Step 4b/4c (full supercell ionic relaxation with NSW=200) were intentionally
# removed: the supercell generated from the relaxed unit cell (Step 3) should
# be near equilibrium, so a short electronic SCF check suffices.
# NELM=10 in the static template limits electronic SCF iterations; if the
# supercell electronic structure is incompatible, VASP errors out quickly.
if START_STEP <= 4:
    write_status(4, "running", "Supercell generation + electronic convergence check")

    print("\n--- Step 4: Generate supercell + quick electronic convergence check ---")
    groundstate_dir = os.path.join(HFFILES_DIR, "groundstate")
    run_command(f"mkdir -p {groundstate_dir}", cwd=WORK_DIR)

    # 4a. Generate supercell from relaxed unit cell (in hf/ — shared with Step 6)
    print("  [setup] Creating supercell in hf/ via phonopy (shared with force-constant step)...")
    run_command(f"cp scf/CONTCAR {HFFILES_DIR}/POSCAR_unitcell", cwd=WORK_DIR)
    # Guard: POSCAR_unitcell must not contain Selective Dynamics — CONTCAR never carries it,
    # but if it somehow did, phonopy displacements would be silently wrong.
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

    # 4d. Quick electronic SCF convergence check on the perfect supercell
    # Uses the static template (NSW=0, NELM=100, LCHARG=TRUE, LWAVE=TRUE).
    # Ions are fixed — only electronic SCF iterations count.
    print("  [setup] Setting up quick electronic SCF convergence check on the SPOSCAR supercell...")
    write_incar(os.path.join(groundstate_dir, "INCAR"), CONFIG, "static", CPU_FLAG)
    write_kpoints(os.path.join(groundstate_dir, "KPOINTS"),
                  "K-points for supercell electronic convergence check",
                  SUP_RELAX_KPOINTS_MESH, SUP_RELAX_KPOINTS_SHIFT)
    print(f"  [setup] Wrote supercell KPOINTS ({SUP_RELAX_KPOINTS_MESH}) to hf/groundstate/")
    run_command(f"cp input/POTCAR {groundstate_dir}/", cwd=WORK_DIR)

    # 4d-ii. Run VASP — NSW=0 (fixed ions), NELM=100 enables full SCF convergence
    print("  [vasp] Running electronic SCF convergence check on supercell (NSW=0, NELM=100)...")
    print(f"  [vasp] Verifying the supercell ({PHONOPY_DIM}) generated from "
          f"the relaxed unit cell is electronically compatible.")
    run_command(
        f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > static.stdout",
        cwd=groundstate_dir,
    )
    check_vasp_convergence(groundstate_dir, "step-4d")

    # 4d-iii. Count electronic SCF iterations — warn if >3
    # (count_ionic_steps() counts Iteration N(M) lines in OUTCAR, which
    #  are the electronic SCF iterations. With NSW=0 there's only one
    #  ionic step, so the total is the number of SCF iterations.)
    n_elec_steps = count_ionic_steps(groundstate_dir)
    if n_elec_steps:
        print(f"  [info] Supercell SCF converged in {n_elec_steps} electronic iteration(s)")
        if n_elec_steps > 3:
            print(f"  WARNING: SCF needed {n_elec_steps} electronic iterations to converge "
                  f"(expected <=3 for a compatible supercell). The structure generated "
                  f"from the relaxed unit cell may not be electronically stable.")
    else:
        print(f"  WARNING: OUTCAR not found in {groundstate_dir} — cannot count SCF iterations")

    # 4e. Copy CONTCAR to hf/ for reference + phonopy_visualization
    # phonopy_visualization (Step 10b) reads a file named "CONTCAR" in hf/
    # CONTCAR_supercell_relaxed is a human-readable reference copy.
    run_command(f"cp CONTCAR {HFFILES_DIR}/CONTCAR_supercell_relaxed", cwd=groundstate_dir)
    run_command(f"cp CONTCAR {HFFILES_DIR}/CONTCAR", cwd=groundstate_dir)

    print("  [done] Step 4 complete — CHGCAR + WAVECAR in hf/groundstate/ ready for force-constant seeding.")
    write_status(4, "completed", "Supercell electronic check done — "
                 "CHGCAR/WAVECAR in hf/groundstate/")


# ── Step 5: Copy files to hf/ ───────────────────────────────────────────────
if START_STEP <= 5:
    write_status(5, "running", "Copy files to hf/")
    print("\n--- Step 5: Copy POTCAR + KPOINTS + INCAR to hf/ ---")
    run_command(f"mkdir -p {HFFILES_DIR}", cwd=WORK_DIR)
    # POSCAR_unitcell = relaxed unit cell (primitive) for phonopy displacement generation
    run_command(f"cp scf/CONTCAR {HFFILES_DIR}/POSCAR_unitcell", cwd=WORK_DIR)
    # Force-constant INCAR (NSW=0, no LOPTICS) from YAML: incar_templates.hf + incar_settings.hf
    write_incar(os.path.join(HFFILES_DIR, "INCAR"), CONFIG, "hf", CPU_FLAG)
    write_kpoints(os.path.join(HFFILES_DIR, "KPOINTS"),
                  "K-points for force-constant calculation (coarse mesh)",
                  HF_KPOINTS_MESH, HF_KPOINTS_SHIFT)
    print(f"  [setup] Wrote coarse KPOINTS ({HF_KPOINTS_MESH}) to hf/")
    run_command(f"cp input/POTCAR {HFFILES_DIR}/", cwd=WORK_DIR)

    # symmetry.conf for phonopy (DIM from config, IRREPS=0 0 0)
    ensure_dim_in_conf(os.path.join(HFFILES_DIR, "symmetry.conf"), "symmetry.conf", PHONOPY_DIM)
    write_status(5, "completed", "Files copied to hf/")

# ── Step 6-7: Phonopy displacements + runHF ──────────────────────────────────
if START_STEP <= 6:
    write_status(6, "running", "Verify supercell displacements (from Step 4)")
    print("\n--- Step 6: Verify supercell displacements (generated in Step 4) ---")
    # Displacement files (SPOSCAR + POSCAR-*) were already generated in hf/ by Step 4
    sposcar_path = os.path.join(HFFILES_DIR, "SPOSCAR")
    if not os.path.exists(sposcar_path):
        raise FileNotFoundError(
            f"SPOSCAR not found in {HFFILES_DIR}. Step 4 should have generated it "
            f"from CONTCAR_unitcell via phonopy -d."
        )
    print(f"  [verify] SPOSCAR found in hf/ — reusing supercell displacements from Step 4")
    write_status(6, "completed", "Displacements verified (from Step 4)")

if START_STEP <= 7:
    write_status(7, "running", "runHF folder organization")
    print("\n--- Step 7: Run runHF to organize displacement folders ---")
    run_hf_script = os.path.join(BINARY_UTILITIES_DIR, "runHF")
    if not os.path.exists(run_hf_script):
        raise FileNotFoundError(
            f"runHF not found at {run_hf_script}. Check BINARY_UTILITIES_DIR."
        )
    run_command(run_hf_script, cwd=HFFILES_DIR)
    write_status(7, "completed", "runHF folder organization done")

# ── Step 8: Populate groundstate/ config + update displacement symlinks ────────
# Step 4 ran the supercell static SCF directly in hf/groundstate/, producing
# CHGCAR and WAVECAR there.  This step copies INCAR/KPOINTS/POSCAR for
# record-keeping and updates the hf_POSCAR-* symlinks to point to groundstate/.
if START_STEP <= 8:
    write_status(8, "running", "Populate groundstate/ config + update displacement symlinks")

    print("\n--- Step 8: Populate groundstate/ config + update displacement symlinks ---")
    groundstate_dir = os.path.join(HFFILES_DIR, "groundstate")

    # Ensure groundstate/ exists
    if not os.path.isdir(groundstate_dir):
        print("  groundstate/ not found — Step 4 may not have run; creating it")
        run_command(f"mkdir -p {groundstate_dir}", cwd=WORK_DIR)

    # Copy INCAR, KPOINTS, POSCAR for record-keeping (the displacement VASP
    # runs read their own from hf/ level, but groundstate/ serves as reference)
    run_command(f"cp INCAR {groundstate_dir}/", cwd=HFFILES_DIR)
    run_command(f"cp KPOINTS {groundstate_dir}/", cwd=HFFILES_DIR)
    run_command(f"cp POSCAR_unitcell {groundstate_dir}/POSCAR", cwd=HFFILES_DIR)

    # Replace dangling runHF symlinks with direct groundstate/WAVECAR links
    update_wavecar_symlinks(HFFILES_DIR)
    # Create CHGCAR symlinks in displacement dirs (parallel to WAVECAR)
    update_chgcar_symlinks(HFFILES_DIR)

    write_status(8, "completed", "groundstate/ config populated — "
                 "displacement runs seeded from hf/groundstate/")

# ── Step 9: VASP force constants ─────────────────────────────────────────────
if START_STEP <= 9:
    write_status(9, "running", "VASP in all hf_POSCAR folders (force constants)")

    print("\n--- Step 9: Run VASP in all hf_POSCAR folders ---")
    automate_script = os.path.join(BINARY_UTILITIES_DIR, "automate_hfiles.sh")
    if not os.path.exists(automate_script):
        raise FileNotFoundError(
            f"automate_hfiles.sh not found at {automate_script}. "
            f"Check BINARY_UTILITIES_DIR."
        )
    # Guard: SPOSCAR must not contain Selective Dynamics — force constants need
    # full 3D displacements including out-of-plane (z) motion.
    check_no_selective_dynamics(
        os.path.join(HFFILES_DIR, "SPOSCAR"),
        "SPOSCAR — source for all force-constant displacement POSCAR files"
    )
    vasp9_ok = vasp_loop_check_and_restart(automate_script, max_restarts=VASP_MAX_RESTARTS)
    if not vasp9_ok:
        write_status(9, "failed", "VASP force-constant runs did not complete — check hf_POSCAR-*/stdout files")
        raise RuntimeError(
            f"Step 9 failed: VASP force-constant runs incomplete after {VASP_MAX_RESTARTS} attempts. "
            "Proceeding would cause phonopy to compute wrong force constants from partial data."
        )
    write_status(9, "completed", "VASP force-constant runs finished")

# ── Step 10: Phonon postprocessing ───────────────────────────────────────────
if START_STEP <= 10:
    write_status(10, "running", "Phonon postprocessing")

    # Replicates phonon_postprocessing (original had hardcoded inaccessible PATH)
    print("\n--- Step 10: Phonon postprocessing (force constants + eigenvectors) ---")

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

# ── Step 11: Copy CONTCAR + VASP inputs to raman dir ─────────────────────────
if START_STEP <= 11:
    write_status(11, "running", "Copy CONTCAR to raman dir")

    print("\n--- Step 11: Copy CONTCAR + INCAR + KPOINTS + POTCAR to Raman dir ---")
    run_command(f"mkdir -p {RAMAN_DIR}", cwd=WORK_DIR)
    run_command(f"cp scf/CONTCAR {RAMAN_DIR}/CONTCAR", cwd=WORK_DIR)
    # Symlink CHGCAR and WAVECAR from scf/ for warm-starting the resonant Raman runs
    # (Step 14 runs VASP in each RA_POSCAR-* with ISTART=1, ICHARG=1 — having these
    #  files present reduces SCF iterations by ~2-3 per displaced structure.)
    for f in ("CHGCAR", "WAVECAR"):
        src = os.path.join(WORK_DIR, "scf", f)
        dst = os.path.join(RAMAN_DIR, f)
        if os.path.exists(src):
            os.symlink(src, dst)
            print(f"  [setup] Symlinked {f} from scf/ → raman/")
        else:
            print(f"  [setup] WARNING: {f} not found in scf/ — RA_POSCAR-* runs will start cold")
    write_incar(os.path.join(RAMAN_DIR, "INCAR"), CONFIG, "dielec", CPU_FLAG)
    write_kpoints(os.path.join(RAMAN_DIR, "KPOINTS"),
                  "K-points for resonant Raman calculation (dense mesh)",
                  RAMAN_KPOINTS_MESH, RAMAN_KPOINTS_SHIFT)
    print(f"  [setup] Wrote Raman KPOINTS ({RAMAN_KPOINTS_MESH}) to raman/")
    run_command(f"cp input/POTCAR {RAMAN_DIR}/", cwd=WORK_DIR)
    print("  [setup] Copied CONTCAR + INCAR (from YAML config), POTCAR (from input/) to RAMAN_DIR.")
    print(f"  [setup] KPOINTS generated from config raman_kpoints.mesh={RAMAN_KPOINTS_MESH}.")
    write_status(11, "completed", "CONTCAR and INCAR copied to raman dir")

# ── Step 12: chdir to RAMAN_DIR (always runs; status only if START_STEP <= 12) ─
if START_STEP <= 12:
    write_status(12, "completed", "Navigated to Raman dir")

print("\n--- Step 12: Navigate to Raman dir ---")
os.chdir(RAMAN_DIR)
print(f"Current working directory: {os.getcwd()}")

# ── Step 13: Generate Raman displacements and organize ──────────────────────
if START_STEP <= 13:
    write_status(13, "running", "Generate Raman displacements and organize")

    print("\n--- Step 13: Generate Raman displacements and organize ---")

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

# ── Step 14: Resonant VASP ───────────────────────────────────────────────────
if START_STEP <= 14:
    write_status(14, "running", "Resonant VASP runs in all ra_pos_* folders")
    print("\n--- Step 14: Run resonant Raman calculations ---")
    # Use local fixed copy (original has scancel on line 59)
    LOCAL_RUN_ALL_VASP = os.path.join(SCRIPT_DIR, "run_all_vasp_folders_fixed.sh")
    if not os.path.exists(LOCAL_RUN_ALL_VASP):
        raise FileNotFoundError(
            f"run_all_vasp_folders_fixed.sh not found at {LOCAL_RUN_ALL_VASP}. "
            f"This script is required for Step 14 resonant VASP runs."
        )
    # Guard: spot-check the first ra_pos_* POSCAR — Raman displacement files are
    # generated from scratch (not copied from input/POSCAR) so Selective Dynamics
    # should never be present.
    ra_pos_dirs = sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if ra_pos_dirs:
        check_no_selective_dynamics(
            os.path.join(ra_pos_dirs[0], "POSCAR"),
            "ra_pos_* POSCAR — Raman displacement file"
        )
    # Export SRUN_ARGS so run_all_vasp_folders_fixed.sh respects GPU/CPU mode
    run_command(f"export SRUN_ARGS='{SRUN_ARGS}' && bash {LOCAL_RUN_ALL_VASP}")
    # Validate VASP convergence in every ra_pos_* directory
    ra_dirs_step14 = sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if not ra_dirs_step14:
        raise RuntimeError(
            "Step 14 produced no ra_pos_* directories — "
            "run_all_vasp_folders_fixed.sh likely failed silently."
        )
    for d in ra_dirs_step14:
        check_vasp_convergence(d, "step-14")
    write_status(14, "completed", "Resonant VASP runs finished — "
                 f"{len(ra_dirs_step14)} directories validated")

# ── Step 15: Kopia post-processing ───────────────────────────────────────────
if START_STEP <= 15:
    # Dynamically generate kopia script (copies vasprun.xml from each ra_pos_* to AXML/)
    write_status(15, "running", "Kopia post-processing")

    print("\n--- Step 15: Run kopia post-processing ---")
    # Find all ra_pos_* directories that contain vasprun.xml
    ra_dirs = sorted(glob.glob(os.path.join(RAMAN_DIR, "ra_pos_*")))
    if not ra_dirs:
        write_status(15, "failed", "No ra_pos_* directories found — Step 13 (runRA) likely failed")
        raise RuntimeError(
            "Step 15 failed: no ra_pos_* directories found in RAMAN_DIR. "
            "Step 13 (runRA) must create these before kopia can run."
        )
    else:
        generate_kopia_script(RAMAN_DIR, ra_dirs)
        # Verify AXML/ was populated with non-empty XML files.
        # Kopia runs without error-checking, so 0-byte or missing vasprun.xml
        # files are copied silently — catching them here avoids the cryptic
        # "File: B1a.xml not found" error from genRAram610_dynamic in Step 16.
        axml_dir = os.path.join(RAMAN_DIR, "AXML")
        if not os.path.isdir(axml_dir):
            raise RuntimeError(
                "Step 15 failed: kopia ran but AXML/ directory was not created."
            )
        axml_files = [f for f in os.listdir(axml_dir) if f.endswith(".xml")]
        if not axml_files:
            raise RuntimeError(
                "Step 15 failed: AXML/ exists but contains no .xml files — "
                "kopia may have failed to copy any vasprun.xml."
            )
        empty_xml = [f for f in axml_files
                     if os.path.getsize(os.path.join(axml_dir, f)) == 0]
        if empty_xml:
            raise RuntimeError(
                f"Step 15 failed: {len(empty_xml)} AXML/*.xml file(s) are empty (0 bytes). "
                f"The corresponding ra_pos_*/vasprun.xml files are missing or empty: "
                f"{', '.join(empty_xml[:5])}{'...' if len(empty_xml) > 5 else ''}. "
                f"Check Step 14 VASP convergence in those directories."
            )
        print(f"  [verify] AXML/ contains {len(axml_files)} valid XML files")
        write_status(15, "completed", "Kopia post-processing done")

# ── Step 16: RAMFILE generation ──────────────────────────────────────────────
if START_STEP <= 16:
    # Generate config-driven ramfile script with laser energies from settings
    write_status(16, "running", "RAMFILE generation")

    print("\n--- Step 16: Generate RAMFILE for each desired energy ---")

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

    # Run with BINARY_UTILITIES_DIR on PATH for genRAram610_dynamic
    run_command(f"export PATH={BINARY_UTILITIES_DIR}:$PATH && bash ramfile_dynamic.sh", cwd=RAMAN_DIR)

    for energy in DESIRED_ENERGIES:
        ramfile = os.path.join(store_ram, f"RAMFILE_{energy}")
        if not os.path.exists(ramfile):
            raise RuntimeError(
                f"Step 16 failed: ramfile_dynamic.sh produced no RAMFILE_{energy}. "
                f"Cannot continue without required RAMFILE for energy {energy} eV."
            )

    write_status(16, "completed", "RAMFILE generation done")

# ── Step 17: Copy static files to Raman dir + output dir ─────────────────────
if START_STEP <= 17:
    print("\n--- Step 17: Copying static files to Raman dir + output dir ---")
    # Static copies to raman/ (check_success=False for resume safety)
    run_command(f"cp {HFFILES_DIR}/band.yaml .", cwd=RAMAN_DIR, check_success=False)
    run_command(f"cp {HFFILES_DIR}/irreps.yaml .", cwd=RAMAN_DIR, check_success=False)
    # output/ — permanent results archive (--scratch: copied to HOME at end)
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
    # all_mode.txt (if it exists — Gamma-only skips phonopy_visualization)
    all_mode = os.path.join(HFFILES_DIR, "all_mode.txt")
    if os.path.exists(all_mode) and os.path.getsize(all_mode) > 0:
        run_command(f"cp {all_mode} {output_dir}/", cwd=WORK_DIR, check_success=False)
    # mode* files (phonopy_visualization output, if any)
    for mf in glob.glob(os.path.join(HFFILES_DIR, "mode*")):
        if os.path.getsize(mf) > 0:
            run_command(f"cp {mf} {output_dir}/", cwd=WORK_DIR, check_success=False)
    write_status(17, "completed", f"Static files copied to output/ ({output_dir})")

# ── Steps 18-20: Energy processing loop ──────────────────────────────────────
if START_STEP <= 18:
    print("\n--- Step 18-20: Processing Raman results for each energy ---")
    # Status written once after loop (avoids overwriting per-iteration)
    write_status(18, "running", f"Processing energies: {', '.join(DESIRED_ENERGIES)} eV")

    # Validate binaries needed for this step exist before entering the energy loop
    for bin_name in ("raman_tensor", "broadening"):
        bin_path = os.path.join(BINARY_UTILITIES_DIR, bin_name)
        if not os.path.exists(bin_path):
            raise FileNotFoundError(
                f"{bin_name} not found at {bin_path}. "
                f"Cannot process Raman results without this binary."
            )

    # Validate RAMFILEs exist (Step 15 must have produced them)
    store_ram_step18 = os.path.join(RAMAN_DIR, "store_ramfile")
    for energy in DESIRED_ENERGIES:
        ramfile_check = os.path.join(store_ram_step18, f"RAMFILE_{energy}")
        if not os.path.exists(ramfile_check):
            raise RuntimeError(
                f"RAMFILE_{energy} not found in store_ramfile/. "
                f"Step 15 did not produce the required RAMFILE for energy {energy} eV. "
                f"Cannot continue."
            )

    for energy in DESIRED_ENERGIES:
        print(f"\n--- Processing for energy: {energy}eV ---")

        # A. Copy the energy-specific RAMFILE to working directory
        run_command("rm -f RAMFILE", cwd=RAMAN_DIR, check_success=False)
        run_command(f"cp store_ramfile/RAMFILE_{energy} RAMFILE", cwd=RAMAN_DIR)

        # B. Run raman_tensor (stdin via file redirect for Fortran reliability)
        raman_input = f"{RAMAN_INCIDENT_POL}\n{RAMAN_SCATTERED_POL}\n{RAMAN_SURFACE_NORMAL}\n"
        rt_file = os.path.join(RAMAN_DIR, ".raman_tensor_input")
        with open(rt_file, "w") as rf:
            rf.write(raman_input)
        # Suppress stdout (hide prompt echoes); keep stderr visible for errors
        run_command(
            f"{BINARY_UTILITIES_DIR}/raman_tensor < .raman_tensor_input > /dev/null",
            cwd=RAMAN_DIR, check_success=not CPU_FLAG
        )
        os.remove(rt_file)
        print(f"    [energy {energy}eV] Raman tensor computed with pol=({RAMAN_INCIDENT_POL},{RAMAN_SCATTERED_POL})")

        # C. Run broadening script
        # raman_tensor leaves broadening_input empty (0 bytes) — write correct config here
        b_input = os.path.join(RAMAN_DIR, "broadening_input")
        b_content = (
            "Raman_intensity_complex  !!! the file name\n"
            "2            !!! peak broadening mode (1 for Gaussian, 2 for Lorentzian)\n"
            "1            !!! half width at half maximum (cm-1)\n"
            "200          !!! number of data points inserted between two old data points\n"
            "2            !!! normalization\n"
        )
        with open(b_input, "w") as bf:
            bf.write(b_content)
        print(f"  [setup] Wrote broadening_input for {energy}eV")
        run_command(f"{BINARY_UTILITIES_DIR}/broadening", cwd=RAMAN_DIR)

        # D. Rename output files with energy suffix (check existence first)
        if os.path.exists(os.path.join(RAMAN_DIR, "Raman_intensity_complex")):
            run_command(f"mv Raman_intensity_complex Raman_intensity_complex_{energy}eV", cwd=RAMAN_DIR)
        else:
            print(f"WARNING: Raman_intensity_complex not found for {energy}eV.")

        if os.path.exists(os.path.join(RAMAN_DIR, "Raman_intensity_complex_broadening")):
            run_command(f"mv Raman_intensity_complex_broadening Raman_intensity_complex_broadening_{energy}eV", cwd=RAMAN_DIR)
        else:
            print(f"WARNING: Raman_intensity_complex_broadening not found for {energy}eV.")

    write_status(18, "completed", f"Raman tensor computed for all energies: {', '.join(DESIRED_ENERGIES)} eV")
    write_status(20, "completed", f"All energies processed: {', '.join(DESIRED_ENERGIES)} eV")

    # ---- Step 21: Generate Raman spectra plots via SpectroPy -----------------
    print("\n--- Step 21: Generating Raman spectra plots ---")
    write_status(21, "running", "Generating publication-style Raman plots via SpectroPy")

    spectropy_dir = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "SpectroPy"))
    generate_plots = os.path.join(spectropy_dir, "generate_raman_plots.py")

    # Create energy subdirectories with header-formatted data files
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
        else:
            print(f"  WARNING: {src_file} not found -- skipping {energy_label}")

    # Run SpectroPy plot generator
    if os.path.exists(generate_plots):
        # Run from RAMAN_DIR so os.walk finds the energy subdirs
        # Pipe "5.0\\n1" for default FWHM=5.0 and Lorentzian broadening
        run_command(
            f"echo -e '5.0\\nl' | python3 {generate_plots}",
            cwd=RAMAN_DIR,
            check_success=False
        )
        write_status(21, "completed", "Raman spectra plots generated")
    else:
        print(f"  WARNING: SpectroPy plotter not found at {generate_plots}")
        write_status(21, "failed", f"SpectroPy plotter not found at {generate_plots}")

    # ---- Aggregate Raman spectra plots + data to output/ ---------------------
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
            print(f"  [output] Copied: raman_spectra/{energy_label}.png")

    for pattern in ("Raman_intensity_complex_*.eV", "Raman_intensity_complex_broadening_*.eV"):
        for f in glob.glob(os.path.join(RAMAN_DIR, pattern)):
            shutil.copy2(f, os.path.join(raman_data_out, os.path.basename(f)))
            print(f"  [output] Copied: raman_data/{os.path.basename(f)}")

    print("\n--- Automation workflow complete. ---")

    write_status("final", "completed", "Automation workflow complete")

    # --scratch: copy output/ from WORK_DIR to HOME (SCRATCH may be purged)
    if SCRATCH_FLAG:
        home_output = os.path.join(MATERIAL_DIR, "output")
        scratch_output = os.path.join(WORK_DIR, "output")
        if os.path.exists(scratch_output):
            print(f"\n  [scratch] Copying output/ from SCRATCH to HOME...")
            run_command(f"mkdir -p {home_output}", cwd=MATERIAL_DIR)
            run_command(f"cp -r {scratch_output}/* {home_output}/", cwd=MATERIAL_DIR,
                        check_success=False)
            print(f"  [scratch] Results saved to: {home_output}")
        else:
            print(f"  [scratch] No output/ found on SCRATCH -- nothing to copy back.")

# --- Self-cancel salloc (interactive mode only; batch exits naturally) ---
if "SLURM_JOB_ID" in os.environ and os.environ.get("SLURM_SUBMIT_HOST", "") == "":
    run_command(f"{BINARY_UTILITIES_DIR}/end_salloc.sh", check_success=False)
else:
    print("Batch job mode detected — skipping end_salloc.sh (job will exit naturally).")
