"""I/O utilities: Tee, run_command, time formatting, exception hook."""

import os
import subprocess
import sys
import time
import traceback


class Tee:
    """Duplicate all writes to the real stdout, a status file, and optionally a
    full-output log file."""
    def __init__(self, log_path, out_path=None):
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
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


def run_command(command, cwd=None, shell=True, check_success=True, verbose=True):
    """Execute a shell command, optionally printing banners.

    Args:
        command: The shell command to execute.
        cwd: Working directory for the command.
        shell: Whether to use the shell. Defaults to True.
        check_success: Raise RuntimeError on non-zero exit. Defaults to True.
        verbose: Print ``--- Running ---`` banners. Set False for file-copy
            operations where the noise would clutter the log.
    """
    if verbose:
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
        if verbose:
            print("--- Command completed successfully ---")
    except RuntimeError:
        raise
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


def print_job_header(material_label, material_name, work_dir, status_file,
                     scratch_flag, restart_flag, cpu_flag,
                     compute_mode="interactive_manual", inside_salloc=False):
    """Print a formatted job-start banner to stdout and the log."""
    _now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    _node = os.uname().nodename
    _mode_suffix = " [inside salloc]" if inside_salloc else ""
    _sep = "\u2550" * 78
    print(f"\n{_sep}")
    print(f"{_sep}")
    print(f"  RAMAN PIPELINE — JOB START{_mode_suffix}")
    print(f"{_sep}")
    print(f"  Date        : {_now}")
    print(f"  Host        : {_node}")
    print(f"  Material    : {material_label}  ({material_name})")
    print(f"  Work dir    : {work_dir}")
    print(f"  Log file    : {status_file}")
    print(f"  Compute     : {compute_mode}")
    print(
        f"  Flags       : scratch={'on' if scratch_flag else 'off'}  "
        f"restart={'on' if restart_flag else 'off'}  "
        f"cpu={'on' if cpu_flag else 'off'}"
    )
    print(f"{_sep}")
    print(f"{_sep}\n")


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
