"""
Utility functions for the Raman automation pipeline.

This module provides self-contained helper functions extracted from
automation_raman_analysis.py for better organization. Functions that
are tightly coupled to the pipeline's global state remain in the main script.
"""

import os
import re
import subprocess
import sys
import time
import traceback
import yaml


def run_command(command, cwd=None, shell=True, check_success=True):
    """
    Executes a shell command.
    Args:
        command (str): The shell command to execute.
        cwd (str, optional): The current working directory for the command.
        shell (bool): Whether to use the shell. Defaults to True.
        check_success (bool): If True, raises RuntimeError if command exits with non-zero code.
    """
    print(f"\n--- Running: {command} ---")
    if cwd:
        print(f"--- In directory: {cwd} ---")

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            shell=shell,
            executable="/bin/bash",
            text=True,
        )
        process.wait()

        if check_success and process.returncode != 0:
            raise RuntimeError(f"Command failed with exit code {process.returncode}: {command}")
        print("--- Command completed successfully ---")
    except Exception as e:
        print(f"--- ERROR: {e} ---")
        if check_success:
            raise


def fmt_time(ts):
    """Format a Unix timestamp to a human-readable UTC string."""
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def calc_duration(start_ts, end_ts):
    """Calculate a human-readable duration between two Unix timestamps."""
    secs = end_ts - start_ts
    if secs < 60:
        return f"{secs:.0f}s"
    elif secs < 3600:
        return f"{secs//60:.0f}m {secs%60:.0f}s"
    else:
        return f"{secs//3600:.0f}h {(secs%3600)//60:.0f}m"


def ensure_dim_in_conf(conf_path, label, dim):
    """Ensure a phonopy .conf file exists with DIM + default IRREPS setting.

    - If the file doesn't exist: create it with DIM and IRREPS = 0 0 0
    - If the file exists but lacks DIM: prepend DIM (preserving existing content)
    - If the file exists with DIM: no-op

    Always returns True (creates if missing).
    """
    if not os.path.exists(conf_path):
        print(f"  [setup] {label} not found — creating with DIM = {dim} + IRREPS = 0 0 0...")
        with open(conf_path, "w") as f:
            f.write(f"DIM = {dim}\n")
            f.write("IRREPS = 0 0 0\n")
        return True
    with open(conf_path) as f:
        content = f.read()
    if "DIM" not in content:
        print(f"  [setup] {label} lacks DIM — prepending DIM = {dim}...")
        with open(conf_path, "w") as f:
            f.write(f"DIM = {dim}\n")
            f.write(content)
    return True


def restore_z_lattice_vector(material_dir):
    """
    Replace the 3rd lattice vector (z-axis) in CONTCAR with the original value
    from input/POSCAR. This prevents vacuum compression in 2D slab calculations
    while preserving in-plane (x,y) relaxation.

    Args:
        material_dir: Path to the material directory (e.g., /path/to/hBN_PBE)
    """
    poscar_path = os.path.join(material_dir, "input", "POSCAR")
    # CONTCAR lives in scf/ (VASP runs there)
    contcar_path = os.path.join(material_dir, "scf", "CONTCAR")

    if not os.path.exists(poscar_path):
        print("  [z-fix] input/POSCAR not found — cannot restore z lattice vector. Skipping.")
        return
    if not os.path.exists(contcar_path):
        print("  [z-fix] CONTCAR not found — nothing to fix. Skipping.")
        return

    # Read original 3rd lattice vector from input/POSCAR (line 4, 0-indexed)
    with open(poscar_path) as pf:
        poscar_lines = pf.readlines()
    if len(poscar_lines) < 5:
        print(f"  [z-fix] input/POSCAR has only {len(poscar_lines)} lines — unexpected format. Skipping.")
        return
    orig_z_line = poscar_lines[4].strip()
    orig_z_parts = orig_z_line.split()
    if len(orig_z_parts) < 3:
        print(f"  [z-fix] Could not parse 3rd lattice vector from input/POSCAR line 5: '{orig_z_line}'. Skipping.")
        return

    # Read relaxed CONTCAR
    with open(contcar_path) as cf:
        contcar_lines = cf.readlines()
    if len(contcar_lines) < 5:
        print(f"  [z-fix] CONTCAR has only {len(contcar_lines)} lines — unexpected format. Skipping.")
        return

    # Log what changed
    relaxed_z_line = contcar_lines[4].strip()
    print(f"  [z-fix] Original z lattice vector (input/POSCAR):  {orig_z_line}")
    print(f"  [z-fix] Relaxed z lattice vector (CONTCAR before): {relaxed_z_line}")

    # Replace the 3rd lattice vector in CONTCAR
    contcar_lines[4] = poscar_lines[4]
    with open(contcar_path, "w") as cf:
        cf.writelines(contcar_lines)

    print(f"  [z-fix] Restored z lattice vector in CONTCAR to:  {orig_z_line}")


def is_vasprun_valid(filepath):
    """Check vasprun.xml is non-trivial and has ``</modeling>`` closing tag."""
    try:
        if not os.path.exists(filepath):
            return False
        size = os.path.getsize(filepath)
        if size <= 1000:
            return False
        # Check last 4 KB for closing tag
        with open(filepath, "rb") as f:
            if size > 4096:
                f.seek(-4096, 2)
            tail = f.read()
        return b"</modeling>" in tail
    except (IOError, OSError):
        return False


def merge_config(target_config, file_config, label=""):
    """
    Deep-merge a YAML config dict into a target config dict.

    Skips keys starting with '_' (metadata). Nested dict sections are
    updated (merged) recursively; top-level non-dict values replace.
    """
    if file_config is None:
        return
    for section, values in file_config.items():
        if section.startswith("_"):
            continue  # skip metadata keys
        if isinstance(target_config.get(section), dict) and isinstance(values, dict):
            target_config[section].update(values)
        else:
            target_config[section] = values


def parse_resume_step(status_file, step_history, step_descriptions):
    """
    Parse a workflow status file to determine which step to resume from.

    Populates *step_history* with entries for steps marked COMPLETED so
    that write_status() can display an accurate history on restart.

    Returns:
        int: Step number to resume from (3–20), or ``None`` if all steps
        are already completed (caller should exit gracefully).
    """
    if not os.path.exists(status_file):
        print(f"[resume] No existing status file at {status_file}. Starting from step 3.")
        return 3

    try:
        with open(status_file) as f:
            content = f.read()

        completed_steps = set()
        for match in re.finditer(r'STEP\s+(\d+)\s+\[\s*COMPLETED\]', content):
            step_num = int(match.group(1))
            completed_steps.add(step_num)
            step_history[step_num] = {
                "status": "completed",
                "start_ts": 0,
                "end_ts": 0,
                "message": "Resumed — completed in previous run",
            }

        # Check for a step that was RUNNING (likely crashed)
        running_step = None
        for match in re.finditer(r'STEP\s+(\d+)\s+\[\s*RUNNING\]', content):
            running_step = int(match.group(1))

        if running_step is not None:
            print(f"[resume] Step {running_step} was RUNNING (likely failed). "
                  f"Retrying from step {running_step}.")
            return running_step

        # Find the first non-completed step
        all_step_keys = sorted(k for k in step_descriptions if isinstance(k, int))
        for s in all_step_keys:
            if s not in completed_steps:
                print(f"[resume] Continuing from step {s} "
                      f"({step_descriptions.get(s, 'Unknown')}).")
                return s

        print("[resume] All steps already completed. Nothing to do.")
        return None

    except Exception as e:
        print(f"[resume] Warning: Could not parse {status_file}: {e}")
        print("[resume] Starting from step 3 (full pipeline).")
        return 3


def make_pipeline_excepthook(status_file):
    """Return a sys.excepthook that appends a full traceback to *status_file* on crash."""
    def hook(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(tb_text, file=sys.stderr)
        try:
            with open(status_file, "a") as f:
                f.write("\n" + "=" * 80 + "\n")
                f.write("  UNHANDLED EXCEPTION — Full Traceback\n")
                f.write("=" * 80 + "\n")
                f.write(tb_text)
                f.write("=" * 80 + "\n")
        except Exception:
            pass
    return hook


def load_config(paths):
    """Load and deep-merge YAML config files in order; later files override earlier.

    Args:
        paths: iterable of (filepath, label) pairs. Missing files are silently skipped.
    Returns:
        dict: merged configuration
    """
    config = {}
    for path, label in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            merge_config(config, data, label)
            print(f"Loaded {label} config from {path}")
        except Exception as e:
            print(f"Warning: Could not load {label} config {path}: {e}")
    if not config:
        print("Warning: No config files loaded — all settings will be empty.")
    return config


def build_srun_args(config, cpu_flag=False):
    """Build an srun argument string from the pipeline config.

    Returns a string for use as: ``srun {SRUN_ARGS} {vasp_binary} ...``
    """
    if cpu_flag:
        cfg = config["vasp_srun_cpu"]
        return (f"--cpu_bind=cores --ntasks {cfg['ntasks']} "
                f"--cpus-per-task {cfg['cpus_per_task']}")
    else:
        cfg = config["vasp_srun"]
        return (f"--cpu_bind=cores --gpus {cfg['gpus']} "
                f"--ntasks {cfg['ntasks']} --cpus-per-task {cfg['cpus_per_task']} "
                f"-C {cfg['constraint']}")


def write_eigenvectors_conf(path, dim, band_path, band_labels, band_points):
    """Create or update *path* as a phonopy eigenvectors.conf file.

    Regenerates the file when it is missing or when DIM, BAND, or BAND_POINTS
    no longer match the supplied values. Returns True if the file was written.
    """
    expected_band = f"BAND = {band_path}"
    needs_recreation = False
    if os.path.exists(path):
        with open(path) as f:
            content = f.read()
        if "DIM" not in content:
            needs_recreation = True
            print("  [9b] eigenvectors.conf exists but lacks DIM — regenerating...")
        elif expected_band not in content:
            needs_recreation = True
            print(f"  [9b] eigenvectors.conf BAND has changed — regenerating...")
            print(f"       Expected: {expected_band}")
        elif "BAND_POINTS" not in content:
            needs_recreation = True
            print("  [9b] eigenvectors.conf lacks BAND_POINTS — regenerating...")
    else:
        needs_recreation = True
        print("  [9b] eigenvectors.conf not found — creating...")

    if needs_recreation:
        with open(path, "w") as f:
            f.write("# eigenvectors.conf — auto-generated by automation_raman_analysis.py\n")
            f.write(f"DIM = {dim}\n")
            f.write(f"BAND = {band_path}\n")
            if band_labels:
                f.write(f"BAND_LABELS = {band_labels}\n")
            f.write(f"BAND_POINTS = {band_points}\n")
            f.write("EIGENVECTORS = .TRUE.\n")

    return needs_recreation


def update_wavecar_symlinks(hffiles_dir):
    """Replace runHF's dangling ``../WAVECAR`` symlinks with ``../groundstate/WAVECAR``.

    runHF creates ``ln -s ../WAVECAR`` in each ``hf_POSCAR-*/`` pointing to hf/ level.
    This replaces them with a single-hop link to ``groundstate/WAVECAR``.

    Returns the number of symlinks updated, or 0 if ``groundstate/WAVECAR`` is absent.
    """
    gs_wavecar = os.path.join(hffiles_dir, "groundstate", "WAVECAR")
    if not os.path.exists(gs_wavecar):
        print("  WARNING: groundstate/WAVECAR not found — displacement runs will start from scratch")
        return 0

    displacement_dirs = sorted(
        d for d in os.listdir(hffiles_dir)
        if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(hffiles_dir, d))
    )
    for d in displacement_dirs:
        wav = os.path.join(hffiles_dir, d, "WAVECAR")
        if os.path.islink(wav):
            os.remove(wav)
        os.symlink("../groundstate/WAVECAR", wav)

    print(f"  Replaced symlinks in {len(displacement_dirs)} displacement dirs:")
    print(f"    hf_POSCAR-*/WAVECAR → ../groundstate/WAVECAR  (1 hop)")


def update_chgcar_symlinks(hffiles_dir):
    """Create ``../groundstate/CHGCAR`` symlinks in each ``hf_POSCAR-*/``.

    runHF does not create CHGCAR symlinks.  This function mirrors
    :func:`update_wavecar_symlinks` so that displacement VASP runs
    can read the precomputed charge density from the supercell static
    groundstate (generated in Step 4, stored in ``hf/relax/``, and
    symlinked into ``hf/groundstate/`` in Step 8).

    Returns the number of symlinks created, or 0 if ``groundstate/CHGCAR``
    is absent.
    """
    gs_chgcar = os.path.join(hffiles_dir, "groundstate", "CHGCAR")
    if not os.path.exists(gs_chgcar) and not os.path.islink(gs_chgcar):
        print("  WARNING: groundstate/CHGCAR not found — displacement runs will start without charge-density seeding")
        return 0

    displacement_dirs = sorted(
        d for d in os.listdir(hffiles_dir)
        if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(hffiles_dir, d))
    )
    for d in displacement_dirs:
        chg = os.path.join(hffiles_dir, d, "CHGCAR")
        if os.path.islink(chg) or os.path.exists(chg):
            os.remove(chg)
        os.symlink("../groundstate/CHGCAR", chg)

    print(f"  Created CHGCAR symlinks in {len(displacement_dirs)} displacement dirs:")
    print(f"    hf_POSCAR-*/CHGCAR → ../groundstate/CHGCAR  (1 hop)")
    return len(displacement_dirs)


# ── Workflow status tracking ──────────────────────────────────────────────────
# These constants and the write_status() function track pipeline progress.
# They originally lived in automation_raman_analysis.py but were moved here
# for better organization.

# Human-readable step descriptions
STEP_DESCRIPTIONS = {
    3: "Initial VASP relaxation",
    4: "Supercell relaxation + static groundstate",
    5: "Copy files to hf/",
    6: "Phonopy displacement generation",
    7: "Run runHF to organize displacement folders",
    8: "WAVECAR + CHGCAR symlinks (seeding)",
    9: "VASP in all hf_POSCAR folders (force constants)",
    10: "Phonon postprocessing",
    11: "Copy CONTCAR to raman dir",
    12: "Navigate to Raman dir",
    13: "Generate Raman displacements and organize",
    14: "Run resonant Raman calculations (VASP)",
    15: "Kopia post-processing",
    16: "Generate RAMFILE for each desired energy",
    17: "Copy static Band/Irreps files to Raman dir",
    18: "Process Raman results",
    20: "All energies processed",
    "final": "Pipeline complete",
}

# Accumulated step history (preserved across write_status calls)
# Imported by automation_raman_analysis.py for resume logic; mutations
# through either module's name affect the same underlying dict.
STEP_HISTORY = {}

# Total number of steps for display
TOTAL_STEPS = 20


def write_status(step, status, message="", *,
                 status_file, material_label, material_name, base_project_dir):
    """
    Write a verbose plain-text workflow status file.

    The pipeline-specific parameters (status_file, material_label,
    material_name, base_project_dir) are keyword-only so that callers
    must pass them explicitly — typically via a thin wrapper in the
    main module that captures the pipeline's global variables.

    Args:
        step:           Step number (int or "final")
        status:         "running", "completed", or "failed"
        message:        Optional descriptive message about what happened
        status_file:    Path to the status file to write
        material_label: Short label for the material
        material_name:  Full material name
        base_project_dir: Base project directory path
    """
    now_ts = time.time()
    now_str = fmt_time(now_ts)
    step_desc = STEP_DESCRIPTIONS.get(step, f"Step {step}")

    # Track start time if this is the first call for the step,
    # but also keep the original start if it was already set as "running"
    if step not in STEP_HISTORY:
        STEP_HISTORY[step] = {"start_ts": now_ts}
    elif status == "completed" and STEP_HISTORY[step].get("status") == "running":
        # Transition from running → completed: keep original start time
        pass

    STEP_HISTORY[step]["end_ts"] = now_ts
    STEP_HISTORY[step]["status"] = status
    if message:
        STEP_HISTORY[step]["message"] = message

    # Determine overall pipeline status
    overall_status = "RUNNING"
    any_failed = any(
        h.get("status") == "failed"
        for h in STEP_HISTORY.values()
    )
    if status == "failed" or any_failed:
        overall_status = "FAILED"
    elif status == "completed" and step == "final":
        overall_status = "COMPLETED"

    # Get pipeline start time from the first tracked step
    pipeline_start = STEP_HISTORY.get(3, {}).get("start_ts", now_ts)

    # ── Build the status file content ──────────────────────────────────────
    lines = []
    sep = "=" * 80

    lines.append(sep)
    lines.append("  RAMAN WORKFLOW STATUS")
    lines.append(sep)
    lines.append("")
    lines.append(f"  Material:         {material_label}  ({material_name})")
    lines.append(f"  Project Dir:      {base_project_dir}")
    lines.append(f"  Started:          {fmt_time(pipeline_start)}")
    lines.append(f"  Last Updated:     {now_str}")
    lines.append(f"  Overall Status:   {overall_status}")
    running_step = None
    for k, h in STEP_HISTORY.items():
        if h.get("status") == "running" and k != "final":
            running_step = k
    if running_step is not None:
        r_desc = STEP_DESCRIPTIONS.get(running_step, f"Step {running_step}")
        lines.append(f"  Current Step:     {running_step}  —  {r_desc}")
    lines.append("")

    # ═══ STEP HISTORY ══════════════════════════════════════════════════════
    lines.append(sep)
    lines.append("  STEP HISTORY")
    lines.append(sep)
    lines.append("")

    completed_keys = sorted(
        [k for k in STEP_HISTORY if isinstance(k, int)],
        key=lambda x: (isinstance(x, int), x)
    )
    # Sort by start time for chronological order
    completed_keys.sort(key=lambda k: STEP_HISTORY[k].get("start_ts", 0))

    for s in completed_keys:
        h = STEP_HISTORY[s]
        desc = STEP_DESCRIPTIONS.get(s, f"Step {s}")
        sts = h.get("status", "UNKNOWN").upper()
        start_ts = h.get("start_ts")
        end_ts = h.get("end_ts")
        msg = h.get("message", "")

        # Format the status tag with padding
        status_tag = f"[{sts:>9}]"

        lines.append(f"  STEP {s:<3}  {status_tag}  {desc}")
        if start_ts:
            lines.append(f"           Started:     {fmt_time(start_ts)}")
        if end_ts and sts in ("COMPLETED", "FAILED"):
            duration_str = calc_duration(start_ts, end_ts) if start_ts else ""
            lines.append(f"           Ended:       {fmt_time(end_ts)}  ({duration_str})")
        if msg:
            # Word-wrap message at 70 chars
            while len(msg) > 70:
                lines.append(f"           Note:       {msg[:70]}")
                msg = msg[70:]
            lines.append(f"           Note:       {msg}")
        lines.append("")

    # ═══ REMAINING STEPS ══════════════════════════════════════════════════
    lines.append(sep)
    lines.append("  REMAINING STEPS")
    lines.append(sep)
    lines.append("")

    all_step_keys = sorted(
        [k for k in STEP_DESCRIPTIONS if isinstance(k, int)]
    )
    done_or_running = set()
    for k, h in STEP_HISTORY.items():
        if isinstance(k, int) and h.get("status") in ("completed", "running", "failed"):
            done_or_running.add(k)

    remaining = [s for s in all_step_keys if s not in done_or_running]
    if remaining:
        for s in remaining:
            lines.append(f"  Step {s:<3}  {STEP_DESCRIPTIONS[s]}")
    else:
        lines.append("  (none — all steps have been attempted)")

    # ═══ ERROR SECTION ════════════════════════════════════════════════════
    if status == "failed" or any_failed:
        lines.append("")
        lines.append(sep)
        lines.append("  ERROR")
        lines.append(sep)
        lines.append(f"  {message}")

    lines.append("")
    lines.append(sep)

    # ── Write the status file ──────────────────────────────────────────────
    try:
        with open(status_file, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[status] Warning: Could not write status file: {e}")


def make_write_status(status_file, material_label, material_name, base_project_dir):
    """
    Create a ``write_status`` callable pre-bound to pipeline-specific values.

    The returned function has the same signature as the pipeline wrapper
    that used to live in ``automation_raman_analysis.py`` — callers just
    pass ``(step, status, message)`` without repeating the four config
    arguments on every invocation.

    Usage in the pipeline script::

        write_status = make_write_status(
            STATUS_FILE, MATERIAL_LABEL, MATERIAL_NAME, BASE_PROJECT_DIR,
        )
        write_status(3, "running", "Initial VASP relaxation")
    """
    def _inner(step, status, message=""):
        write_status(
            step, status, message,
            status_file=status_file,
            material_label=material_label,
            material_name=material_name,
            base_project_dir=base_project_dir,
        )
    return _inner


# ── NBANDS auto-scaling ───────────────────────────────────────────────────────
# Hardcoded NBANDS=64 in input/INCAR fails for large supercells (e.g., 5x5x1
# has NELECT=200, requiring at least 100 bands). This function dynamically
# calculates NBANDS from the primitive cell, POTCAR ZVAL, and phonopy.dim.
def calculate_nbands(poscar_path, potcar_path, phonopy_dim, buffer_factor=1.3):
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
        with open(poscar_path) as f:
            lines = f.readlines()
    except (IOError, OSError) as e:
        print(f"  [nbands] WARNING: Cannot read POSCAR '{poscar_path}': {e}")
        return None

    # Find atom-counts line: first line after the 3 lattice vectors (lines 2-4,
    # 0-indexed) that contains only digits and whitespace.
    atom_counts_line = None
    for i in range(5, len(lines)):
        stripped = lines[i].strip()
        if stripped and all(c.isdigit() or c.isspace() for c in stripped):
            atom_counts_line = stripped
            break

    if atom_counts_line is None:
        print(f"  [nbands] WARNING: Could not find atom-counts line in POSCAR: {poscar_path}")
        return None

    atom_counts = [int(x) for x in atom_counts_line.split()]
    total_atoms_primitive = sum(atom_counts)

    # ── Parse POTCAR for ZVAL per species ─────────────────────────────────
    # POTCAR contains lines like:
    #   POMASS =   10.811; ZVAL   =    3.000    mass and valenz
    zvals = []
    try:
        with open(potcar_path) as f:
            for line in f:
                if 'ZVAL' in line:
                    match = re.search(r'ZVAL\s*=\s*([\d.]+)', line)
                    if match:
                        zvals.append(float(match.group(1)))
    except (IOError, OSError) as e:
        print(f"  [nbands] WARNING: Cannot read POTCAR '{potcar_path}': {e}")
        return None

    if len(zvals) != len(atom_counts):
        print(f"  [nbands] WARNING: Got {len(zvals)} ZVAL values but {len(atom_counts)} "
              f"species — cannot calculate NBANDS.")
        return None

    # ── Calculate NELECT in primitive cell ─────────────────────────────────
    nelect_primitive = sum(c * z for c, z in zip(atom_counts, zvals))

    # ── Parse phonopy.dim ──────────────────────────────────────────────────
    dim_parts = phonopy_dim.split()
    if len(dim_parts) < 3:
        print(f"  [nbands] WARNING: phonopy dim '{phonopy_dim}' has < 3 components")
        return None
    try:
        dim_mult = [int(x) for x in dim_parts[:3]]
    except ValueError:
        print(f"  [nbands] WARNING: Could not parse phonopy dim components: '{phonopy_dim}'")
        return None
    supercell_factor = dim_mult[0] * dim_mult[1] * dim_mult[2]

    # ── Calculate NELECT in supercell ──────────────────────────────────────
    nelect_supercell = nelect_primitive * supercell_factor

    # Each band holds 2 electrons (non-spin-polarized).  Add buffer for
    # empty bands (improves convergence and avoids "highest band occupied"
    # warnings).
    min_nbands = int(nelect_supercell / 2)
    nbands = int(min_nbands * buffer_factor)
    if nbands <= min_nbands:
        nbands = min_nbands + 1

    # Round up to nearest 16 (convenient divisor for parallelization)
    nbands = ((nbands + 15) // 16) * 16

    print(f"  [nbands] Primitive cell: {total_atoms_primitive} atoms, "
          f"{nelect_primitive:.0f} electrons")
    print(f"  [nbands] Phonopy dim: {phonopy_dim}  →  supercell factor = {supercell_factor}")
    print(f"  [nbands] Supercell: ~{total_atoms_primitive * supercell_factor} atoms, "
          f"{nelect_supercell:.0f} electrons → NBANDS = {nbands} "
          f"(min required: {min_nbands}, buffer: {buffer_factor})")

    return nbands
