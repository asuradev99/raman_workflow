"""VASP directory runners — serial, parallel, and retry loop.

Public API:
  run_vasp_in_dirs  — run srun in a list of dirs with retry (used by both
                      force_constants.py and resonant_vasp.py)
  list_hf_dirs      — discover hf_POSCAR-* directories
"""

import os
import subprocess

from .io import run_command
from .vasp import is_calculation_complete
from .config import split_srun_args


HF_DIR_PREFIX = "hf_POSCAR-"


def list_hf_dirs(hffiles_dir, include_groundstate=False):
    """Return sorted absolute paths of hf_POSCAR-* dirs in *hffiles_dir*.

    Args:
        hffiles_dir: Path to the hf/ directory.
        include_groundstate: If True, prepend the groundstate/ dir when present.
    """
    dirs = sorted(
        os.path.join(hffiles_dir, d)
        for d in os.listdir(hffiles_dir)
        if d.startswith(HF_DIR_PREFIX) and os.path.isdir(os.path.join(hffiles_dir, d))
    )
    if include_groundstate:
        gs = os.path.join(hffiles_dir, "groundstate")
        if os.path.isdir(gs):
            dirs.insert(0, gs)
    return dirs


def run_vasp_in_dirs(dirs, srun_args, vasp_binary, *,
                     max_restarts=3, hf_parallel=False, cpu_flag=False,
                     log_name="relaxation.stdout"):
    """Run VASP in *dirs* (absolute paths) with retry and optional parallel execution.

    Re-checks ``is_calculation_complete`` at the start of each attempt and only
    re-runs incomplete dirs. Both serial and parallel runners use soft-fail
    internally — the caller's convergence-check loop validates VASP output.
    Returns True when all dirs are complete after all attempts.

    Args:
        dirs:          Absolute paths to the VASP calculation directories.
        srun_args:     srun argument string (e.g. ``"--gpus-per-node=4 --nodes=1"``).
        vasp_binary:   Path to the VASP binary.
        max_restarts:  Maximum number of retry attempts.
        hf_parallel:   Run all dirs concurrently with split srun args.
        cpu_flag:      Serial CPU mode (overrides hf_parallel).
        log_name:      Filename for VASP stdout within each dir.
    """
    for attempt in range(1, max_restarts + 1):
        todo = [d for d in dirs if not is_calculation_complete(d)]
        if not todo:
            break
        if len(todo) < len(dirs):
            print(f"  [vasp] Attempt {attempt}/{max_restarts}: "
                  f"{len(todo)}/{len(dirs)} dir(s) remaining...")
        else:
            print(f"  [vasp] Attempt {attempt}/{max_restarts}: "
                  f"{len(todo)} dir(s)...")
        if hf_parallel and not cpu_flag:
            _run_hf_parallel(todo, srun_args, vasp_binary, log_name=log_name)
        else:
            _run_serial(todo, srun_args, vasp_binary, log_name=log_name)
    return all(is_calculation_complete(d) for d in dirs)


# ── Internal runners (accept absolute paths) ────────────────────────────────

def _run_serial(dirs, srun_args, vasp_binary, log_name="relaxation.stdout"):
    """Run VASP sequentially in each directory (absolute paths, soft-fail)."""
    print(f"  Running VASP serially in {len(dirs)} director{'y' if len(dirs) == 1 else 'ies'}...")
    for dirpath in dirs:
        print(f"    Running VASP in {os.path.basename(dirpath)}...")
        run_command(
            f"srun {srun_args} {vasp_binary} > {log_name}",
            cwd=dirpath,
            check_success=False,
        )


def _run_hf_parallel(dirs, srun_args, vasp_binary, log_name="relaxation.stdout"):
    """Run VASP concurrently across dirs using split srun args and --overlap."""
    print(f"  [hf_parallel] Running {len(dirs)} dir(s) in parallel...")
    split_args = split_srun_args(srun_args, len(dirs))
    if not split_args:
        print("  [hf_parallel] split_srun_args failed — falling back to serial")
        _run_serial(dirs, srun_args, vasp_binary, log_name=log_name)
        return
    procs = []
    for dirpath, sargs in zip(dirs, split_args):
        cmd = f"srun --overlap {sargs} {vasp_binary} > {log_name}"
        print(f"    [{os.path.basename(dirpath)}] {cmd}")
        procs.append(subprocess.Popen(cmd, shell=True, cwd=dirpath))
    failed = []
    for dirpath, proc in zip(dirs, procs):
        rc = proc.wait()
        if rc != 0:
            failed.append(os.path.basename(dirpath))
            print(f"    [{os.path.basename(dirpath)}] ERROR: VASP exited with code {rc}")
    if failed:
        print(f"  [hf_parallel] {len(failed)}/{len(dirs)} FAILED: {', '.join(failed)}")
    else:
        print(f"  [hf_parallel] All {len(dirs)} dir(s) completed.")
