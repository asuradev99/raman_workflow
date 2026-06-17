"""Step 7 — Resonant VASP runs in all ra_pos_* directories (mode-dispatched)."""
import os, glob
from util.compute import dispatch_vasp_runs
from util.vasp import check_vasp_convergence, check_dielectric_complete, check_no_selective_dynamics, is_calculation_complete
from util.vasp_loop import run_vasp_in_dirs
from util.status import begin_step, finish_dispatch_step


def run(ctx):
    raman_dir = ctx.raman_dir
    compute_mode = ctx.compute_mode

    t_start = begin_step(ctx, f"Resonant VASP runs ({compute_mode})")

    ra_dirs = sorted(glob.glob(os.path.join(raman_dir, "ra_pos_*")))
    if not ra_dirs:
        raise RuntimeError("No ra_pos_* directories found")
    check_no_selective_dynamics(os.path.join(ra_dirs[0], "POSCAR"), "ra_pos_* POSCAR")

    todo = [d for d in ra_dirs if not is_calculation_complete(d)]
    if not todo:
        finish_dispatch_step(ctx, True, t_start, len(ra_dirs), compute_mode, "Resonant VASP")
        return

    def _manual_runner(todo):
        return run_vasp_in_dirs(todo, ctx.srun_args, ctx.vasp_binary,
                                max_restarts=ctx.vasp_max_restarts,
                                cpu_flag=ctx.cpu_flag,
                                log_name="stdout")

    ok = dispatch_vasp_runs(
        ctx, ra_dirs, todo,
        job_prefix="raman",
        dir_script_name="sbatch_raman_dir.sh",
        all_script_name="sbatch_all_raman.sh",
        env_dir_key="RAMAN_DIR", env_dir_value=raman_dir,
        all_job_name="raman_all",
        manual_runner=_manual_runner,
        mix_log_name="stdout",
    )

    # ── Validation ───────────────────────────────────────────────────────────
    for dirpath in ra_dirs:
        check_vasp_convergence(dirpath, "step-7")
        check_dielectric_complete(dirpath, "step-7")

    finish_dispatch_step(ctx, ok, t_start, len(ra_dirs), compute_mode, "Resonant VASP")
