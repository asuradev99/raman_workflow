"""Workflow status tracking: step labels, resume parsing, status table.

Design note: step *numbers* carry no identity anywhere in this module or in
the resume logic. The only stable identity is the step **label** (its
human-readable description string, e.g. "Phonon postprocessing"). Numbers
shown in the rendered table are purely cosmetic — recomputed on every render
as 1-based positions within EXPECTED_LABELS — so reordering, inserting, or
splitting steps never breaks resume matching.
"""

import os
import time

from .io import calc_duration, fmt_time


# ── Canonical relax-step labels ──────────────────────────────────────────────
# Shared by src/__init__.py (pipeline registry) and src/scf_relax.py (the step
# that actually writes status under these labels) so the literal strings
# can't drift out of sync between the two call sites.
RELAX_LABEL_SINGLE = "Initial VASP relaxation"
RELAX_LABEL_DEFECT_1 = "Defect relax 1 (lattice fixed)"
RELAX_LABEL_DEFECT_2 = "Defect relax 2 (full)"
RELAX_LABEL_DEFECT_2_CPU = "Defect relax 2 (CPU)"


def relax_labels(config: dict, start_from_supercell: bool) -> list:
    """Label(s) for the scf_relax step — always the single unit-cell relaxation.

    Defect two-stage relaxation is handled by the separate defect_relax_1 /
    defect_relax_2 / defect_relax_2_cpu entries in STEP_REGISTRY; they are
    never dispatched through this step.
    """
    return [RELAX_LABEL_SINGLE]


# EXPECTED_LABELS is the full ordered label sequence for THIS run, set once
# at startup (see set_expected_labels) from the Step registry + config. It
# drives both the status-table row list (including not-yet-started rows)
# and what "everything completed" means for parse_resume_step.
EXPECTED_LABELS: list = []

# Accumulated step history (preserved across write_status calls), keyed by label.
STEP_HISTORY: dict = {}


def set_expected_labels(labels: list) -> None:
    """Set the ordered list of labels for this run's pipeline.

    Must be called once at startup, before any write_status()/
    parse_resume_step() calls.
    """
    EXPECTED_LABELS.clear()
    EXPECTED_LABELS.extend(labels)


def _step_number(label: str) -> int:
    """1-based display position of `label` in EXPECTED_LABELS — cosmetic only."""
    try:
        return EXPECTED_LABELS.index(label) + 1
    except ValueError:
        return 0


# ── Step banners ───────────────────────────────────────────────────────────
def print_step_header(label: str):
    """Print a visually distinct step-start banner to the log."""
    text = f"  STEP {_step_number(label)} — {label}"
    text = text[:66].ljust(66)
    bar = "═" * 66
    print(f"\n╔{bar}╗")
    print(f"║{text}║")
    print(f"╚{bar}╝\n")


def print_step_result(label: str, ok=True, duration_s=0, message=""):
    """Print a step-completion or step-failure message to the log."""
    icon = "✓" if ok else "✗"
    status_word = "COMPLETE" if ok else "FAILED"
    dur_str = ""
    if ok and duration_s > 0:
        dur_str = f" ({calc_duration(0, duration_s)})"
    elif duration_s > 0:
        dur_str = f" [{calc_duration(0, duration_s)}]"
    msg_suffix = f" — {message}" if message else ""
    print(f"\n  {icon} STEP {_step_number(label)} {status_word} — {label}{dur_str}{msg_suffix}\n")


def begin_step(ctx, description):
    """Print step header, mark running in status file, return t_start.

    Replaces the three-line boilerplate at the top of every step's run():
        print_step_header(step)
        ctx.write_status(step, "running", description)
        t_start = time.time()
    """
    step = ctx.current_label
    print_step_header(step)
    ctx.write_status(step, "running", description)
    return time.time()


def finish_dispatch_step(ctx, ok, t_start, n_dirs, compute_mode, name):
    """Write final status and raise on failure for a dispatch-based step.

    Replaces the identical 8-line ok/fail/complete block at the end of
    force_constants.py and resonant_vasp.py.
    """
    step = ctx.current_label
    if not ok:
        ctx.write_status(step, "failed", f"{name} incomplete ({compute_mode})")
        print_step_result(step, ok=False, duration_s=time.time() - t_start,
                          message=f"{compute_mode} failed")
        raise RuntimeError(f"{step} failed ({compute_mode})")
    ctx.write_status(step, "completed", f"{name} — {n_dirs} dirs ({compute_mode})")
    print_step_result(step, ok=True, duration_s=time.time() - t_start,
                      message=f"{n_dirs} dirs ({compute_mode})")


def _icon(sts):
    return {"completed": "✓", "running": "▶", "failed": "✗"}.get(sts, "—")


def _boxed(line):
    """Wrap a single line in a box so it visually stands out when scanning
    the log -- especially now that module-load/conda-activate stderr is no
    longer suppressed and can appear between a step's running/completed lines.
    """
    width = len(line) + 2
    top = "┌" + "─" * width + "┐"
    mid = f"│ {line} │"
    bot = "└" + "─" * width + "┘"
    return "\n".join(["", top, mid, bot, ""])


# ── Concise per-transition log line ─────────────────────────────────────────
def write_status(label, status, message="", *,
                 status_file, material_label, material_name, base_project_dir):
    """Append ONE concise log line for a step status transition.

    `label` is the step's human-readable description — the canonical
    identity for everything (table rows). The special label "final" marks
    overall pipeline completion. The full step-overview table is NOT
    rendered here on every call (that used to make every single step
    transition append a whole box-drawn table) — it's only rendered at
    real stopping points: pipeline end (success or failure) via this
    function, or once at the start of a resume via render_status_table()
    called explicitly from automation_raman_analysis.py.
    """
    now_ts = time.time()

    if label not in STEP_HISTORY:
        STEP_HISTORY[label] = {"start_ts": now_ts}
    STEP_HISTORY[label]["end_ts"] = now_ts
    STEP_HISTORY[label]["status"] = status
    if message:
        STEP_HISTORY[label]["message"] = message

    h = STEP_HISTORY[label]
    dur = calc_duration(h["start_ts"], h["end_ts"]) if status in ("completed", "failed") else ""
    total = len(EXPECTED_LABELS) if EXPECTED_LABELS else 0
    if label == "final":
        pos = "done"
    else:
        num = _step_number(label)
        pos = f"{num}/{total}" if total else str(num)
    dur_str = f"  ({dur})" if dur else ""
    msg_str = f" — {message}" if message else ""
    line = f"{fmt_time(now_ts)}  [{pos}] {_icon(status)} {status.upper():<9} {label}{dur_str}{msg_str}"

    # completed/failed are the delineating events worth spotting at a glance;
    # "running" stays a single plain line to keep the common case concise.
    text = _boxed(line) if status in ("completed", "failed") else line

    try:
        with open(status_file, "a") as f:
            f.write(text + "\n")
    except Exception as e:
        print(f"[status] Warning: Could not write status file: {e}")

    # Natural stopping points only: pipeline-ending failure, or final success.
    if status == "failed" or (status == "completed" and label == "final"):
        render_status_table(status_file, material_name)


# ── Full status table — called only at start-of-resume or pipeline end ─────
def render_status_table(status_file, material_name):
    """Append the full box-drawn step-overview table to *status_file*.

    Not called on every step transition (see write_status) — only:
      * once at the start of a run, if resuming (some steps already done)
      * at the end of the run (pipeline success or a fatal step failure)
    """
    now_ts = time.time()
    now_str = fmt_time(now_ts)

    any_failed = any(h.get("status") == "failed" for h in STEP_HISTORY.values())
    final_done = STEP_HISTORY.get("final", {}).get("status") == "completed"
    if any_failed:
        overall_status = "FAILED"
    elif final_done:
        overall_status = "COMPLETED"
    else:
        overall_status = "RUNNING"

    first_label = EXPECTED_LABELS[0] if EXPECTED_LABELS else None
    pipeline_start = STEP_HISTORY.get(first_label, {}).get("start_ts", now_ts) if first_label else now_ts

    def _dur(s, e):
        return calc_duration(s, e) if s and e else ""

    running_label = None
    failed_label = None
    for k, h in STEP_HISTORY.items():
        if h.get("status") == "running" and k != "final":
            running_label = k
        if h.get("status") == "failed" and k != "final":
            failed_label = k

    lines = []
    lines.append("")
    lines.append("━" * 78)
    lines.append(f"  RAMAN WORKFLOW  │  {material_name}  │  {now_str}")
    lines.append("━" * 78)
    lines.append("")

    elapsed = calc_duration(pipeline_start, now_ts) if pipeline_start else ""
    summary_parts = [f"Status   {overall_status}"]
    if running_label is not None:
        summary_parts.append(f"— Step {_step_number(running_label)} ({running_label})")
    if overall_status == "FAILED" and failed_label is not None:
        msg = STEP_HISTORY.get(failed_label, {}).get("message", "")
        summary_parts.append(f"— {failed_label}" + (f": {msg}" if msg else ""))
    lines.append(f"  {'  '.join(summary_parts)}")
    lines.append(f"  Started  {fmt_time(pipeline_start)}")
    lines.append(f"  Elapsed  {elapsed}")
    lines.append("")

    rows = []
    for lbl in EXPECTED_LABELS:
        h = STEP_HISTORY.get(lbl, {})
        sts = h.get("status", "")
        icon = _icon(sts)
        dur = _dur(h.get("start_ts"), h.get("end_ts"))
        desc_display = lbl[:40]
        rows.append((_step_number(lbl), icon, sts.upper() if sts else "—", desc_display, dur))

    col_widths = [4, 3, 8, 42, 8]
    sep_line = "─" * (sum(col_widths) + len(col_widths) + 1)

    def _fmt_row(cols):
        parts = []
        for i, (c, w) in enumerate(zip(cols, col_widths)):
            if i in (0, 4):
                parts.append(f"{c:>{w}}")
            elif i == 1:
                parts.append(f" {c} ")
            else:
                parts.append(f"{c:<{w}}")
        return "│ " + " │ ".join(parts) + " │"

    lines.append("  ┌" + sep_line + "┐")
    lines.append("  " + _fmt_row(["#", "", "Status", "Description", "Duration"]))
    lines.append("  │" + sep_line + "│")
    for num, icon, sts_text, desc, dur in rows:
        lines.append("  " + _fmt_row([num, icon, sts_text, desc, dur]))
    lines.append("  └" + sep_line + "┘")
    lines.append("")
    lines.append("━" * 78)
    lines.append("")

    try:
        with open(status_file, "a") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[status] Warning: Could not write status table: {e}")


def make_write_status(status_file, material_label, material_name, base_project_dir):
    """Create a ``write_status`` callable pre-bound to pipeline-specific values."""
    def _inner(label, status, message=""):
        write_status(
            label, status, message,
            status_file=status_file,
            material_label=material_label,
            material_name=material_name,
            base_project_dir=base_project_dir,
        )
    return _inner


# NOTE: resume is entirely file-based (each step's is_complete(work_dir, config)
# checks real VASP output files — see _step_is_done() in
# automation_raman_analysis.py). workflow.log is written for human monitoring
# only and is never parsed to decide what to resume. A previous version of
# this module had a parse_resume_step() that re-derived resume state from the
# log table; it was dead code (never called) and has been removed so nothing
# here even suggests the log is a source of truth for resume.
