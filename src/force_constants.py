"""Step 9 — VASP force constants."""

import os, time
from util.vasp import check_no_selective_dynamics
from util.status import print_step_header, print_step_result


def run(ctx):
    ws = ctx["write_status"]
    H = ctx["hffiles_dir"]
    SD = ctx["script_dir"]
    BU = ctx["binary_utilities_dir"]
    print_step_header(4)
    ws(4, "running", "VASP force constants")
    _t0 = time.time()
    s = os.path.join(SD, "scripts", "automate_hfiles_fixed.sh")
    if not os.path.exists(s):
        s = os.path.join(BU, "automate_hfiles.sh")
    if not os.path.exists(s):
        raise FileNotFoundError(f"automate_hfiles not found")
    check_no_selective_dynamics(os.path.join(H, "SPOSCAR"), "SPOSCAR")
    ok = ctx["vasp_loop_fn"](s, max_restarts=ctx["vasp_max_restarts"])
    if not ok:
        ws(4, "failed", "VASP force-constant runs incomplete")
        print_step_result(
            4,
            ok=False,
            duration_s=time.time() - _t0,
            message="Force-constant VASP runs incomplete",
        )
        raise RuntimeError(f"Step 9 failed after {ctx['vasp_max_restarts']} attempts")
    ws(4, "completed", "VASP force-constant runs finished")
    print_step_result(4, ok=True, duration_s=time.time() - _t0)
