"""Step 7 — Resonant VASP runs in all ra_pos_* directories (mode-dispatched)."""
import os, time, glob
from util.compute import submit_many, submit_sbatch_wrapper, run_serial_in_salloc_with_retry, build_serial_vasp_wrapper
from util.io import run_command
from util.vasp import check_vasp_convergence, check_dielectric_complete, check_no_selective_dynamics, is_calculation_complete
from util.status import print_step_header, print_step_result


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_step
    raman_dir = ctx.raman_dir
    script_dir = ctx.script_dir
    scripts_root = os.path.join(os.path.dirname(script_dir), "scripts")
    srun_args = ctx.srun_args
    compute_mode = ctx.compute_mode

    print_step_header(step)
    write_status(step, "running", f"Resonant VASP runs ({compute_mode})")
    t_start = time.time()

    ra_dirs = sorted(glob.glob(os.path.join(raman_dir, "ra_pos_*")))
    if not ra_dirs:
        raise RuntimeError("No ra_pos_* directories found")
    if ra_dirs:
        check_no_selective_dynamics(os.path.join(ra_dirs[0], "POSCAR"), "ra_pos_* POSCAR")

    completed = [d for d in ra_dirs if is_calculation_complete(d)]
    todo = [d for d in ra_dirs if d not in completed]
    if completed:
        print(f"  [resume] Skipping {len(completed)} completed ra_pos_* dirs, "
              f"running {len(todo)} remaining")
    if not todo:
        print("  All ra_pos_* directories already complete.")
        write_status(step, "completed", f"Resonant VASP — {len(ra_dirs)} dirs (cached)")
        print_step_result(step, ok=True, duration_s=time.time() - t_start,
                          message=f"{len(ra_dirs)} directories (cached)")
        return

    # ══════════════════════════════════════════════════════════════════════════
    #  MODE DISPATCH
    # ══════════════════════════════════════════════════════════════════════════

    if compute_mode in ("sbatch_parallel", "sbatch"):
        script_path = os.path.join(scripts_root, "sbatch_raman_dir.sh")
        max_retries = getattr(ctx, "vasp_max_restarts", 3)
        ok = False
        for attempt in range(1, max_retries + 1):
            incomplete = [d for d in ra_dirs if not is_calculation_complete(d)]
            if not incomplete:
                ok = True
                break
            print(f"  [sbatch_parallel] Attempt {attempt}/{max_retries}: "
                  f"submitting {len(incomplete)}/{len(ra_dirs)} raman dirs…")
            submit_many(script_path, incomplete, job_name_prefix="raman",
                        system_paths=ctx.system_paths,
                        srun_args=ctx.vasp_srun_per_dir,
                        sbatch_args=ctx.vasp_sbatch_per_dir)

    elif compute_mode == "sbatch_serial":
        if ctx.inside_salloc:
            # Inside the sbatch allocation — use normal srun
            ok = True
            for dirpath in todo:
                dirname = os.path.basename(dirpath)
                print(f"    Running VASP in {dirname}...")
                try:
                    run_command(
                        f"srun {srun_args} {ctx.vasp_binary} > stdout",
                        cwd=dirpath,
                    )
                except Exception as e:
                    print(f"  [serial] VASP failed in {dirname}: {e}")
                    ok = False
        else:
            print(f"  [sbatch_serial] Submitting 1 job for {len(todo)} raman dirs…")
            script_path = os.path.join(scripts_root, "sbatch_all_raman.sh")
            with open(script_path) as _f:
                script_content = _f.read()
            exports = {"RAMAN_DIR": raman_dir, "VASP_BINARY": ctx.vasp_binary}
            ok = submit_sbatch_wrapper(
                script_content,
                job_name="raman_all",
                extra_exports=exports,
                output_dir=raman_dir,
            )

    elif compute_mode == "interactive_serial":
        if ctx.inside_salloc:
            # Inside the salloc — use normal srun, don't re-allocate
            ok = True
            for dirpath in todo:
                dirname = os.path.basename(dirpath)
                print(f"    Running VASP in {dirname}...")
                try:
                    run_command(
                        f"srun {srun_args} {ctx.vasp_binary} > stdout",
                        cwd=dirpath,
                    )
                except Exception as e:
                    print(f"  [serial] VASP failed in {dirname}: {e}")
                    ok = False
        else:
            # On login node — auto-salloc + retry
            print(f"  [interactive_serial] {len(todo)} raman dirs, auto-salloc + retry…")
            ok = run_serial_in_salloc_with_retry(
                todo, build_serial_vasp_wrapper(ctx.system_paths),
                vasp_binary=ctx.vasp_binary,
                work_dir=ctx.work_dir,
                srun_args=ctx.srun_args,
                salloc_args=ctx.salloc_per_dir,
            )

    elif compute_mode == "interactive_parallel":
        raise NotImplementedError("interactive_parallel not yet implemented")

    else:  # interactive_manual (default)
        for dirpath in todo:
            dirname = os.path.basename(dirpath)
            print(f"    Running VASP in {dirname}...")
            run_command(
                f"srun {srun_args} {ctx.vasp_binary} > stdout",
                cwd=dirpath,
                check_success=False,
            )
        ok = True  # validation below will catch failures

    # ── Validation ───────────────────────────────────────────────────────────
    for dirpath in ra_dirs:
        check_vasp_convergence(dirpath, "step-7")
        check_dielectric_complete(dirpath, "step-7")

    if not ok:
        write_status(step, "failed", f"Resonant VASP incomplete ({compute_mode})")
        print_step_result(step, ok=False, duration_s=time.time() - t_start,
                          message=f"{compute_mode} failed")
        raise RuntimeError(f"Step 7 failed ({compute_mode})")
    write_status(step, "completed", f"Resonant VASP — {len(ra_dirs)} dirs ({compute_mode})")
    print_step_result(step, ok=True, duration_s=time.time() - t_start,
                      message=f"{len(ra_dirs)} dirs ({compute_mode})")
