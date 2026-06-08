"""
Utility functions for the Raman automation pipeline.

This module provides self-contained helper functions extracted from
automation_raman_analysis.py for better organization. Functions that
are tightly coupled to the pipeline's global state remain in the main script.
"""

import os
import re
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
import yaml

try:
    import py4vasp as _py4vasp
    from py4vasp.exception import FileAccessError as _Py4vaspFileAccessError
    _PY4VASP = True
except ImportError:
    _PY4VASP = False


def _h5_path(dirpath):
    """Return path to vaspout.h5 in *dirpath*, or None if absent."""
    p = os.path.join(dirpath, "vaspout.h5")
    return p if os.path.exists(p) else None


class Tee:
    """Duplicate all writes to the real stdout, a status file, and optionally a
    full-output log file."""
    def __init__(self, log_path, out_path=None):
        self.log = open(log_path, "a")
        self.out = open(out_path, "a") if out_path else None
        self.stdout = sys.stdout

    def write(self, data):
        self.stdout.write(data)
        self.log.write(data)
        self.log.flush()
        if self.out:
            self.out.write(data)
            self.out.flush()

    def flush(self):
        self.stdout.flush()
        self.log.flush()
        if self.out:
            self.out.flush()

    def close(self):
        self.log.close()
        if self.out:
            self.out.close()


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


def check_no_selective_dynamics(filepath, context=""):
    """Guard: raise a RuntimeError if *filepath* contains VASP Selective Dynamics.

    Selective Dynamics (``T T F``) is only valid for the initial unit-cell
    relaxation (Step 3).  If it propagates into phonopy displacement or Raman
    POSCAR files, force constants or Raman tensors will be silently wrong.
    """
    if not os.path.exists(filepath):
        return  # let the caller decide what to do about missing files
    with open(filepath) as f:
        for i, line in enumerate(f, 1):
            if "selective" in line.lower():
                raise RuntimeError(
                    f"Selective Dynamics detected in {filepath} (line {i})"
                    + (f" — {context}" if context else "")
                )


def is_vasprun_valid(filepath):
    """Check vasprun.xml is non-trivial and has ``</modeling>`` closing tag.

    Kept as a fallback for directories that do not have vaspout.h5.
    Prefer :func:`is_calculation_complete` for new code.
    """
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


def is_calculation_complete(dirpath):
    """Return True if a VASP run in *dirpath* completed at least one ionic step.

    Checks ``vaspout.h5`` via py4vasp-core when available; falls back to the
    ``</modeling>`` tag check on ``vasprun.xml`` for directories without HDF5
    output.
    """
    if _PY4VASP and _h5_path(dirpath):
        try:
            calc = _py4vasp.Calculation.from_path(dirpath)
            return calc.run_info.read()["num_ionic_steps"] > 0
        except _Py4vaspFileAccessError:
            pass
        except Exception:
            pass
    return is_vasprun_valid(os.path.join(dirpath, "vasprun.xml"))


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
    Parse a unified workflow log (box-drawn table format) to determine which
    step to resume from.

    Populates *step_history* with entries for completed/failed steps so that
    write_status() can display an accurate history on restart.

    The parser searches for the **last** occurrence of the status table in the
    file (the file is append-only, so the last block is the most current),
    then extracts each row's step number and status.

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

        # Find the LAST status table in the file (append-only format).
        # The table starts with ┌─── and ends with └─── (box-drawing chars).
        # We split by ┌─ to find all tables, take the last one.
        table_starts = [i for i, c in enumerate(content) if c == '\u250c']
        if not table_starts:
            # No table found — treat as fresh start
            print(f"[resume] No status table found in {status_file}. Starting from step 3.")
            return 3

        last_table_start = table_starts[-1]
        table_end = content.find('\u2514', last_table_start)
        if table_end == -1:
            table_end = len(content)

        table_section = content[last_table_start:table_end]

        # Parse rows: │ 14 │ ▶ │ ACTIVE │ ...
        # Pattern: │ <step_num> │ <icon> │ <status> │ ...
        completed_steps = set()
        running_step = None
        failed_step = None

        for line in table_section.split('\n'):
            line = line.strip()
            if not line.startswith('\u2502'):
                continue
            parts = [p.strip() for p in line.split('\u2502')]
            # parts[0] is empty (before first │), parts[1] = step, parts[2] = icon,
            # parts[3] = status text, parts[4] = description
            if len(parts) < 4:
                continue
            try:
                step_num = int(parts[1])
            except (ValueError, IndexError):
                continue

            status_text = parts[3].strip().upper() if len(parts) > 3 else ""

            if status_text == "DONE":
                completed_steps.add(step_num)
                step_history[step_num] = {
                    "status": "completed",
                    "start_ts": 0,
                    "end_ts": 0,
                    "message": "Resumed \u2014 completed in previous run",
                }
            elif status_text == "ACTIVE":
                running_step = step_num
            elif status_text == "FAIL":
                failed_step = step_num

        # Priority: running (crashed) > failed > first non-completed
        if running_step is not None:
            step_history[running_step] = {
                "status": "running",
                "start_ts": 0,
                "end_ts": 0,
                "message": "Interrupted \u2014 was RUNNING",
            }
            print(f"[resume] Step {running_step} was ACTIVE (likely crashed). "
                  f"Retrying from step {running_step}.")
            return running_step

        if failed_step is not None:
            print(f"[resume] Step {failed_step} was FAILED. Retrying from step {failed_step}.")
            return failed_step

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


def write_kpoints(path, comment, mesh, shift):
    """Write a Gamma-centred KPOINTS file."""
    with open(path, "w") as f:
        f.write(f"{comment}\n")
        f.write("0\n")
        f.write("Gamma\n")
        f.write(f"{mesh}\n")
        f.write(f"{shift}\n")


def write_incar(path, config, stage):
    """Assemble and write an INCAR file from YAML config.

    Combines :func:`build_incar_content` + file write.
    """
    content = build_incar_content(config, stage)
    with open(path, "w") as f:
        f.write(content)


def count_ionic_steps(dirpath):
    """Return the number of completed ionic steps.

    Reads ``run_info.num_ionic_steps`` from ``vaspout.h5`` via py4vasp-core when
    available; falls back to counting ``Iteration N(M)`` lines in OUTCAR.
    """
    if _PY4VASP and _h5_path(dirpath):
        try:
            calc = _py4vasp.Calculation.from_path(dirpath)
            return int(calc.run_info.read()["num_ionic_steps"])
        except Exception:
            pass
    # Fallback: OUTCAR regex
    outcar = os.path.join(dirpath, "OUTCAR")
    if not os.path.exists(outcar):
        return 0
    with open(outcar) as f:
        return sum(1 for line in f if re.match(r"\s+Iteration\s+\d+\(\s*\d+\)", line))


def generate_kopia_script(raman_dir, ra_dirs):
    """Write and execute the kopia script that copies vasprun.xml files to AXML/.

    ``genRAram610_dynamic`` expects ``B1a.xml``, not ``ra_pos_B1a.xml``, so the
    ``ra_pos_`` prefix is stripped when naming the destination files.
    """
    kopia_path = os.path.join(raman_dir, "kopia")
    with open(kopia_path, "w") as kf:
        kf.write("#!/bin/bash\n")
        kf.write("# Dynamically generated by automation_raman_analysis.py\n")
        kf.write("mkdir -p AXML\n")
        for d in ra_dirs:
            dirname = os.path.basename(d)
            xml_name = dirname[len("ra_pos_"):] if dirname.startswith("ra_pos_") else dirname
            kf.write(f'cp "{dirname}/vasprun.xml" "AXML/{xml_name}.xml"\n')
    run_command(f"chmod +x kopia && ./kopia", cwd=raman_dir)


def inject_ramfile_energies(template_path, dst_path, energies):
    """Inject custom laser energies into ``ramfile_dynamic.sh`` and write to *dst_path*.

    Raises :exc:`RuntimeError` if the template lacks the expected
    ``desired_energies=(...)`` line.
    """
    with open(template_path) as f:
        template = f.read()
    energies_str = " ".join(f'"{e}"' for e in energies)
    match = re.search(r'^desired_energies=\([^)]*\)', template, re.MULTILINE)
    if not match:
        raise RuntimeError(
            "ramfile_dynamic.sh does not contain the expected "
            "'desired_energies=(...)' line — cannot inject custom energies. "
            "Ensure the template has a line like: desired_energies=(\"1.96\" \"2.33\")"
        )
    content = template.replace(match.group(0), f"desired_energies=({energies_str})")
    with open(dst_path, "w") as f:
        f.write(content)
    os.chmod(dst_path, 0o755)
    print(f"  [setup] Generated {os.path.basename(dst_path)} with energies: {energies_str}")


def make_pipeline_excepthook(status_file):
    """Return a sys.excepthook that appends a formatted traceback to *status_file* on crash."""
    def hook(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(tb_text, file=sys.stderr)
        try:
            with open(status_file, "a") as f:
                f.write("\n" + "\u2501" * 78 + "\n")
                f.write("  \u2717 UNHANDLED EXCEPTION \u2014 Full Traceback\n")
                f.write("\u2501" * 78 + "\n")
                f.write(tb_text)
                f.write("\u2501" * 78 + "\n")
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

    If the config contains a ``raw`` key under ``vasp_srun`` or
    ``vasp_srun_cpu``, it is returned verbatim — this allows arbitrary
    srun configurations (e.g. multi-node, ``--gpus-per-task``) without
    Python changes.

    Without ``raw``, the string is assembled from individual keys
    (``gpus``, ``ntasks``, ``cpus_per_task``, ``constraint``) with
    Python-level defaults if those keys are absent from the config.
    """
    key = "vasp_srun_cpu" if cpu_flag else "vasp_srun"
    cfg = config.get(key, {}) if isinstance(config, dict) else {}

    # ── raw override: return verbatim, bypass structured construction ──────
    if "raw" in cfg:
        return cfg["raw"]

    if cpu_flag:
        ntasks = cfg.get("ntasks", 32)
        cpus_per_task = cfg.get("cpus_per_task", 4)
        return (f"--cpu_bind=cores --ntasks {ntasks} "
                f"--cpus-per-task {cpus_per_task}")
    else:
        gpus = cfg.get("gpus", 4)
        ntasks = cfg.get("ntasks", 4)
        cpus_per_task = cfg.get("cpus_per_task", 32)
        constraint = cfg.get("constraint", "gpu")
        return (f"--cpu_bind=cores --gpus {gpus} "
                f"--ntasks {ntasks} --cpus-per-task {cpus_per_task} "
                f"-C {constraint}")


def split_srun_args(srun_args, num_dirs):
    """Split an srun arg string into *num_dirs* proportional copies.

    Parses ``--gpus N`` and ``--ntasks M`` from *srun_args*, divides by
    *num_dirs* (floor), and returns a list of *num_dirs* srun arg strings
    each with proportional GPU/task counts.  Non-numeric flags are preserved
    verbatim.

    Returns an empty list if *num_dirs* < 1 or the split is not possible.
    """
    if num_dirs < 1:
        return []

    gpus_match = re.search(r'--gpus\s+(\d+)', srun_args)
    ntasks_match = re.search(r'--ntasks\s+(\d+)', srun_args)
    if not gpus_match or not ntasks_match:
        return []

    total_gpus = int(gpus_match.group(1))
    total_ntasks = int(ntasks_match.group(1))
    gpus_per = total_gpus // num_dirs
    ntasks_per = total_ntasks // num_dirs

    if gpus_per < 1 or ntasks_per < 1:
        return []

    result = []
    for _ in range(num_dirs):
        args = re.sub(r'--gpus\s+\d+', f'--gpus {gpus_per}', srun_args)
        args = re.sub(r'--ntasks\s+\d+', f'--ntasks {ntasks_per}', args)
        result.append(args)

    idle_gpus = total_gpus - (gpus_per * num_dirs)
    if idle_gpus:
        print(f"  [hf_parallel] {idle_gpus} GPU(s) idle "
              f"({total_gpus} not divisible by {num_dirs})")

    return result


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
    return len(displacement_dirs)


def update_chgcar_symlinks(hffiles_dir):
    """Create ``../groundstate/CHGCAR`` symlinks in each ``hf_POSCAR-*/``.

    runHF does not create CHGCAR symlinks.  This function mirrors
    :func:`update_wavecar_symlinks` so that displacement VASP runs
    can read the precomputed charge density from the supercell
    groundstate (generated in Step 4, stored in ``hf/groundstate/``).

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
    4: "Supercell generation + ionic relaxation",
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


def print_step_header(step_num, description=""):
    """Print a visually distinct step-start banner to the log.

    Example::

        ╔══════════════════════════════════════════════════════════════╗
        ║  STEP 3 — Initial VASP relaxation                          ║
        ╚══════════════════════════════════════════════════════════════╝
    """
    desc = description or STEP_DESCRIPTIONS.get(step_num, f"Step {step_num}")
    text = f"  STEP {step_num} — {desc}"
    # Pad to 66 chars (fits inside ║ ║ border)
    text = text[:66].ljust(66)
    bar = "═" * 66
    print(f"\n╔{bar}╗")
    print(f"║{text}║")
    print(f"╚{bar}╝\n")


def print_step_result(step_num, ok=True, duration_s=0, message=""):
    """Print a step-completion or step-failure message to the log.

    Example::

         ✓ STEP 3 COMPLETE — Initial VASP relaxation (2m 15s)
         ✗ STEP 9 FAILED   — VASP force constants
    """
    desc = STEP_DESCRIPTIONS.get(step_num, f"Step {step_num}")
    icon = "✓" if ok else "✗"
    status_word = "COMPLETE" if ok else "FAILED"
    dur_str = ""
    if ok and duration_s > 0:
        dur_str = f" ({calc_duration(0, duration_s)})"
    elif duration_s > 0:
        dur_str = f" [{calc_duration(0, duration_s)}]"
    msg_suffix = f" — {message}" if message else ""
    print(f"\n  {icon} STEP {step_num} {status_word} — {desc}{dur_str}{msg_suffix}\n")


def write_status(step, status, message="", *,
                 status_file, material_label, material_name, base_project_dir):
    """
    Write a combined status-overview + chronological log entry to *status_file*.

    The format uses box-drawing characters and section headers:

        ━━━━ RAMAN WORKFLOW ━━━━ ... ━━━━
          Status table (box-drawn, one row per step)
        ━━━━ STEP LOG ━━━━
          [timestamp] log entries...

    The file is **append-only** — write_status() appends a new formatted
    status block on every call.  Because ``sys.stdout`` is typically
    redirected to the same file via ``Tee``, the chronological log entries
    from ``print()`` appear between status blocks automatically.

    Pipeline-specific parameters (status_file, material_label,
    material_name, base_project_dir) are keyword-only — typically provided
    via ``make_write_status()``.

    Args:
        step:           Step number (int or ``"final"``).
        status:         ``"running"``, ``"completed"``, or ``"failed"``.
        message:        Optional descriptive message.
        status_file:    Path to the unified workflow log / status file.
        material_label: Short label for the material.
        material_name:  Full material name.
        base_project_dir: Base project directory path.
    """
    now_ts = time.time()
    now_str = fmt_time(now_ts)
    step_desc = STEP_DESCRIPTIONS.get(step, f"Step {step}")

    # Track start time for the step
    if step not in STEP_HISTORY:
        STEP_HISTORY[step] = {"start_ts": now_ts}
    elif status == "completed" and STEP_HISTORY[step].get("status") == "running":
        pass  # keep original start time

    STEP_HISTORY[step]["end_ts"] = now_ts
    STEP_HISTORY[step]["status"] = status
    if message:
        STEP_HISTORY[step]["message"] = message

    # Overall pipeline status
    any_failed = any(
        h.get("status") == "failed"
        for h in STEP_HISTORY.values()
    )
    if status == "failed" or any_failed:
        overall_status = "FAILED"
    elif status == "completed" and step == "final":
        overall_status = "COMPLETED"
    else:
        overall_status = "RUNNING"

    pipeline_start = STEP_HISTORY.get(3, {}).get("start_ts", now_ts)

    # ── Helper: duration string ────────────────────────────────────────────
    def _dur(s, e):
        return calc_duration(s, e) if s and e else ""

    # ── Helper: status icon ────────────────────────────────────────────────
    def _icon(sts):
        return {"completed": "\u2713", "running": "\u25B6",
                "failed": "\u2717"}.get(sts, "\u2014")

    # Determine running / failed step info
    running_step = None
    for k, h in STEP_HISTORY.items():
        if h.get("status") == "running" and k != "final":
            running_step = k

    # ═══════════════════════════════════════════════════════════════════════
    #  Build the status block
    # ═══════════════════════════════════════════════════════════════════════
    lines = []

    # ── Header ─────────────────────────────────────────────────────────────
    lines.append("")
    lines.append("\u2501" * 78)
    header = (
        f"  RAMAN WORKFLOW  \u2502  {material_name}  "
        f"\u2502  {now_str}"
    )
    lines.append(header)
    lines.append("\u2501" * 78)
    lines.append("")

    # ── Summary line ───────────────────────────────────────────────────────
    elapsed = calc_duration(pipeline_start, now_ts) if pipeline_start else ""
    summary_parts = [f"Status   {overall_status}"]
    if running_step is not None:
        r_desc = STEP_DESCRIPTIONS.get(running_step, f"Step {running_step}")
        summary_parts.append(f"\u2014 Step {running_step} ({r_desc})")
    if overall_status == "FAILED" and message:
        summary_parts.append(f"\u2014 {message}")
    lines.append(f"  {'  '.join(summary_parts)}")
    lines.append(f"  Started  {fmt_time(pipeline_start)}")
    lines.append(f"  Elapsed  {elapsed}")
    lines.append("")

    # ── Step status table ──────────────────────────────────────────────────
    # Collect all step keys (3-20 + "final") in display order
    table_keys = sorted(
        k for k in STEP_DESCRIPTIONS if isinstance(k, int)
    )
    # Build rows
    rows = []
    for s in table_keys:
        h = STEP_HISTORY.get(s, {})
        sts = h.get("status", "")
        desc = STEP_DESCRIPTIONS[s]
        icon = _icon(sts)
        dur = _dur(h.get("start_ts"), h.get("end_ts"))
        # Truncate description to fit table
        desc_display = desc[:40]
        rows.append((s, icon, sts.upper() if sts else "\u2014", desc_display, dur))

    col_widths = [4, 3, 8, 42, 8]  # step, icon, status, desc, duration
    sep_line = "\u2500" * (sum(col_widths) + len(col_widths) + 1)

    def _fmt_row(cols):
        parts = []
        for i, (c, w) in enumerate(zip(cols, col_widths)):
            if i == 0:
                parts.append(f"{c:>{w}}")
            elif i == 1:
                parts.append(f" {c} ")
            elif i == 2:
                parts.append(f"{c:<{w}}")
            elif i == 3:
                parts.append(f"{c:<{w}}")
            else:
                parts.append(f"{c:>{w}}")
        return "\u2502 " + " \u2502 ".join(parts) + " \u2502"

    # Table top
    lines.append("  \u250c" + sep_line + "\u2510")
    lines.append("  " + _fmt_row(["#", "", "Status", "Description", "Duration"]))
    lines.append("  \u2502" + sep_line + "\u2502")

    for s, icon, sts_text, desc, dur in rows:
        lines.append("  " + _fmt_row([s, icon, sts_text, desc, dur]))

    lines.append("  \u2514" + sep_line + "\u2518")
    lines.append("")

    # ── Step log section marker ────────────────────────────────────────────
    # (Chronological entries from Tee+print() appear below this marker.)
    lines.append("\u2501" * 78)
    lines.append(f"  STEP LOG")
    lines.append("\u2501" * 78)
    lines.append("")

    # ── Append the status block to the file ────────────────────────────────
    try:
        with open(status_file, "a") as f:
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


def build_incar_content(config, stage):
    """Assemble an INCAR file content string from YAML config sources.

    Assembly order (VASP "last value wins"):

      1. Base template (``incar_templates.{stage}`` from *config*).
      2. Per-material overrides (``incar_settings.{stage}`` from *config*),
         if present — these override base-template tags.

    Parameters
    ----------
    config : dict
        The merged pipeline configuration.
        Must contain ``incar_templates`` with a *stage* key.
    stage : str
        One of ``"relax"``, ``"dielec"``, ``"hf"``, or ``"supercell_relax"``.

    Returns
    -------
    str
        Complete INCAR file content ready to write to disk.
    """
    templates = config.get("incar_templates", {})
    base = templates.get(stage, "")
    if not base:
        raise KeyError(
            f"Missing incar_templates.{stage} in pipeline config. "
            f"Available stages: {list(templates.keys())}"
        )

    per_material = config.get("incar_settings", {}).get(stage, "")

    # per_material goes FIRST so VASP uses its tags (VASP takes the first
    # occurrence of a duplicate tag, not the last).
    parts = []
    if per_material:
        parts.append(per_material)
    parts.append(base)

    return "\n".join(parts) + "\n"


def _build_atom_labels(hf, n_atoms):
    """Build human-readable atom labels from vaspout.h5 POSCAR data.

    Returns a list like ``['B_1', 'N_1']`` for a 2-atom unit cell or
    ``['B_1', 'B_2', ..., 'N_1', 'N_2', ...]`` for a supercell.
    Falls back to numbered indices if POSCAR data is absent.
    """
    try:
        ion_types_raw = hf["input/poscar/ion_types"][()]
        num_per_type  = hf["input/poscar/number_ion_types"][()]
        # Decode bytes → str; VASP stores as fixed-length byte strings
        symbols = []
        for raw in ion_types_raw:
            s = raw.tobytes().decode("utf-8").strip() if isinstance(raw, np.bytes_) else str(raw).strip()
            symbols.append(s)
        labels = []
        for sym, count in zip(symbols, num_per_type):
            for j in range(int(count)):
                labels.append(f"{sym}_{j + 1}")
        if len(labels) >= n_atoms:
            return labels[:n_atoms]
    except Exception:
        pass
    # Fallback: numbered atoms
    return [f"atom_{i + 1}" for i in range(n_atoms)]


def _print_force_table(prefix, forces, mags, labels, max_idx, max_f):
    """Print a compact per-atom force table to the log.

    For ≤12 atoms prints all rows; for larger systems prints the 5 atoms
    with highest forces plus summary statistics.
    """
    n = len(mags)
    # Sort by force magnitude descending
    order = np.argsort(mags)[::-1]

    if n <= 12:
        # Full table
        lines = [f"{prefix} Per-atom residual forces (eV/Å):",
                 f"{prefix}   {'Atom':>8s}  {'Fx':>10s}  {'Fy':>10s}  {'Fz':>10s}  {'|F|':>10s}",
                 f"{prefix}   " + "─" * 55]
        for i in range(n):
            fx, fy, fz = forces[i]
            flag = " ← MAX" if i == max_idx else ""
            lines.append(
                f"{prefix}   {labels[i]:>8s}  {fx:10.6f}  {fy:10.6f}  "
                f"{fz:10.6f}  {mags[i]:10.6f}{flag}"
            )
        print("\n".join(lines))
    else:
        # Large system: top-5 only
        lines = [f"{prefix} Residual forces — top 5 of {n} atoms (eV/Å):",
                 f"{prefix}   {'Atom':>8s}  {'Fx':>10s}  {'Fy':>10s}  {'Fz':>10s}  {'|F|':>10s}",
                 f"{prefix}   " + "─" * 55]
        for k in range(min(5, n)):
            i = order[k]
            fx, fy, fz = forces[i]
            flag = " ← MAX" if i == max_idx else ""
            lines.append(
                f"{prefix}   {labels[i]:>8s}  {fx:10.6f}  {fy:10.6f}  "
                f"{fz:10.6f}  {mags[i]:10.6f}{flag}"
            )
        # Summary stats
        mean_f = float(np.mean(mags))
        median_f = float(np.median(mags))
        lines.append(f"{prefix}   " + "─" * 55)
        lines.append(f"{prefix}   max={max_f:.6f}  mean={mean_f:.6f}  median={median_f:.6f}  "
                     f"({n} atoms total)")
        print("\n".join(lines))


def check_vasp_convergence(outcar_dir, stage_label=""):
    """Check that VASP completed and converged in *outcar_dir*.

    Primary path: reads ``vaspout.h5`` via py4vasp-core — checks
    ``run_info.num_ionic_steps`` for completion and compares the final
    ``force[-1]`` magnitude against ``EDIFFG`` from the INCAR block.
    This also handles the ZBRENT-crash case naturally: if VASP died on the
    conjugate-gradient line search but the last ionic step already satisfied
    EDIFFG, the forces say "converged" regardless of the exit code.

    Fallback: greps OUTCAR for the ``"General timing and accounting"`` footer
    and the ionic/electronic convergence strings (legacy behaviour, used when
    ``vaspout.h5`` is absent).

    Parameters
    ----------
    outcar_dir : str
        Directory containing the VASP output files.
    stage_label : str, optional
        Label for log messages (e.g. ``"step-3"``).

    Raises
    ------
    RuntimeError
        If VASP produced no ionic steps or did not complete normally.
    """
    prefix = f"  [vasp:{stage_label}]" if stage_label else "  [vasp]"

    # ── Primary: py4vasp-core + HDF5 ─────────────────────────────────────────
    if _PY4VASP and _h5_path(outcar_dir):
        try:
            import numpy as np
            import h5py

            calc = _py4vasp.Calculation.from_path(outcar_dir)
            n_steps = int(calc.run_info.read()["num_ionic_steps"])

            if n_steps == 0:
                raise RuntimeError(
                    f"{prefix} vaspout.h5 contains no ionic steps — "
                    f"VASP crashed before writing any output."
                )

            # Read NSW and EDIFFG directly from the HDF5 INCAR block
            with h5py.File(_h5_path(outcar_dir), "r") as hf:
                nsw    = int(hf["input/incar/NSW"][()])
                ediffg = float(hf["input/incar/EDIFFG"][()])

            if nsw == 0:
                # Static / dielectric run — ionic convergence not applicable
                print(f"{prefix} VASP static run complete ({n_steps} step)")
                return

            # Ionic relaxation: compare max force against EDIFFG threshold
            f_last = calc.force[-1].read()["forces"]          # (n_atoms, 3)
            f_mags = np.linalg.norm(f_last, axis=1)
            max_f  = float(np.max(f_mags))
            max_idx = int(np.argmax(f_mags))
            tol    = abs(ediffg)

            # ── Per-atom force table ─────────────────────────────────────
            atom_labels = _build_atom_labels(hf, n_atoms=len(f_mags))

            converged = max_f <= tol
            status = "converged" if converged else "NOT converged"
            print(f"{prefix} VASP {status}: max |F| = {max_f:.6f} eV/Å "
                  f"{'≤' if converged else '>'} EDIFFG = {tol} "
                  f"after {n_steps}/{nsw} step(s) "
                  f"(worst atom: {atom_labels[max_idx]})")

            # Always print a compact per-atom force table for relaxation runs.
            # For small systems (≤12 atoms) print full table; for larger
            # supercells print top-5 worst atoms + summary stats.
            _print_force_table(prefix, f_last, f_mags, atom_labels, max_idx, max_f)
            return

        except RuntimeError:
            raise
        except _Py4vaspFileAccessError:
            pass  # no vaspout.h5 — fall through to OUTCAR
        except Exception as e:
            print(f"{prefix} [py4vasp check failed: {e}] — falling back to OUTCAR")

    # ── Fallback: OUTCAR grep (no vaspout.h5 or py4vasp unavailable) ─────────
    outcar_path = os.path.join(outcar_dir, "OUTCAR")
    if not os.path.exists(outcar_path):
        raise RuntimeError(
            f"{prefix} OUTCAR not found at {outcar_path} — "
            f"VASP likely did not run or crashed immediately."
        )
    with open(outcar_path) as f:
        content = f.read()
    if "General timing and accounting" not in content:
        # ZBRENT non-fatal guard: CG line search crashes when already converged.
        stdout_path = os.path.join(outcar_dir, "relaxation.stdout")
        if os.path.exists(stdout_path):
            with open(stdout_path) as _f:
                if "ZBRENT: fatal error in bracketing" in _f.read():
                    blocks = re.findall(
                        r"TOTAL-FORCE \(eV/Angst\)\n\s*-+\n(.*?)\n\s*-+",
                        content, re.DOTALL
                    )
                    if blocks:
                        lines = [l.split() for l in
                                 blocks[-1].strip().split("\n")
                                 if len(l.split()) >= 6]
                        max_f = max(
                            (float(p[3])**2 + float(p[4])**2 + float(p[5])**2)**0.5
                            for p in lines
                        )
                        if max_f < 0.01:
                            print(f"{prefix} ZBRENT error — forces converged "
                                  f"(max |F| = {max_f:.6f} eV/Å). Continuing.")
                            return
        raise RuntimeError(
            f"{prefix} VASP did not complete normally "
            f"(no 'General timing and accounting' footer in OUTCAR)."
        )
    if "reached required accuracy" in content or "aborting loop because EDIFF is reached" in content:
        print(f"{prefix} VASP converged successfully")
    else:
        print(f"{prefix} WARNING: VASP did NOT reach convergence "
              f"(no convergence signal found).")
        print(f"{prefix} The calculation finished but may not be fully converged.")


def check_dielectric_complete(dirpath, stage_label=""):
    """Verify that a LOPTICS run wrote non-zero dielectric tensor data.

    Reads ``results/linear_response/current_current_dielectric_function`` from
    ``vaspout.h5`` via py4vasp-core.  Raises :exc:`RuntimeError` if the tensor
    is absent or all-zero (which would silently corrupt the ``genRAram610_dynamic``
    output in Step 16).

    Silently skips if py4vasp-core is unavailable or no ``vaspout.h5`` exists.
    """
    if not _PY4VASP or not _h5_path(dirpath):
        return

    prefix = f"  [vasp:{stage_label}]" if stage_label else "  [vasp]"
    try:
        import numpy as np
        import h5py

        # Skip silently if this run did not produce linear-response data.
        # Checking results/linear_response directly is more reliable than
        # reading LOPTICS from the INCAR block: some dirs have LOPTICS=.TRUE.
        # in the INCAR but did not actually compute optical response (e.g.
        # hf_POSCAR-* from older pipeline runs).
        with h5py.File(_h5_path(dirpath), "r") as hf:
            if "results/linear_response" not in hf:
                return

        calc = _py4vasp.Calculation.from_path(dirpath)
        diel = calc.dielectric_function.read()
        eps  = diel["dielectric_function"]   # complex (3, 3, NEDOS)

        if not np.any(np.abs(eps.imag) > 1e-10):
            raise RuntimeError(
                f"{prefix} Dielectric tensor imaginary part is zero in {dirpath}. "
                f"LOPTICS calculation produced no optical response — "
                f"check VASP INCAR (LOPTICS = .TRUE. required) and rerun Step 14."
            )
        print(f"{prefix} Dielectric tensor OK: shape {eps.shape}, "
              f"max |Im(ε)| = {float(np.max(np.abs(eps.imag))):.4f}")
    except RuntimeError:
        raise
    except _Py4vaspFileAccessError:
        pass
    except Exception as e:
        print(f"{prefix} WARNING: could not verify dielectric data ({e})")


def _extract_max_force(outcar_path):
    """Extract the maximum atomic force (eV/Å) from VASP OUTCAR.

    Parses the last ``TOTAL-FORCE (eV/Angst)`` block and returns the
    RMS force magnitude. Returns ``None`` if the block can't be parsed.
    """
    try:
        with open(outcar_path) as f:
            content = f.read()
    except (OSError, IOError):
        return None
    blocks = re.findall(
        r"TOTAL-FORCE \(eV/Angst\)\n\s*-+\n(.*?)\n\s*-+",
        content, re.DOTALL
    )
    if not blocks:
        return None
    lines = [l.split() for l in blocks[-1].strip().split("\n")
             if len(l.split()) >= 6]
    if not lines:
        return None
    return max(
        (float(p[3])**2 + float(p[4])**2 + float(p[5])**2)**0.5
        for p in lines
    )


def _has_zbrent_error(stdout_path):
    """Check if a VASP stdout file contains any ZBRENT error message."""
    try:
        with open(stdout_path) as f:
            content = f.read()
        return (
            "ZBRENT: fatal error in bracketing" in content or
            "ZBRENT: can't locate minimum" in content
        )
    except (OSError, IOError):
        return False


def run_relaxation_with_zbrent_retry(scf_dir, srun_args, vasp_binary,
                                     max_attempts=3, stage_label="step-3"):
    """Run VASP relaxation in *scf_dir* with automatic CONTCAR→POSCAR restart.

    VASP's conjugate-gradient line search (IBRION=2) can emit ZBRENT
    errors when the structure is near a minimum but the bracketing
    algorithm fails.  This function detects that condition and restarts
    from the partially relaxed CONTCAR, leaving the INCAR untouched.

    Parameters
    ----------
    scf_dir : str
        Directory containing the VASP input files (POSCAR, INCAR, POTCAR, KPOINTS).
    srun_args : str
        Slurm ``srun`` arguments (e.g. ``"--cpu_bind=cores --gpus 4 ..."``).
    vasp_binary : str
        Path to the VASP executable.
    max_attempts : int, optional
        Maximum number of VASP runs before giving up (default 3).
    stage_label : str, optional
        Label for log messages (default ``"step-3"``).

    Returns
    -------
    bool
        ``True`` if the relaxation completed successfully (forces converged
        or ZBRENT-resolved). ``False`` if all attempts failed.
    """
    prefix = f"  [vasp:{stage_label}]" if stage_label else "  [vasp]"
    outcar_path = os.path.join(scf_dir, "OUTCAR")
    stdout_path = os.path.join(scf_dir, "relaxation.stdout")
    contcar_path = os.path.join(scf_dir, "CONTCAR")
    poscar_path = os.path.join(scf_dir, "POSCAR")

    for attempt in range(1, max_attempts + 1):
        print(f"\n  [relax] Attempt {attempt}/{max_attempts}...")

        # Remove stale OUTCAR from previous attempt (VASP appends otherwise)
        if os.path.exists(outcar_path):
            os.remove(outcar_path)

        run_command(
            f"srun {srun_args} {vasp_binary} > relaxation.stdout",
            cwd=scf_dir, check_success=False,
        )

        # ── Case 1: ZBRENT error — check BEFORE convergence ─────────────
        # VASP can both converge forces AND hit a ZBRENT error in the same
        # run (forces reach 0, then the next ionic step's line minimisation
        # fails because there is no downhill direction).  Detecting the
        # error first lets us restart from CONTCAR so VASP writes proper
        # WAVECAR / CHGCAR files on the retry.
        if os.path.exists(stdout_path) and _has_zbrent_error(stdout_path):
            max_f = _extract_max_force(outcar_path) if os.path.exists(outcar_path) else None

            if max_f is not None and max_f < 0.01:
                print(f"{prefix} ZBRENT error after convergence "
                      f"(max |F| = {max_f:.6f} eV/Å) — restarting from "
                      f"CONTCAR so VASP writes I/O files cleanly.")
            else:
                print(f"  [relax] ZBRENT detected "
                      f"(max |F| = {max_f:.4f} eV/Å if available) — "
                      f"restarting from CONTCAR...")

            if os.path.exists(contcar_path):
                shutil.copy2(contcar_path, poscar_path)
                if attempt < max_attempts:
                    continue
            else:
                print(f"  [relax] ZBRENT detected but no CONTCAR found.")
            # fall through to failure if out of attempts

        # ── Case 2: VASP completed normally ──────────────────────────────
        try:
            check_vasp_convergence(scf_dir, stage_label)
            print(f"{prefix} Relaxation succeeded on attempt {attempt}.")
            return True
        except RuntimeError:
            pass  # fall through

        # ── Case 3: Other failure (not ZBRENT) ───────────────────────────
        print(f"{prefix} VASP did not complete normally "
              f"(no ZBRENT pattern detected).")

        if attempt < max_attempts:
            print(f"  [relax] Retrying ({attempt + 1}/{max_attempts})...")
        else:
            print(f"  [relax] Max attempts ({max_attempts}) reached.")
            return False

    return False