"""Step 3 — Initial VASP relaxation."""

import os, time
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.status import print_step_header, print_step_result


def run(ctx):
    ws = ctx["write_status"]
    print_step_header(1)
    ws(1, "running", "Initial VASP relaxation")
    _t0 = time.time()

    scf_dir = os.path.join(ctx["work_dir"], "scf")
    run_command(f"mkdir -p {scf_dir}", cwd=ctx["work_dir"])

    input_dir = os.path.join(ctx["material_dir"], "input")
    for vasp_input in ("POSCAR", "POTCAR"):
        src = os.path.join(input_dir, vasp_input)
        if not os.path.exists(src):
            raise FileNotFoundError(f"input/{vasp_input} not found at {src}.")
        run_command(f"cp input/{vasp_input} scf/{vasp_input}", cwd=ctx["work_dir"])
    write_kpoints(
        os.path.join(scf_dir, "KPOINTS"),
        "K-points for unit cell SCF",
        ctx["scf_kpoints_mesh"],
        ctx["scf_kpoints_shift"],
    )
    print(f"  [setup] Wrote unit cell KPOINTS ({ctx['scf_kpoints_mesh']}) to scf/")
    write_incar(os.path.join(ctx["work_dir"], "scf", "INCAR"), ctx["config"], "relax")

    if not os.path.exists(os.path.join(scf_dir, "POTCAR")):
        raise FileNotFoundError(f"POTCAR not found in {scf_dir}.")

    if not ctx["run_relaxation"](
        scf_dir, ctx["srun_args"], ctx["vasp_binary"], stage_label="step-1"
    ):
        msg = f"VASP relaxation failed after max retries in {scf_dir}. Check {scf_dir}/relaxation.stdout."
        print_step_result(
            1, ok=False, duration_s=time.time() - _t0, message="Relaxation failed"
        )
        raise RuntimeError(msg)

    ws(1, "completed", "Initial VASP relaxation finished")
    print_step_result(1, ok=True, duration_s=time.time() - _t0)
