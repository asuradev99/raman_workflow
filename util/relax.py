"""VASP relaxation runner: stall watchdog + ZBRENT retry in one loop."""

import os
import shutil
import signal
import subprocess

from .vasp import check_vasp_convergence, _has_zbrent_error, _extract_max_force


def run_relaxation(scf_dir, srun_args, vasp_binary, stage_label="",
                                     max_attempts=3, stall_poll_s=900):
    """Run VASP relaxation, self-healing stalls and retrying on ZBRENT crashes.

    Each attempt:
      1. Removes stale OUTCAR so convergence check reads only this run's data.
      2. Launches ``srun`` and polls OSZICAR every ``stall_poll_s`` seconds.
         If OSZICAR hasn't updated — the signature of an MPI-deadlock stall
         (all ranks ~100% CPU, 0% GPU, no file progress) — sends SIGINT to
         srun (terminates the job step, not the allocation), copies
         CONTCAR → POSCAR to preserve geometry, and relaunches srun in the
         same allocation.  Stall restarts are unlimited within one attempt.
      3. After srun exits cleanly, calls ``check_vasp_convergence``.
         On success, returns True immediately.
      4. On failure: if ZBRENT with forces < 0.01 eV/Å, copies CONTCAR → POSCAR
         and retries.  Otherwise copies CONTCAR → POSCAR as best-effort and
         retries.  Gives up after ``max_attempts`` and returns False.
    """
    stdout_path = os.path.join(scf_dir, "relaxation.stdout")
    outcar_path = os.path.join(scf_dir, "OUTCAR")
    contcar_path = os.path.join(scf_dir, "CONTCAR")
    poscar_path = os.path.join(scf_dir, "POSCAR")
    watch_path = os.path.join(scf_dir, "OSZICAR")
    cmd = f"srun {srun_args} {vasp_binary} > relaxation.stdout"

    def _mtime():
        return os.path.getmtime(watch_path) if os.path.exists(watch_path) else None

    for attempt in range(1, max_attempts + 1):
        if os.path.exists(outcar_path):
            os.remove(outcar_path)

        print(f"\n  [relax] Attempt {attempt}/{max_attempts}...")

        # ── Inner loop: run srun, restart on stall ────────────────────────────
        while True:
            print(f"\n--- Running: {cmd} ---")
            print(f"--- In directory: {scf_dir} ---")
            proc = subprocess.Popen(cmd, shell=True, cwd=scf_dir, executable="/bin/bash")
            last_mtime = _mtime()
            stalled = False

            while True:
                try:
                    proc.wait(timeout=stall_poll_s)
                    break
                except subprocess.TimeoutExpired:
                    cur_mtime = _mtime()
                    if cur_mtime == last_mtime:
                        print(f"  [watchdog] No OSZICAR update in "
                              f"{stall_poll_s // 60} min — stalled. Restarting srun...")
                        proc.send_signal(signal.SIGINT)
                        try:
                            proc.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        stalled = True
                        break
                    last_mtime = cur_mtime

            if stalled:
                if os.path.exists(contcar_path) and os.path.getsize(contcar_path) > 0:
                    shutil.copy2(contcar_path, poscar_path)
                    print(f"  [watchdog] Copied CONTCAR → POSCAR, relaunching VASP...")
                else:
                    print(f"  [watchdog] No usable CONTCAR yet — relaunching from existing POSCAR...")
                continue  # relaunch srun

            print("--- Command completed successfully ---" if proc.returncode == 0
                  else f"--- Command exited with code {proc.returncode} ---")
            break  # srun exited cleanly; proceed to convergence check

        # ── Convergence check + ZBRENT retry logic ────────────────────────────
        try:
            check_vasp_convergence(scf_dir, stage_label)
            print(f"  [relax] Relaxation succeeded on attempt {attempt}.")
            return True
        except RuntimeError as e:
            print(f"  [relax] Attempt {attempt} failed: {e}")
            if attempt < max_attempts:
                if _has_zbrent_error(stdout_path):
                    max_f = _extract_max_force(outcar_path)
                    if max_f is not None and max_f < 0.01:
                        shutil.copy(contcar_path, poscar_path)
                        print(f"  [zbrent] Forces converged (max |F| = {max_f:.6f} eV/Å). "
                              f"Copied CONTCAR → POSCAR for retry.")
                    else:
                        f_str = f"{max_f:.6f} eV/Å" if max_f is not None else "unavailable"
                        print(f"  [zbrent] ZBRENT detected (max |F| = {f_str} — not yet converged).")
                elif os.path.exists(contcar_path) and os.path.getsize(contcar_path) > 0:
                    shutil.copy(contcar_path, poscar_path)
                    print(f"  [relax] Copied CONTCAR → POSCAR to continue from best structure.")

    print(f"  [relax] Max attempts ({max_attempts}) reached.")
    return False
