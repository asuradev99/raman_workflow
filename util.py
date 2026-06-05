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


class Tee:
    """Duplicate all writes to both the real stdout and a log file."""
    def __init__(self, log_path):
        self.log = open(log_path, "a")
        self.stdout = sys.stdout

    def write(self, data):
        self.stdout.write(data)
        self.log.write(data)
        self.log.flush()

    def flush(self):
        self.stdout.flush()
        self.log.flush()

    def close(self):
        self.log.close()


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


def write_incar(path, config, stage, cpu_flag):
    """Assemble, validate, and write an INCAR file in one call.

    Combines :func:`build_incar_content` + :func:`validate_incar_lscalapack` + write.
    """
    content = build_incar_content(config, stage, cpu_flag)
    validate_incar_lscalapack(content, cpu_flag)
    with open(path, "w") as f:
        f.write(content)


def count_ionic_steps(outcar_dir):
    """Return the number of ionic steps from OUTCAR, or 0 if OUTCAR is absent."""
    outcar = os.path.join(outcar_dir, "OUTCAR")
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
    return len(displacement_dirs)


def update_chgcar_symlinks(hffiles_dir):
    """Create ``../groundstate/CHGCAR`` symlinks in each ``hf_POSCAR-*/``.

    runHF does not create CHGCAR symlinks.  This function mirrors
    :func:`update_wavecar_symlinks` so that displacement VASP runs
    can read the precomputed charge density from the supercell static
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


def build_incar_content(config, stage, cpu_flag=False):
    """Assemble an INCAR file content string from YAML config sources.

    The assembly order ensures VASP's "last value wins" semantics:

      1. Base template (``incar_templates.{stage}`` from *config*).
      2. Per-material overrides (``incar_settings.{stage}`` from *config*),
         if present — these override base-template tags for this material.
      3. Arch override — ``incar_gpu_settings`` or ``incar_cpu_settings``
         from *config*, auto-appended based on *cpu_flag*. This sets
         ``LSCALAPACK`` to ``.FALSE.`` (GPU) or ``.TRUE.`` (CPU) and
         always wins, so per-material YAMLs never need to repeat it.

    Parameters
    ----------
    config : dict
        The merged pipeline configuration (fallback + shared + per-material).
        Must contain ``incar_templates`` with a *stage* key.
    stage : str
        One of ``"relax"``, ``"dielec"``, ``"hf"``, or ``"static"``.
    cpu_flag : bool
        ``True`` to use ``incar_cpu_settings`` (LSCALAPACK=.TRUE.),
        ``False`` to use ``incar_gpu_settings`` (LSCALAPACK=.FALSE.).

    Returns
    -------
    str
        Complete INCAR file content ready to write to disk.
    """
    # 1. Base template
    templates = config.get("incar_templates", {})
    base = templates.get(stage, "")
    if not base:
        raise KeyError(
            f"Missing incar_templates.{stage} in pipeline config. "
            f"Available stages: {list(templates.keys())}"
        )

    # 2. Per-material overrides (GGA, NBANDS, etc. — NOT LSCALAPACK)
    per_material = config.get("incar_settings", {}).get(stage, "")

    # 3. Arch override (LSCALAPACK matching target hardware)
    arch_key = "incar_cpu_settings" if cpu_flag else "incar_gpu_settings"
    arch_override = config.get(arch_key, "")
    if not arch_override:
        label = "CPU" if cpu_flag else "GPU"
        raise KeyError(
            f"Missing {arch_key} in config — required for {label} mode. "
            f"Add it to shared_workflow_settings.yaml."
        )

    # Combine with blank-line separators for readability
    parts = [base]
    if per_material:
        parts.append(per_material)
    parts.append(arch_override)

    return "\n".join(parts) + "\n"


def validate_incar_lscalapack(incar_content, cpu_flag):
    """Check that an INCAR content string contains the expected ``LSCALAPACK`` value.

    Parameters
    ----------
    incar_content : str
        The INCAR file content to scan.
    cpu_flag : bool
        ``True`` if running on CPU (expects ``LSCALAPACK = .TRUE.``),
        ``False`` if running on GPU (expects ``LSCALAPACK = .FALSE.``).

    Raises
    ------
    ValueError
        If ``LSCALAPACK`` is missing or set to the wrong value.
    """
    expected = ".TRUE." if cpu_flag else ".FALSE."
    label = "CPU" if cpu_flag else "GPU"

    # Extract the last LSCALAPACK value (VASP "last value wins")
    matches = re.findall(
        r"^\s*LSCALAPACK\s*=\s*\.(TRUE|FALSE)\.\s*$",
        incar_content,
        re.MULTILINE | re.IGNORECASE,
    )
    if not matches:
        raise ValueError(
            f"LSCALAPACK validation FAILED — tag not found in INCAR content "
            f"({label} mode expected LSCALAPACK = {expected}). "
            f"Ensure incar_gpu_settings or incar_cpu_settings in "
            f"shared_workflow_settings.yaml contains LSCALAPACK = {expected}."
        )
    actual_value = matches[-1].upper()
    expected_value = "TRUE" if cpu_flag else "FALSE"
    if actual_value != expected_value:
        raise ValueError(
            f"LSCALAPACK mismatch! Expected {expected} for {label} mode, "
            f"but last occurrence is LSCALAPACK = .{actual_value}.\n"
            f"Fix incar_gpu_settings or incar_cpu_settings in "
            f"shared_workflow_settings.yaml."
        )
    print(f"  [validate] LSCALAPACK = .{actual_value}.  ({label} mode: OK)")


def check_vasp_convergence(outcar_dir, stage_label=""):
    """Check that VASP completed and converged in *outcar_dir*/OUTCAR.

    Parameters
    ----------
    outcar_dir : str
        Directory containing the VASP OUTCAR file.
    stage_label : str, optional
        Label for log messages (e.g. ``"step-3"``).

    Raises
    ------
    RuntimeError
        If OUTCAR is missing or VASP did not complete (no
        ``"General timing and accounting"`` footer).
    """
    prefix = f"  [vasp:{stage_label}]" if stage_label else "  [vasp]"
    outcar_path = os.path.join(outcar_dir, "OUTCAR")
    if not os.path.exists(outcar_path):
        raise RuntimeError(
            f"{prefix} OUTCAR not found at {outcar_path} — "
            f"VASP likely did not run or crashed immediately."
        )
    with open(outcar_path) as f:
        content = f.read()
    if "General timing and accounting" not in content:
        # ── ZBRENT non-fatal guard ──────────────────────────────────────────────
        # VASP's conjugate-gradient line search can crash with ZBRENT error when
        # the structure is already converged.  If forces < 0.01 eV/Å, continue.
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
    if "reached required accuracy" in content:
        print(f"{prefix} VASP converged successfully")
    else:
        print(f"{prefix} WARNING: VASP did NOT reach convergence "
              f"(no 'reached required accuracy').")
        print(f"{prefix} The calculation finished but may not be fully converged.")