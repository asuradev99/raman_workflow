"""
Utility functions for the Raman automation pipeline.

This module provides self-contained helper functions extracted from
automation_raman_analysis.py for better organization. Functions that
are tightly coupled to the pipeline's global state remain in the main script.
"""

import os
import subprocess
import time


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


def _fmt_time(ts):
    """Format a Unix timestamp to a human-readable UTC string."""
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _calc_duration(start_ts, end_ts):
    """Calculate a human-readable duration between two Unix timestamps."""
    secs = end_ts - start_ts
    if secs < 60:
        return f"{secs:.0f}s"
    elif secs < 3600:
        return f"{secs//60:.0f}m {secs%60:.0f}s"
    else:
        return f"{secs//3600:.0f}h {(secs%3600)//60:.0f}m"


def _ensure_dim_in_conf(conf_path, label, dim):
    """Prepend DIM = <dim> to a phonopy .conf file if it lacks DIM.
    Returns True if the file exists (and is now valid), False otherwise.
    """
    if not os.path.exists(conf_path):
        print(f"  [setup] {label} not found at {conf_path}")
        return False
    with open(conf_path) as _f:
        content = _f.read()
    if "DIM" not in content:
        print(f"  [setup] {label} lacks DIM — prepending DIM = {dim}...")
        with open(conf_path, "w") as _f:
            _f.write(f"DIM = {dim}\n")
            _f.write(content)
    return True


def _restore_z_lattice_vector(material_dir):
    """
    Replace the 3rd lattice vector (z-axis) in CONTCAR with the original value
    from input/POSCAR. This prevents vacuum compression in 2D slab calculations
    while preserving in-plane (x,y) relaxation.

    Args:
        material_dir: Path to the material directory (e.g., /path/to/hBN_PBE)
    """
    _poscar_path = os.path.join(material_dir, "input", "POSCAR")
    _contcar_path = os.path.join(material_dir, "CONTCAR")

    if not os.path.exists(_poscar_path):
        print("  [z-fix] input/POSCAR not found — cannot restore z lattice vector. Skipping.")
        return
    if not os.path.exists(_contcar_path):
        print("  [z-fix] CONTCAR not found — nothing to fix. Skipping.")
        return

    # Read original 3rd lattice vector from input/POSCAR (line 4, 0-indexed)
    with open(_poscar_path) as _pf:
        _poscar_lines = _pf.readlines()
    if len(_poscar_lines) < 5:
        print(f"  [z-fix] input/POSCAR has only {len(_poscar_lines)} lines — unexpected format. Skipping.")
        return
    _orig_z_line = _poscar_lines[4].strip()
    _orig_z_parts = _orig_z_line.split()
    if len(_orig_z_parts) < 3:
        print(f"  [z-fix] Could not parse 3rd lattice vector from input/POSCAR line 5: '{_orig_z_line}'. Skipping.")
        return

    # Read relaxed CONTCAR
    with open(_contcar_path) as _cf:
        _contcar_lines = _cf.readlines()
    if len(_contcar_lines) < 5:
        print(f"  [z-fix] CONTCAR has only {len(_contcar_lines)} lines — unexpected format. Skipping.")
        return

    # Log what changed
    _relaxed_z_line = _contcar_lines[4].strip()
    print(f"  [z-fix] Original z lattice vector (input/POSCAR):  {_orig_z_line}")
    print(f"  [z-fix] Relaxed z lattice vector (CONTCAR before): {_relaxed_z_line}")

    # Replace the 3rd lattice vector in CONTCAR
    _contcar_lines[4] = _poscar_lines[4]
    with open(_contcar_path, "w") as _cf:
        _cf.writelines(_contcar_lines)

    print(f"  [z-fix] Restored z lattice vector in CONTCAR to:  {_orig_z_line}")


# ── Workflow status tracking ──────────────────────────────────────────────────
# These constants and the write_status() function track pipeline progress.
# They originally lived in automation_raman_analysis.py but were moved here
# for better organization.

# Human-readable step descriptions
_STEP_DESCRIPTIONS = {
    3: "Initial VASP relaxation",
    4: "Copy files to hf/",
    5: "Phonopy displacement generation",
    6: "Run runHF to organize displacement folders",
    7: "VASP in all hf_POSCAR folders (force constants)",
    8: "Phonon postprocessing",
    9: "Phonopy symmetry analysis",
    10: "Copy CONTCAR to raman dir",
    11: "Navigate to Raman dir",
    12: "Generate Raman displacements and organize",
    13: "Run resonant Raman calculations (VASP)",
    14: "Kopia post-processing",
    15: "Generate RAMFILE for each desired energy",
    16: "RAMFILE confirmation",
    17: "Copy static Band/Irreps files to Raman dir",
    18: "Process Raman results",
    20: "All energies processed",
    "final": "Pipeline complete",
}

# Accumulated step history (preserved across write_status calls)
# Imported by automation_raman_analysis.py for resume logic; mutations
# through either module's name affect the same underlying dict.
_STEP_HISTORY = {}

# Total number of steps for display
_TOTAL_STEPS = 20


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
    now_str = _fmt_time(now_ts)
    step_desc = _STEP_DESCRIPTIONS.get(step, f"Step {step}")

    # Track start time if this is the first call for the step,
    # but also keep the original start if it was already set as "running"
    if step not in _STEP_HISTORY:
        _STEP_HISTORY[step] = {"start_ts": now_ts}
    elif status == "completed" and _STEP_HISTORY[step].get("status") == "running":
        # Transition from running → completed: keep original start time
        pass

    _STEP_HISTORY[step]["end_ts"] = now_ts
    _STEP_HISTORY[step]["status"] = status
    if message:
        _STEP_HISTORY[step]["message"] = message

    # Determine overall pipeline status
    overall_status = "RUNNING"
    any_failed = any(
        h.get("status") == "failed"
        for h in _STEP_HISTORY.values()
    )
    if status == "failed" or any_failed:
        overall_status = "FAILED"
    elif status == "completed" and step == "final":
        overall_status = "COMPLETED"

    # Get pipeline start time from the first tracked step
    pipeline_start = _STEP_HISTORY.get(3, {}).get("start_ts", now_ts)

    # ── Build the status file content ──────────────────────────────────────
    lines = []
    sep = "=" * 80

    lines.append(sep)
    lines.append("  RAMAN WORKFLOW STATUS")
    lines.append(sep)
    lines.append("")
    lines.append(f"  Material:         {material_label}  ({material_name})")
    lines.append(f"  Project Dir:      {base_project_dir}")
    lines.append(f"  Started:          {_fmt_time(pipeline_start)}")
    lines.append(f"  Last Updated:     {now_str}")
    lines.append(f"  Overall Status:   {overall_status}")
    running_step = None
    for k, h in _STEP_HISTORY.items():
        if h.get("status") == "running" and k != "final":
            running_step = k
    if running_step is not None:
        r_desc = _STEP_DESCRIPTIONS.get(running_step, f"Step {running_step}")
        lines.append(f"  Current Step:     {running_step}  —  {r_desc}")
    lines.append("")

    # ═══ STEP HISTORY ══════════════════════════════════════════════════════
    lines.append(sep)
    lines.append("  STEP HISTORY")
    lines.append(sep)
    lines.append("")

    completed_keys = sorted(
        [k for k in _STEP_HISTORY if isinstance(k, int)],
        key=lambda x: (isinstance(x, int), x)
    )
    # Sort by start time for chronological order
    completed_keys.sort(key=lambda k: _STEP_HISTORY[k].get("start_ts", 0))

    for s in completed_keys:
        h = _STEP_HISTORY[s]
        desc = _STEP_DESCRIPTIONS.get(s, f"Step {s}")
        sts = h.get("status", "UNKNOWN").upper()
        start_ts = h.get("start_ts")
        end_ts = h.get("end_ts")
        msg = h.get("message", "")

        # Format the status tag with padding
        status_tag = f"[{sts:>9}]"

        lines.append(f"  STEP {s:<3}  {status_tag}  {desc}")
        if start_ts:
            lines.append(f"           Started:     {_fmt_time(start_ts)}")
        if end_ts and sts in ("COMPLETED", "FAILED"):
            duration_str = _calc_duration(start_ts, end_ts) if start_ts else ""
            lines.append(f"           Ended:       {_fmt_time(end_ts)}  ({duration_str})")
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
        [k for k in _STEP_DESCRIPTIONS if isinstance(k, int)]
    )
    done_or_running = set()
    for k, h in _STEP_HISTORY.items():
        if isinstance(k, int) and h.get("status") in ("completed", "running", "failed"):
            done_or_running.add(k)

    remaining = [s for s in all_step_keys if s not in done_or_running]
    if remaining:
        for s in remaining:
            lines.append(f"  Step {s:<3}  {_STEP_DESCRIPTIONS[s]}")
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
