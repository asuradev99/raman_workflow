"""VASP force-constant loop — runs hf_POSCAR-* directories with retry.

Extracted from automation_raman_analysis.py.  Pure function — no globals.
"""

import os
import subprocess

from .io import run_command
from .vasp import is_calculation_complete
from .config import split_srun_args


def run_hf_loop(hffiles_dir, vasp_script_path, max_restarts,
                srun_args, vasp_binary,
                cpu_flag=False, hf_parallel=False):
    """Run VASP in all hf_POSCAR-* dirs, retrying incomplete ones.

    On retry, only re-runs directories that haven't completed.
    Returns True if all directories succeeded, False otherwise.
    """
    for i in range(max_restarts):
        print(f"\n--- Running VASP iteration {i+1}/{max_restarts} ---")

        all_hf = sorted(
            d for d in os.listdir(hffiles_dir)
            if d.startswith("hf_POSCAR-")
            and os.path.isdir(os.path.join(hffiles_dir, d))
        )

        if not all_hf:
            print("  No hf_POSCAR-* dirs found. Running orchestration script...")
            run_command(vasp_script_path, cwd=hffiles_dir)
            all_hf = sorted(
                d for d in os.listdir(hffiles_dir)
                if d.startswith("hf_POSCAR-")
                and os.path.isdir(os.path.join(hffiles_dir, d))
            )
            if not all_hf:
                print("  ERROR: orchestration script created no hf_POSCAR-* directories.")
                return False

        # On retry, only process incomplete directories
        if i > 0:
            incomplete = [
                d for d in all_hf
                if not is_calculation_complete(os.path.join(hffiles_dir, d))
            ]
            if not incomplete:
                print("  All hf_POSCAR-* directories already complete.")
                return True
            print(f"  Skipping {len(all_hf) - len(incomplete)} completed dirs, "
                  f"retrying {len(incomplete)} incomplete: "
                  f"{', '.join(incomplete[:5])}"
                  f"{'...' if len(incomplete) > 5 else ''}")
            all_hf = incomplete

        # ── Run VASP in the (possibly filtered) set of directories ───────
        if cpu_flag:
            _run_cpu(all_hf, hffiles_dir, srun_args, vasp_binary)
        elif hf_parallel:
            _run_hf_parallel(all_hf, hffiles_dir, srun_args, vasp_binary, vasp_script_path)
        else:
            _run_gpu_serial(all_hf, hffiles_dir, srun_args, vasp_binary,
                            vasp_script_path, first_iteration=(i == 0))

        # ── Validate ─────────────────────────────────────────────────────
        hf_dirs = sorted(
            d for d in os.listdir(hffiles_dir)
            if d.startswith("hf_POSCAR-")
            and os.path.isdir(os.path.join(hffiles_dir, d))
        )
        if not hf_dirs:
            print("No hf_POSCAR-* folders found.")
            return False

        failed = [d for d in hf_dirs
                  if not is_calculation_complete(os.path.join(hffiles_dir, d))]
        if not failed:
            print(f"VASP runs completed in all {len(hf_dirs)} displacement directories.")
            return True
        else:
            print(f"VASP failed or incomplete in {len(failed)}/{len(hf_dirs)} "
                  f"directories: "
                  f"{', '.join(failed[:5])}{'...' if len(failed) > 5 else ''}")
            if i + 1 < max_restarts:
                print(f"Retrying ({i+2}/{max_restarts})...")

    print(f"--- VASP loop failed after {max_restarts} attempts. ---")
    return False


# ── Internal runners ────────────────────────────────────────────────────────

def _run_cpu(dirs, hffiles_dir, srun_args, vasp_binary):
    print(f"  [cpu] Running VASP in {len(dirs)} directories...")
    for d in dirs:
        dpath = os.path.join(hffiles_dir, d)
        print(f"    Running VASP in {d}...")
        run_command(f"srun {srun_args} {vasp_binary} > stdout", cwd=dpath)


def _run_gpu_serial(dirs, hffiles_dir, srun_args, vasp_binary,
                    vasp_script_path, first_iteration=True):
    print(f"  [gpu] Running VASP in {len(dirs)} directories (serial)...")
    if first_iteration:
        run_command(
            f"export SRUN_ARGS='{srun_args}' && bash {vasp_script_path}",
            cwd=hffiles_dir,
        )
    else:
        for d in dirs:
            dpath = os.path.join(hffiles_dir, d)
            print(f"    Running VASP in {d}...")
            run_command(f"srun {srun_args} {vasp_binary} > stdout", cwd=dpath)


def _run_hf_parallel(dirs, hffiles_dir, srun_args, vasp_binary, vasp_script_path):
    print(f"  [gpu:hf_parallel] Running {len(dirs)} directories in parallel...")
    split_args = split_srun_args(srun_args, len(dirs))
    if not split_args:
        print("  [gpu:hf_parallel] split_srun_args failed — falling back to serial")
        run_command(
            f"export SRUN_ARGS='{srun_args}' && bash {vasp_script_path}",
            cwd=hffiles_dir,
        )
        return
    procs = []
    for d, sargs in zip(dirs, split_args):
        dpath = os.path.join(hffiles_dir, d)
        cmd = f"srun --overlap {sargs} {vasp_binary} > stdout"
        print(f"    [{d}] srun --overlap {sargs} {vasp_binary}")
        procs.append(subprocess.Popen(cmd, shell=True, cwd=dpath))
    failed = []
    for d, p in zip(dirs, procs):
        rc = p.wait()
        if rc != 0:
            failed.append(d)
            print(f"    [{d}] ERROR: VASP exited with code {rc}")
    if failed:
        print(f"  [gpu:hf_parallel] {len(failed)}/{len(dirs)} "
              f"directories FAILED: {', '.join(failed)}")
    else:
        print(f"  [gpu:hf_parallel] All {len(dirs)} directories completed.")
