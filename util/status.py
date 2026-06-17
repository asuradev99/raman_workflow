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


def relax_labels(config: dict, start_from_supercell: bool) -> list:
    """Label(s) the relax step uses for this material's config.

    Two-stage defect relax (relax 1 + relax 2) if the config requests it and
    provides both INCAR templates; otherwise the standard single-stage
    unit-cell relaxation.
    """
    templates = config.get("incar_templates", {})
    has_defect_pair = (
        start_from_supercell
        and "defect_relax_fixed" in templates
        and "defect_relax_full" in templates
    )
    if has_defect_pair:
        return [RELAX_LABEL_DEFECT_1, RELAX_LABEL_DEFECT_2]
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


# ── Status table writer ────────────────────────────────────────────────────
def write_status(label, status, message="", *,
                 status_file, material_label, material_name, base_project_dir):
    """Write a combined status-overview + chronological log entry.

    `label` is the step's human-readable description — the canonical
    identity for everything (table rows, resume matching). The special
    label "final" marks overall pipeline completion.
    """
    now_ts = time.time()
    now_str = fmt_time(now_ts)

    if label not in STEP_HISTORY:
        STEP_HISTORY[label] = {"start_ts": now_ts}

    STEP_HISTORY[label]["end_ts"] = now_ts
    STEP_HISTORY[label]["status"] = status
    if message:
        STEP_HISTORY[label]["message"] = message

    any_failed = any(h.get("status") == "failed" for h in STEP_HISTORY.values())
    if status == "failed" or any_failed:
        overall_status = "FAILED"
    elif status == "completed" and label == "final":
        overall_status = "COMPLETED"
    else:
        overall_status = "RUNNING"

    first_label = EXPECTED_LABELS[0] if EXPECTED_LABELS else label
    pipeline_start = STEP_HISTORY.get(first_label, {}).get("start_ts", now_ts)

    def _dur(s, e):
        return calc_duration(s, e) if s and e else ""

    def _icon(sts):
        return {"completed": "✓", "running": "▶",
                "failed": "✗"}.get(sts, "—")

    running_label = None
    for k, h in STEP_HISTORY.items():
        if h.get("status") == "running" and k != "final":
            running_label = k

    lines = []
    lines.append("")
    lines.append("━" * 78)
    header = f"  RAMAN WORKFLOW  │  {material_name}  │  {now_str}"
    lines.append(header)
    lines.append("━" * 78)
    lines.append("")

    elapsed = calc_duration(pipeline_start, now_ts) if pipeline_start else ""
    summary_parts = [f"Status   {overall_status}"]
    if running_label is not None:
        summary_parts.append(f"— Step {_step_number(running_label)} ({running_label})")
    if overall_status == "FAILED" and message:
        summary_parts.append(f"— {message}")
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
        return "│ " + " │ ".join(parts) + " │"

    lines.append("  ┌" + sep_line + "┐")
    lines.append("  " + _fmt_row(["#", "", "Status", "Description", "Duration"]))
    lines.append("  │" + sep_line + "│")

    for num, icon, sts_text, desc, dur in rows:
        lines.append("  " + _fmt_row([num, icon, sts_text, desc, dur]))

    lines.append("  └" + sep_line + "┘")
    lines.append("")
    lines.append("━" * 78)
    lines.append("  STEP LOG")
    lines.append("━" * 78)
    lines.append("")

    try:
        with open(status_file, "a") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[status] Warning: Could not write status file: {e}")


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


# ── Resume parser ──────────────────────────────────────────────────────────
def parse_resume_step(status_file, step_history, expected_labels):
    """Parse the last status table in workflow.log to determine the resume label.

    Matching is done purely against the Description column text — the
    leading step-number column is cosmetic and intentionally ignored, so
    table re-numbering across code changes never breaks resume. Returns the
    label to resume at, or None if every label in `expected_labels` is
    COMPLETED.
    """
    if not expected_labels:
        raise ValueError("parse_resume_step: expected_labels must be non-empty")

    if not os.path.exists(status_file):
        print(f"[resume] No existing status file at {status_file}. "
              f"Starting from \"{expected_labels[0]}\".")
        return expected_labels[0]

    try:
        with open(status_file) as f:
            content = f.read()

        table_starts = [i for i, c in enumerate(content) if c == '┌']
        if not table_starts:
            print(f"[resume] No status table found in {status_file}. "
                  f"Starting from \"{expected_labels[0]}\".")
            return expected_labels[0]

        last_table_start = table_starts[-1]
        table_end = content.find('└', last_table_start)
        if table_end == -1:
            table_end = len(content)

        table_section = content[last_table_start:table_end]

        completed_labels = set()
        running_label = None
        failed_label = None

        for line in table_section.split('\n'):
            line = line.strip()
            if not line.startswith('│'):
                continue
            parts = [p.strip() for p in line.split('│')]
            if len(parts) < 5:
                continue
            label_text = parts[4].strip()
            status_text = parts[3].strip().upper()
            if not label_text or label_text == "Description":
                continue  # header row

            if status_text == "COMPLETED":
                completed_labels.add(label_text)
                step_history[label_text] = {
                    "status": "completed",
                    "start_ts": 0, "end_ts": 0,
                    "message": "Resumed — completed in previous run",
                }
            elif status_text == "RUNNING":
                running_label = label_text
            elif status_text == "FAILED":
                failed_label = label_text

        if running_label is not None:
            step_history[running_label] = {
                "status": "running", "start_ts": 0, "end_ts": 0,
                "message": "Interrupted — was RUNNING",
            }
            print(f"[resume] \"{running_label}\" was ACTIVE (likely crashed). "
                  f"Retrying from there.")
            return running_label

        if failed_label is not None:
            print(f"[resume] \"{failed_label}\" had FAILED. Retrying from there.")
            return failed_label

        for label in expected_labels:
            if label not in completed_labels:
                print(f"[resume] Continuing from \"{label}\".")
                return label

        print("[resume] All steps already completed. Nothing to do.")
        return None

    except Exception as e:
        print(f"[resume] Warning: Could not parse {status_file}: {e}")
        print(f"[resume] Starting from \"{expected_labels[0]}\" (full pipeline).")
        return expected_labels[0]
