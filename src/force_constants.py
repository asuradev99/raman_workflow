"""Step 4 — VASP force constants (mode-dispatched)."""

import os, time
from util.compute import submit_many, submit_sbatch_wrapper, run_serial_in_salloc_with_retry, build_serial_vasp_wrapper
from util.vasp import check_no_selective_dynamics, is_calculation_complete
from util.vasp_loop import list_hf_dirs
from util.status import print_step_header, print_step_result


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_step
    hf_dir = ctx.hffiles_dir
    script_dir = ctx.script_dir
    scripts_root = os.path.join(os.path.dirname(script_dir), "scripts")
    compute_mode = ctx.compute_mode

    print_step_header(step)
    write_status(step, "running", f"VASP force constants ({compute_mode})")
    t_start = time.time()

    all_dirs = list_hf_dirs(hf_dir, include_groundstate=True)
    todo = [d for d in all_dirs if not is_calculation_complete(d)]

    if not todo:
        write_status(step, "completed", f"Force constants — {len(all_dirs)} dirs (cached)")
        print_step_result(step, ok=True, duration_s=time.time() - t_start,
                          message=f"{len(all_dirs)} dirs (all cached)")
        return

    # ══════════════════════════════════════════════════════════════════════════
    #  MODE DISPATCH
    # ══════════════════════════════════════════════════════════════════════════

    if compute_mode in ("sbatch_parallel", "sbatch"):
        print(f"  [sbatch_parallel] Submitting {len(todo)}/{len(all_dirs)} hf dirs…")
        ok = submit_many(
            os.path.join(scripts_root, "sbatch_hf_dir.sh"),
            todo, job_name_prefix="hf",
            system_paths=ctx.system_paths,
        )

    elif compute_mode == "sbatch_serial":
        if ctx.inside_salloc:
            # Inside the sbatch allocation — use normal srun
            vasp_script = os.path.join(scripts_root, "automate_hfiles_fixed.sh")
            if not os.path.exists(vasp_script):
                vasp_script = os.path.join(ctx.binary_utilities_dir, "automate_hfiles.sh")
            if not os.path.exists(vasp_script):
                raise FileNotFoundError("automate_hfiles not found")
            check_no_selective_dynamics(os.path.join(hf_dir, "SPOSCAR"), "SPOSCAR")
            ok = ctx.vasp_loop_fn(vasp_script, max_restarts=ctx.vasp_max_restarts)
        else:
            print(f"  [sbatch_serial] Submitting 1 job for {len(todo)} hf dirs…")
            script_path = os.path.join(scripts_root, "sbatch_all_hf.sh")
            with open(script_path) as _f:
                script_content = _f.read()
            exports = {"HF_DIR": hf_dir, "VASP_BINARY": ctx.vasp_binary}
            ok = submit_sbatch_wrapper(
                script_content,
                job_name="hf_all",
                extra_exports=exports,
                output_dir=hf_dir,
            )

    elif compute_mode == "interactive_serial":
        if ctx.inside_salloc:
            # Inside the salloc — use normal srun, don't re-allocate
            vasp_script = os.path.join(scripts_root, "automate_hfiles_fixed.sh")
            if not os.path.exists(vasp_script):
                vasp_script = os.path.join(ctx.binary_utilities_dir, "automate_hfiles.sh")
            if not os.path.exists(vasp_script):
                raise FileNotFoundError("automate_hfiles not found")
            check_no_selective_dynamics(os.path.join(hf_dir, "SPOSCAR"), "SPOSCAR")
            ok = ctx.vasp_loop_fn(vasp_script, max_restarts=ctx.vasp_max_restarts)
        else:
            # On login node — auto-salloc + retry
            print(f"  [interactive_serial] {len(todo)} hf dirs, auto-salloc + retry…")
            ok = run_serial_in_salloc_with_retry(
                todo, build_serial_vasp_wrapper(ctx.system_paths),
                vasp_binary=ctx.vasp_binary,
                work_dir=ctx.work_dir,
                srun_args=ctx.vasp_srun_per_dir,
                salloc_args=ctx.salloc_per_dir,
            )

    elif compute_mode == "interactive_parallel":
        raise NotImplementedError("interactive_parallel (hf_parallel) not yet implemented")

    else:  # interactive_manual (default)
        vasp_script = os.path.join(scripts_root, "automate_hfiles_fixed.sh")
        if not os.path.exists(vasp_script):
            vasp_script = os.path.join(ctx.binary_utilities_dir, "automate_hfiles.sh")
        if not os.path.exists(vasp_script):
            raise FileNotFoundError("automate_hfiles not found")
        check_no_selective_dynamics(os.path.join(hf_dir, "SPOSCAR"), "SPOSCAR")
        ok = ctx.vasp_loop_fn(vasp_script, max_restarts=ctx.vasp_max_restarts)

    # ── Result ───────────────────────────────────────────────────────────────
    if not ok:
        write_status(step, "failed", f"Force-constant runs incomplete ({compute_mode})")
        print_step_result(step, ok=False, duration_s=time.time() - t_start,
                          message=f"{compute_mode} failed")
        raise RuntimeError(f"Step 4 failed ({compute_mode})")
    write_status(step, "completed", f"Force constants — {len(all_dirs)} dirs ({compute_mode})")
    print_step_result(step, ok=True, duration_s=time.time() - t_start,
                      message=f"{len(all_dirs)} dirs ({compute_mode})")
