"""Step 9 — VASP force constants."""

import os, time
from util.vasp import check_no_selective_dynamics
from util.status import print_step_header, print_step_result


def run(ctx):
    write_status = ctx["write_status"]
    step = ctx["_step"]
    hf_dir = ctx["hffiles_dir"]
    script_dir = ctx["script_dir"]
    bin_dir = ctx["binary_utilities_dir"]
    print_step_header(step)
    write_status(step, "running", "VASP force constants")
    t_start = time.time()
    vasp_script = os.path.join(script_dir, "scripts", "automate_hfiles_fixed.sh")
    if not os.path.exists(vasp_script):
        vasp_script = os.path.join(bin_dir, "automate_hfiles.sh")
    if not os.path.exists(vasp_script):
        raise FileNotFoundError(f"automate_hfiles not found")
    check_no_selective_dynamics(os.path.join(hf_dir, "SPOSCAR"), "SPOSCAR")
    ok = ctx["vasp_loop_fn"](vasp_script, max_restarts=ctx["vasp_max_restarts"])
    if not ok:
        write_status(step, "failed", "VASP force-constant runs incomplete")
        print_step_result(
            step,
            ok=False,
            duration_s=time.time() - t_start,
            message="Force-constant VASP runs incomplete",
        )
        raise RuntimeError(f"Step 9 failed after {ctx['vasp_max_restarts']} attempts")
    write_status(step, "completed", "VASP force-constant runs finished")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)
