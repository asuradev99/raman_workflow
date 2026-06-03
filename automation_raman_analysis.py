import argparse
import os
import re
import glob
import sys
from util import (run_command, fmt_time, calc_duration, ensure_dim_in_conf,
                  restore_z_lattice_vector, is_vasprun_valid, merge_config,
                  parse_resume_step, load_config, build_srun_args,
                  write_eigenvectors_conf, update_wavecar_symlinks,
                  update_chgcar_symlinks,
                  make_pipeline_excepthook)
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
HFFILES_DIR = os.path.join(MATERIAL_DIR, "hf")
RAMAN_DIR = os.path.join(MATERIAL_DIR, "raman")

# Preliminary WORK_DIR (refined after config load below)
if SCRATCH_FLAG:
    SCRATCH_BASE = os.environ.get("SCRATCH", "")
    if not SCRATCH_BASE:
        print("Error: --scratch flag requires $SCRATCH environment variable.")
        sys.exit(1)
    WORK_DIR = os.path.join(SCRATCH_BASE, "vasp_work", CWD_BASENAME)
else:
    WORK_DIR = MATERIAL_DIR
# Workflow status file (tracks step completion)
STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(MATERIAL_DIR, "workflow_status.txt"))

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

    # Status file (always on HOME)
    if os.path.exists(STATUS_FILE):
        os.remove(STATUS_FILE)
        print(f"  Removed status file: {STATUS_FILE}")

    print(f"\n  [restart] Done — input/ and workflow_settings.yaml preserved.")
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
CONFIG_PATH = os.path.join(MATERIAL_DIR, "workflow_settings.yaml")
SHARED_CONFIG_PATH = os.path.join(BASE_PROJECT_DIR, "shared_workflow_settings.yaml")
# Shared INCAR bases (cat with per-material overrides)
SHARED_INCAR_RELAX = os.path.join(BASE_PROJECT_DIR, "incar_relax.base")
SHARED_INCAR_HF = os.path.join(BASE_PROJECT_DIR, "incar_hf.base")
SHARED_INCAR_DIELEC = os.path.join(BASE_PROJECT_DIR, "incar_dielec.base")
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
if SCRATCH_FLAG:
    WORK_DIR = os.path.join(os.environ["SCRATCH"], "vasp_work", MATERIAL_NAME)
    HFFILES_DIR = os.path.join(WORK_DIR, "hf")
    RAMAN_DIR = os.path.join(WORK_DIR, "raman")
    print(f"  [scratch] WORK_DIR = {WORK_DIR}")
else:
    WORK_DIR = MATERIAL_DIR
    HFFILES_DIR = os.path.join(MATERIAL_DIR, "hf")
    RAMAN_DIR = os.path.join(MATERIAL_DIR, "raman")
STATUS_FILE = os.environ.get("STATUS_FILE", os.path.join(MATERIAL_DIR, "workflow_status.txt"))
# Install exception hook now that STATUS_FILE is finalised
sys.excepthook = make_pipeline_excepthook(STATUS_FILE)

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
    print(f"\n  [scratch] Syncing input/ + workflow_settings.yaml from HOME to SCRATCH...")
    print(f"  [scratch] Source: {MATERIAL_DIR}/input")
    print(f"  [scratch] Target: {WORK_DIR}")
    run_command(f"mkdir -p {WORK_DIR}", cwd=MATERIAL_DIR)
    run_command(f"cp -r input {WORK_DIR}/", cwd=MATERIAL_DIR)
    run_command(f"cp workflow_settings.yaml {WORK_DIR}/", cwd=MATERIAL_DIR)
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
    # Dual-INCAR: INCAR_relax (NSW=200) in scf/; INCAR_{hf,dielec} generated downstream
    input_dir = os.path.join(MATERIAL_DIR, "input")
    incar_relax_path = os.path.join(input_dir, "INCAR_relax")
    incar_dielec_path = os.path.join(input_dir, "INCAR_dielec")
    incar_hf_path = os.path.join(input_dir, "INCAR_hf")
    if not os.path.exists(incar_relax_path) or not os.path.exists(incar_dielec_path) or not os.path.exists(incar_hf_path):
        raise FileNotFoundError(
            f"Step 3 requires input/INCAR_relax, input/INCAR_dielec, and input/INCAR_hf in {MATERIAL_DIR}. "
            f"Found relax={os.path.exists(incar_relax_path)}, "
            f"dielec={os.path.exists(incar_dielec_path)}, "
            f"hf={os.path.exists(incar_hf_path)}. "
            "Create all three files (see hBN materials for examples) or add this material to the pipeline."
        )
    # Copy VASP inputs to scf/
    for vasp_input in ("POSCAR", "POTCAR", "KPOINTS"):
        src = os.path.join(input_dir, vasp_input)
        if os.path.exists(src):
            run_command(f"cp input/{vasp_input} scf/{vasp_input}", cwd=WORK_DIR)
        else:
            print(f"  [setup] WARNING: input/{vasp_input} not found — VASP may fail.")
    run_command(f"cat {SHARED_INCAR_RELAX} input/INCAR_relax > scf/INCAR", cwd=WORK_DIR)
    run_command(f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > relaxation.stdout",
                cwd=scf_dir)
    # Restore z lattice vector (ISIF=4 can shrink vacuum layer)
    restore_z_lattice_vector(WORK_DIR)
    write_status(3, "completed", "Initial VASP relaxation finished")


# ── Step 4: Supercell relaxation + static groundstate ────────────────────────
if START_STEP <= 4:
    write_status(4, "running", "Supercell relaxation + static groundstate")

    print("\n--- Step 4: Supercell relaxation + static groundstate ---")
    relax_dir = os.path.join(HFFILES_DIR, "relax")
    run_command(f"mkdir -p {relax_dir}", cwd=WORK_DIR)

    # 4a. Generate supercell from relaxed unit cell (in hf/ — shared with Step 6)
    print("  [setup] Creating supercell in hf/ via phonopy (shared with force-constant step)...")
    run_command(f"cp scf/CONTCAR {HFFILES_DIR}/POSCAR_unitcell", cwd=WORK_DIR)
    run_command(
        f"phonopy -d --dim=\"{PHONOPY_DIM}\" --amplitude={PHONOPY_AMPLITUDE} "
        f"-c POSCAR_unitcell",
        cwd=HFFILES_DIR,
    )
    # SPOSCAR = perfect supercell; copy to relax/ as starting structure
    run_command(f"cp SPOSCAR {relax_dir}/POSCAR", cwd=HFFILES_DIR)
    print("  [setup] POSCAR-* displacement files + SPOSCAR now in hf/ — Step 6 will reuse them")

    # 4b. Set up VASP inputs for relaxation
    # INCAR = incar_relax.base + input/INCAR_relax (same convergence criteria as Step 3)
    incar_relax_path = os.path.join(MATERIAL_DIR, "input", "INCAR_relax")
    if not os.path.exists(incar_relax_path):
        raise FileNotFoundError(
            f"Step 4 requires input/INCAR_relax in {MATERIAL_DIR}."
        )
    run_command(
        f"cat {SHARED_INCAR_RELAX} input/INCAR_relax > {relax_dir}/INCAR",
        cwd=WORK_DIR,
    )
    # KPOINTS from hf_kpoints config (same mesh as force-constant calculations)
    relax_kpoints_path = os.path.join(relax_dir, "KPOINTS")
    with open(relax_kpoints_path, "w") as rk:
        rk.write("K-points for supercell relaxation\n")
        rk.write("0\n")
        rk.write("Gamma\n")
        rk.write(f"{HF_KPOINTS_MESH}\n")
        rk.write(f"{HF_KPOINTS_SHIFT}\n")
    print(f"  [setup] Wrote supercell KPOINTS ({HF_KPOINTS_MESH}) to hf/relax/")
    run_command(f"cp input/POTCAR {relax_dir}/", cwd=WORK_DIR)

    # 4c. VASP relaxation of supercell (NSW=200, IBRION=2, ISIF=4)
    print("  [vasp] Running supercell relaxation (same convergence criteria as Step 3)...")
    print(f"  [vasp] This is the sanity check: verifying per-atom energy convergence "
          f"at the supercell level ({PHONOPY_DIM}).")
    run_command(
        f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > relaxation.stdout",
        cwd=relax_dir,
    )
    # Note: ISIF=4 with vacuum in 2D materials can shrink the interlayer spacing,
    # but for the supercell this is physically meaningful — no restore needed
    # since the vacuum layer is already large enough (~15+ Å).

    # 4c-ii. Count ionic steps — sanity check metric
    relax_outcar = os.path.join(relax_dir, "OUTCAR")
    n_ionic_steps = 0
    if os.path.exists(relax_outcar):
        with open(relax_outcar) as f:
            for line in f:
                if re.match(r"\s+Iteration\s+\d+\(\s*\d+\)", line):
                    n_ionic_steps += 1
        print(f"  [info] Supercell relaxation converged in {n_ionic_steps} ionic step(s)")
        if n_ionic_steps > 3:
            print(f"  ⚠️  WARNING: {n_ionic_steps} steps is high (expected ≤3). "
                  f"The supercell may be far from equilibrium or the "
                  f"convergence criteria may be too strict.")
    else:
        print(f"  ⚠️  WARNING: OUTCAR not found in {relax_dir} — cannot count ionic steps")

    # 4d. Static groundstate on relaxed supercell → CHGCAR + WAVECAR
    print("  [vasp] Running static groundstate on relaxed supercell (generating CHGCAR + WAVECAR)...")
    static_incar_path = os.path.join(relax_dir, "INCAR_static")
    run_command(
        f"cat {SHARED_INCAR_HF} input/INCAR_hf > {static_incar_path}",
        cwd=WORK_DIR,
    )
    # Append overrides: LCHARG=TRUE, LWAVE=TRUE (override .FALSE. from incar_hf.base)
    with open(static_incar_path, "a") as si:
        si.write("LCHARG= .TRUE.\n")
        si.write("LWAVE =.TRUE.\n")
    run_command(f"cp INCAR_static INCAR", cwd=relax_dir)
    # Use the relaxed structure for the static run
    run_command(f"cp CONTCAR POSCAR", cwd=relax_dir)
    run_command(
        f"srun {SRUN_ARGS} {VASP_BINARY_PATH} > static.stdout",
        cwd=relax_dir,
    )

    # 4e. Copy relaxed supercell CONTCAR to hf/ for reference
    # (CHGCAR and WAVECAR stay in relax/ — symlinked from groundstate/ in Step 8)
    run_command(f"cp CONTCAR {HFFILES_DIR}/CONTCAR_supercell_relaxed", cwd=relax_dir)

    print("  [done] Step 4 complete — CHGCAR + WAVECAR in hf/relax/ ready for force-constant seeding.")
    write_status(4, "completed", "Supercell relaxation + static groundstate done — "
                 "CHGCAR/WAVECAR in hf/relax/")


# ── Step 5: Copy files to hf/ ───────────────────────────────────────────────
if START_STEP <= 5:
    write_status(5, "running", "Copy files to hf/")
    print("\n--- Step 5: Copy POTCAR + KPOINTS + INCAR to hf/ ---")
    run_command(f"mkdir -p {HFFILES_DIR}", cwd=WORK_DIR)
    # POSCAR_unitcell = relaxed unit cell (primitive) for phonopy displacement generation
    run_command(f"cp scf/CONTCAR {HFFILES_DIR}/POSCAR_unitcell", cwd=WORK_DIR)
    # Force-constant INCAR (NSW=0, no LOPTICS) from incar_hf.base + per-material INCAR_hf
    run_command(f"cat {SHARED_INCAR_HF} input/INCAR_hf > {HFFILES_DIR}/INCAR", cwd=WORK_DIR)
    # Coarse KPOINTS for force constants (configured via hf_kpoints)
    hf_kpoints_path = os.path.join(HFFILES_DIR, "KPOINTS")
    with open(hf_kpoints_path, "w") as hk:
        hk.write("K-points for force-constant calculation (coarse mesh)\n")
        hk.write("0\n")
        hk.write("Gamma\n")
        hk.write(f"{HF_KPOINTS_MESH}\n")
        hk.write(f"{HF_KPOINTS_SHIFT}\n")
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
    run_command(f"{BINARY_UTILITIES_DIR}/runHF", cwd=HFFILES_DIR)
    write_status(7, "completed", "runHF folder organization done")

# ── Step 8: Symlink CHGCAR + WAVECAR from hf/relax/ into groundstate/ ────────
# Step 4 generated CHGCAR and WAVECAR in hf/relax/ (static groundstate of the
# relaxed supercell).  Instead of running VASP again, we symlink from relax/
# into groundstate/ so that the existing update_wavecar_symlinks() function
# creates the displacement-dir links pointing to them.
if START_STEP <= 8:
    write_status(8, "running", "CHGCAR + WAVECAR symlinks from relax/ to groundstate/")

    print("\n--- Step 8: Symlink CHGCAR + WAVECAR from hf/relax/ into groundstate/ ---")
    groundstate_dir = os.path.join(HFFILES_DIR, "groundstate")
    relax_dir = os.path.join(HFFILES_DIR, "relax")

    # Ensure groundstate/ exists (re-run runHF if missing — it's idempotent)
    if not os.path.isdir(groundstate_dir):
        print("  groundstate/ not found — re-running runHF to recreate it")
        run_command(f"{BINARY_UTILITIES_DIR}/runHF", cwd=HFFILES_DIR)

    # Symlink groundstate/CHGCAR → ../relax/CHGCAR
    gs_chgcar = os.path.join(groundstate_dir, "CHGCAR")
    relax_chgcar = os.path.join(relax_dir, "CHGCAR")
    if os.path.islink(gs_chgcar) or os.path.exists(gs_chgcar):
        os.remove(gs_chgcar)
    if os.path.exists(relax_chgcar):
        os.symlink("../relax/CHGCAR", gs_chgcar)
        print(f"  Symlinked groundstate/CHGCAR → ../relax/CHGCAR")
    else:
        print(f"  WARNING: {relax_chgcar} not found — no CHGCAR seeding available")

    # Symlink groundstate/WAVECAR → ../relax/WAVECAR
    gs_wavecar = os.path.join(groundstate_dir, "WAVECAR")
    relax_wavecar = os.path.join(relax_dir, "WAVECAR")
    if os.path.islink(gs_wavecar) or os.path.exists(gs_wavecar):
        os.remove(gs_wavecar)
    if os.path.exists(relax_wavecar):
        os.symlink("../relax/WAVECAR", gs_wavecar)
        print(f"  Symlinked groundstate/WAVECAR → ../relax/WAVECAR")
    else:
        print(f"  WARNING: {relax_wavecar} not found — no WAVECAR seeding available")

    # Replace dangling runHF symlinks with direct groundstate/WAVECAR links
    update_wavecar_symlinks(HFFILES_DIR)
    # Create CHGCAR symlinks in displacement dirs (parallel to WAVECAR)
    update_chgcar_symlinks(HFFILES_DIR)

    write_status(8, "completed", "CHGCAR + WAVECAR symlinks created — "
                 "displacement runs seeded from hf/relax/")

# ── Step 9: VASP force constants ─────────────────────────────────────────────
if START_STEP <= 9:
    write_status(9, "running", "VASP in all hf_POSCAR folders (force constants)")

    print("\n--- Step 9: Run VASP in all hf_POSCAR folders ---")
    vasp9_ok = vasp_loop_check_and_restart(f"{BINARY_UTILITIES_DIR}/automate_hfiles.sh", max_restarts=VASP_MAX_RESTARTS)
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

    # Run phonopy eigenvectors
    run_command("phonopy -c CONTCAR eigenvectors.conf", cwd=HFFILES_DIR)

    # phonopy_visualization (CUDA-linked; skip gracefully on CPU)
    pv_cmd = f"export PATH={BINARY_UTILITIES_DIR}:$PATH && echo -e '1\\nno' | phonopy_visualization"
    if CPU_FLAG:
        run_command(pv_cmd, cwd=HFFILES_DIR, check_success=False)
    else:
        run_command(pv_cmd, cwd=HFFILES_DIR)

    # phonopy symmetry (irreps at Gamma)
    ensure_dim_in_conf(os.path.join(HFFILES_DIR, "symmetry.conf"), "symmetry.conf", PHONOPY_DIM)
    run_command("phonopy -c CONTCAR symmetry.conf", cwd=HFFILES_DIR)
    # Skip phonopy_symmetry on CPU (all_mode.txt missing without CUDA)
    all_mode_path = os.path.join(HFFILES_DIR, "all_mode.txt")
    if CPU_FLAG and not os.path.exists(all_mode_path):
        print("  [cpu] phonopy_symmetry skipped (all_mode.txt not available on CPU node)")
    else:
        run_command(f"{BINARY_UTILITIES_DIR}/phonopy_symmetry", cwd=HFFILES_DIR)

    if not os.path.exists(os.path.join(HFFILES_DIR, "all_mode.txt")):
        print("WARNING: all_mode.txt was not created by phonon postprocessing.")
    write_status(10, "completed", "Phonon postprocessing done")

# ── Step 11: Copy CONTCAR + VASP inputs to raman dir ─────────────────────────
if START_STEP <= 11:
    write_status(11, "running", "Copy CONTCAR to raman dir")

    print("\n--- Step 11: Copy CONTCAR + INCAR + KPOINTS + POTCAR to Raman dir ---")
    run_command(f"mkdir -p {RAMAN_DIR}", cwd=WORK_DIR)
    # Dielectric INCAR from shared base + per-material override (not scf/INCAR)
    run_command(f"cp scf/CONTCAR {RAMAN_DIR}/CONTCAR", cwd=WORK_DIR)
    run_command(f"cat {SHARED_INCAR_DIELEC} input/INCAR_dielec > {RAMAN_DIR}/INCAR", cwd=WORK_DIR)
    # Resonant Raman KPOINTS from config (raman_kpoints.mesh), not from input/KPOINTS
    raman_kpoints_path = os.path.join(RAMAN_DIR, "KPOINTS")
    with open(raman_kpoints_path, "w") as rk:
        rk.write("K-points for resonant Raman calculation (dense mesh)\n")
        rk.write("0\n")
        rk.write("Gamma\n")
        rk.write(f"{RAMAN_KPOINTS_MESH}\n")
        rk.write(f"{RAMAN_KPOINTS_SHIFT}\n")
    print(f"  [setup] Wrote Raman KPOINTS ({RAMAN_KPOINTS_MESH}) to raman/")
    run_command(f"cp input/POTCAR {RAMAN_DIR}/", cwd=WORK_DIR)
    print("  [setup] Copied CONTCAR + INCAR (from scf/), POTCAR (from input/) to RAMAN_DIR.")
    print(f"  [setup] KPOINTS generated from config raman_kpoints.mesh={RAMAN_KPOINTS_MESH}.")
    print("  [setup] LOPTICS/NEDOS/OMEGAMAX come from incar_dielec.base; NBANDS from per-material input/INCAR_dielec.")
    print("  [setup] hf/INCAR uses incar_hf.base (no LOPTICS/NEDOS/OMEGAMAX) + input/INCAR_hf (no NBANDS).")
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

    # CUDA-linked binaries — skip gracefully on CPU (check_success=not CPU_FLAG)
    run_command(f"{BINARY_UTILITIES_DIR}/ramdiscar", check_success=not CPU_FLAG)

    go_file = os.path.join(RAMAN_DIR, ".go_input")
    with open(go_file, "w") as gf:
        gf.write("go\n")
    run_command(f"{BINARY_UTILITIES_DIR}/genRApos610 < {go_file}", check_success=not CPU_FLAG)
    os.remove(go_file)

    run_command(f"{BINARY_UTILITIES_DIR}/runRA")
    write_status(13, "completed", "Raman displacements generated and organized")

# ── Step 14: Resonant VASP ───────────────────────────────────────────────────
if START_STEP <= 14:
    write_status(14, "running", "Resonant VASP runs in all ra_pos_* folders")
    print("\n--- Step 14: Run resonant Raman calculations ---")
    # Use local fixed copy (original has scancel on line 59)
    LOCAL_RUN_ALL_VASP = os.path.join(SCRIPT_DIR, "run_all_vasp_folders_fixed.sh")
    run_command(f"bash {LOCAL_RUN_ALL_VASP}")
    write_status(14, "completed", "Resonant VASP runs finished")

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
        kopia_path = os.path.join(RAMAN_DIR, "kopia")
        with open(kopia_path, "w") as kf:
            kf.write("#!/bin/bash\n")
            kf.write("# Dynamically generated by automation_raman_analysis.py Step 15\n")
            kf.write("mkdir -p AXML\n")
            for d in ra_dirs:
                dirname = os.path.basename(d)
                # genRAram610_dynamic expects "B1a.xml" not "ra_pos_B1a.xml"
                xml_name = dirname[len("ra_pos_"):] if dirname.startswith("ra_pos_") else dirname
                kf.write(f'cp "{dirname}/vasprun.xml" "AXML/{xml_name}.xml"\n')
        # Make executable and run
        run_command(f"chmod +x kopia && ./kopia", cwd=RAMAN_DIR)
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

    with open(ramfile_script_src) as f:
        script_template = f.read()

    energies_str = " ".join(f'"{e}"' for e in DESIRED_ENERGIES)
    # Use regex to find the desired_energies=(...) line regardless of its current values.
    # This avoids fragility if the template ships with different default energies.
    energy_line_match = re.search(r'^desired_energies=\([^)]*\)', script_template, re.MULTILINE)
    if not energy_line_match:
        raise RuntimeError(
            "ramfile_dynamic.sh does not contain the expected "
            "'desired_energies=(...)' line — "
            "cannot inject custom energies from workflow_settings.yaml. "
            "Ensure the template has a line like: desired_energies=(\"1.96\" \"2.33\")"
        )
    script_content = script_template.replace(
        energy_line_match.group(0),
        f"desired_energies=({energies_str})"
    )

    ramfile_script_dst = os.path.join(RAMAN_DIR, "ramfile_dynamic.sh")
    with open(ramfile_script_dst, "w") as f:
        f.write(script_content)
    os.chmod(ramfile_script_dst, 0o755)
    print(f"  [setup] Generated ramfile_dynamic.sh with energies: {energies_str}")

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
    print("\n--- Step 17: Copying static Band/Irreps files to Raman dir + output dir ---")
    # Static copies (outside energy loop); check_success=False for resume safety
    run_command(f"cp {HFFILES_DIR}/band.yaml .", cwd=RAMAN_DIR, check_success=False)
    run_command(f"cp {HFFILES_DIR}/irreps.yaml .", cwd=RAMAN_DIR, check_success=False)
    # Also copy band.yaml to output/ (--scratch: copied to HOME after pipeline)
    output_dir = os.path.join(WORK_DIR, "output")
    run_command(f"mkdir -p {output_dir}", cwd=WORK_DIR)
    run_command(f"cp {HFFILES_DIR}/band.yaml {output_dir}/", cwd=WORK_DIR, check_success=False)
    write_status(17, "completed", "Static band/irreps files copied; band.yaml placed in output/")

# ── Steps 18-20: Energy processing loop ──────────────────────────────────────
if START_STEP <= 18:
    print("\n--- Step 18-20: Processing Raman results for each energy ---")
    # Status written once after loop (avoids overwriting per-iteration)
    write_status(18, "running", f"Processing energies: {', '.join(DESIRED_ENERGIES)} eV")

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

    print("\n--- Automation workflow complete. ---")

    write_status(18, "completed", f"Raman tensor computed for all energies: {', '.join(DESIRED_ENERGIES)} eV")
    write_status(20, "completed", f"All energies processed: {', '.join(DESIRED_ENERGIES)} eV")

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
            print(f"  [scratch] No output/ found on SCRATCH — nothing to copy back.")

# --- Self-cancel salloc (interactive mode only; batch exits naturally) ---
if "SLURM_JOB_ID" in os.environ and os.environ.get("SLURM_SUBMIT_HOST", "") == "":
    run_command(f"{BINARY_UTILITIES_DIR}/end_salloc.sh", check_success=False)
else:
    print("Batch job mode detected — skipping end_salloc.sh (job will exit naturally).")
