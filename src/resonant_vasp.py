"""Step 7 — Resonant VASP runs in all ra_pos_* directories."""
import os, time, glob
from util.io import run_command
from util.vasp import check_vasp_convergence, check_dielectric_complete, check_no_selective_dynamics, is_calculation_complete
from util.status import print_step_header, print_step_result

def run(ctx):
    write_status = ctx["write_status"]
    step = ctx["_step"]
    raman_dir = ctx["raman_dir"]
    script_dir = ctx["script_dir"]
    srun_args = ctx["srun_args"]

    print_step_header(step)
    write_status(step, "running", "Resonant VASP runs")
    t_start = time.time()

    ra_dirs = sorted(glob.glob(os.path.join(raman_dir, "ra_pos_*")))
    if not ra_dirs:
        raise RuntimeError("No ra_pos_* directories found")

    if ra_dirs:
        check_no_selective_dynamics(os.path.join(ra_dirs[0], "POSCAR"), "ra_pos_* POSCAR")

    # Skip already-completed directories (from a previous crashed run)
    completed = [d for d in ra_dirs if is_calculation_complete(d)]
    todo = [d for d in ra_dirs if d not in completed]
    if completed:
        print(f"  [resume] Skipping {len(completed)} completed ra_pos_* dirs, "
              f"running {len(todo)} remaining")
    if not todo:
        print("  All ra_pos_* directories already complete.")
        write_status(step, "completed", f"Resonant VASP — {len(ra_dirs)} dirs (all previously completed)")
        print_step_result(step, ok=True, duration_s=time.time() - t_start,
                          message=f"{len(ra_dirs)} directories (all cached)")
        return

    # Run VASP in incomplete directories only
    for dirpath in todo:
        dirname = os.path.basename(dirpath)
        print(f"    Running VASP in {dirname}...")
        run_command(
            f"srun {srun_args} {ctx['vasp_binary']} > stdout",
            cwd=dirpath,
            check_success=False,
        )

    for dirpath in ra_dirs:
        check_vasp_convergence(dirpath, "step-7")
        check_dielectric_complete(dirpath, "step-7")

    write_status(step, "completed", f"Resonant VASP — {len(ra_dirs)} dirs validated")
    print_step_result(step, ok=True, duration_s=time.time() - t_start,
                      message=f"{len(ra_dirs)} directories")
