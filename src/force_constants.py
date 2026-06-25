"""Step 4 — VASP force constants (mode-dispatched)."""

import os, time
from util.compute import dispatch_vasp_runs
from util.vasp import check_no_selective_dynamics, is_calculation_complete, check_vasp_convergence
from util.vasp_loop import list_hf_dirs, run_vasp_in_dirs
from util.status import begin_step, finish_dispatch_step


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_label
    hf_dir = ctx.hffiles_dir
    compute_mode = ctx.compute_mode

    t_start = begin_step(ctx, f"VASP force constants ({compute_mode})")

    # Include groundstate only when start_from_supercell=False (step 2 ran VASP there).
    # When start_from_supercell=True, groundstate/ holds geometry only — VASP never runs.
    all_dirs = list_hf_dirs(hf_dir, include_groundstate=not ctx.start_from_supercell)
    todo = [d for d in all_dirs if not is_calculation_complete(d)]

    if not todo:
        finish_dispatch_step(ctx, True, t_start, len(all_dirs), compute_mode, "Force constants")
        return

    # Guard: selective dynamics in SPOSCAR corrupts force constants silently.
    # Check before any VASP runs — not just in manual mode.
    check_no_selective_dynamics(os.path.join(hf_dir, "SPOSCAR"), "SPOSCAR")

    def _manual_runner(todo):
        return run_vasp_in_dirs(todo, ctx.srun_args, ctx.vasp_binary,
                                max_restarts=ctx.vasp_max_restarts,
                                hf_parallel=ctx.hf_parallel,
                                cpu_flag=ctx.cpu_flag)

    ok = dispatch_vasp_runs(
        ctx, all_dirs, todo,
        job_prefix="hf",
        dir_script_name="sbatch_hf_dir.sh",
        all_script_name="sbatch_all_hf.sh",
        env_dir_key="HF_DIR", env_dir_value=hf_dir,
        all_job_name="hf_all",
        manual_runner=_manual_runner,
        mix_log_name="relaxation.stdout",
    )

    # ── Validation ───────────────────────────────────────────────────────────
    # Defense-in-depth: a preempted+requeued job can briefly vanish from squeue
    # and be mistaken for "done." Re-check real VASP convergence for all modes.
    for dirpath in all_dirs:
        check_vasp_convergence(dirpath, "step-4")

    finish_dispatch_step(ctx, ok, t_start, len(all_dirs), compute_mode, "Force constants")


def is_complete(work_dir, config):
    p = os.path.join(work_dir, "hf", "FORCE_SETS")
    return os.path.exists(p) and os.path.getsize(p) > 0
