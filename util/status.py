"""Workflow status tracking: step descriptions, resume parsing, status table."""

import os
import time

from .io import calc_duration, fmt_time


# STEP_DESCRIPTIONS is populated at pipeline startup from PIPELINE (src/__init__.py)
# so that the descriptions in the status table always match the Step registry.
# The dict is initialised empty here; automation_raman_analysis.py calls
# populate_step_descriptions() before running any steps.
STEP_DESCRIPTIONS: dict = {}

# Accumulated step history (preserved across write_status calls)
STEP_HISTORY: dict = {}

# Total number of steps for display
TOTAL_STEPS = 8


def populate_step_descriptions(descriptions: dict) -> None:
    """Inject step descriptions derived from the PIPELINE registry.

    Called once at pipeline startup so the status table uses the same
    human-readable labels as the Step objects in src/__init__.py.
    """
    STEP_DESCRIPTIONS.clear()
    STEP_DESCRIPTIONS.update(descriptions)


# ── Step banners ───────────────────────────────────────────────────────────
def print_step_header(step_num, description=""):
    """Print a visually distinct step-start banner to the log."""
    desc = description or STEP_DESCRIPTIONS.get(step_num, f"Step {step_num}")
    text = f"  STEP {step_num} — {desc}"
    text = text[:66].ljust(66)
    bar = "═" * 66
    print(f"\n╔{bar}╗")
    print(f"║{text}║")
    print(f"╚{bar}╝\n")


def print_step_result(step_num, ok=True, duration_s=0, message=""):
    """Print a step-completion or step-failure message to the log."""
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


# ── Status table writer ────────────────────────────────────────────────────
def write_status(step, status, message="", *,
                 status_file, material_label, material_name, base_project_dir):
    """Write a combined status-overview + chronological log entry."""
    now_ts = time.time()
    now_str = fmt_time(now_ts)
    step_desc = STEP_DESCRIPTIONS.get(step, f"Step {step}")

    if step not in STEP_HISTORY:
        STEP_HISTORY[step] = {"start_ts": now_ts}
    elif status == "completed" and STEP_HISTORY[step].get("status") == "running":
        pass

    STEP_HISTORY[step]["end_ts"] = now_ts
    STEP_HISTORY[step]["status"] = status
    if message:
        STEP_HISTORY[step]["message"] = message

    any_failed = any(h.get("status") == "failed" for h in STEP_HISTORY.values())
    if status == "failed" or any_failed:
        overall_status = "FAILED"
    elif status == "completed" and step == "final":
        overall_status = "COMPLETED"
    else:
        overall_status = "RUNNING"

    pipeline_start = STEP_HISTORY.get(1, {}).get("start_ts", now_ts)

    def _dur(s, e):
        return calc_duration(s, e) if s and e else ""

    def _icon(sts):
        return {"completed": "\u2713", "running": "\u25B6",
                "failed": "\u2717"}.get(sts, "\u2014")

    running_step = None
    for k, h in STEP_HISTORY.items():
        if h.get("status") == "running" and k != "final":
            running_step = k

    lines = []
    lines.append("")
    lines.append("\u2501" * 78)
    header = f"  RAMAN WORKFLOW  \u2502  {material_name}  \u2502  {now_str}"
    lines.append(header)
    lines.append("\u2501" * 78)
    lines.append("")

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

    table_keys = sorted(k for k in STEP_DESCRIPTIONS if isinstance(k, (int, float)))
    rows = []
    for s in table_keys:
        h = STEP_HISTORY.get(s, {})
        sts = h.get("status", "")
        desc = STEP_DESCRIPTIONS[s]
        icon = _icon(sts)
        dur = _dur(h.get("start_ts"), h.get("end_ts"))
        desc_display = desc[:40]
        rows.append((s, icon, sts.upper() if sts else "\u2014", desc_display, dur))

    col_widths = [4, 3, 8, 42, 8]
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

    lines.append("  \u250c" + sep_line + "\u2510")
    lines.append("  " + _fmt_row(["#", "", "Status", "Description", "Duration"]))
    lines.append("  \u2502" + sep_line + "\u2502")

    for s, icon, sts_text, desc, dur in rows:
        lines.append("  " + _fmt_row([s, icon, sts_text, desc, dur]))

    lines.append("  \u2514" + sep_line + "\u2518")
    lines.append("")
    lines.append("\u2501" * 78)
    lines.append("  STEP LOG")
    lines.append("\u2501" * 78)
    lines.append("")

    try:
        with open(status_file, "a") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[status] Warning: Could not write status file: {e}")


def make_write_status(status_file, material_label, material_name, base_project_dir):
    """Create a ``write_status`` callable pre-bound to pipeline-specific values."""
    def _inner(step, status, message=""):
        write_status(
            step, status, message,
            status_file=status_file,
            material_label=material_label,
            material_name=material_name,
            base_project_dir=base_project_dir,
        )
    return _inner


# ── Resume parser ──────────────────────────────────────────────────────────
def parse_resume_step(status_file, step_history, step_descriptions):
    """Parse the last status table in workflow.log to determine resume step."""
    if not os.path.exists(status_file):
        print(f"[resume] No existing status file at {status_file}. Starting from step 1.")
        return 1

    try:
        with open(status_file) as f:
            content = f.read()

        table_starts = [i for i, c in enumerate(content) if c == '\u250c']
        if not table_starts:
            print(f"[resume] No status table found in {status_file}. Starting from step 1.")
            return 1

        last_table_start = table_starts[-1]
        table_end = content.find('\u2514', last_table_start)
        if table_end == -1:
            table_end = len(content)

        table_section = content[last_table_start:table_end]

        completed_steps = set()
        running_step = None
        failed_step = None

        for line in table_section.split('\n'):
            line = line.strip()
            if not line.startswith('\u2502'):
                continue
            parts = [p.strip() for p in line.split('\u2502')]
            if len(parts) < 4:
                continue
            try:
                step_str = parts[1].strip()
                step_num = float(step_str) if '.' in step_str else int(step_str)
            except (ValueError, IndexError):
                continue

            status_text = parts[3].strip().upper() if len(parts) > 3 else ""

            if status_text == "COMPLETED":
                completed_steps.add(step_num)
                step_history[step_num] = {
                    "status": "completed",
                    "start_ts": 0, "end_ts": 0,
                    "message": "Resumed — completed in previous run",
                }
            elif status_text == "RUNNING":
                running_step = step_num
            elif status_text == "FAILED":
                failed_step = step_num

        if running_step is not None:
            step_history[running_step] = {
                "status": "running", "start_ts": 0, "end_ts": 0,
                "message": "Interrupted — was RUNNING",
            }
            print(f"[resume] Step {running_step} was ACTIVE (likely crashed). "
                  f"Retrying from step {running_step}.")
            return running_step

        if failed_step is not None:
            print(f"[resume] Step {failed_step} was FAILED. Retrying from step {failed_step}.")
            return failed_step

        all_step_keys = sorted(k for k in step_descriptions if isinstance(k, (int, float)))
        for s in all_step_keys:
            if s not in completed_steps:
                print(f"[resume] Continuing from step {s} "
                      f"({step_descriptions.get(s, 'Unknown')}).")
                return s

        print("[resume] All steps already completed. Nothing to do.")
        return None

    except Exception as e:
        print(f"[resume] Warning: Could not parse {status_file}: {e}")
        print("[resume] Starting from step 1 (full pipeline).")
        return 1
