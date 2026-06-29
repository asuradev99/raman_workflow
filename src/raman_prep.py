"""Step 6 — Raman directory setup + displacement generation."""
import os, time, glob
from util.io import run_command, require_file
from util.incar import write_vasp_inputs
from util.symlinks import update_raman_symlinks
from util.status import begin_step, print_step_result

def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_label
    work_dir = ctx.work_dir
    raman_dir = ctx.raman_dir
    bin_dir = ctx.binary_utilities_dir
    is_cpu = ctx.cpu_flag

    t_start = begin_step(ctx, "Raman setup + displacement generation")

    # Copy CONTCAR + VASP inputs to raman/
    run_command(f"mkdir -p {raman_dir}", cwd=work_dir)
    run_command(f"cp scf/CONTCAR {raman_dir}/CONTCAR", cwd=work_dir)

    # Seed raman/ itself with CHGCAR/WAVECAR symlinks so the Fortran binaries
    # (ramdiscar, genRApos610) can see charge/wavefunction data.
    # Relative path: raman/ is one level below work_dir, so ../scf/<file>.
    # Pristine:  scf/ holds unit-cell relaxation CHGCAR/WAVECAR.
    # Defected:  scf/ holds defect-supercell relaxation CHGCAR/WAVECAR.
    for f in ("CHGCAR", "WAVECAR"):
        src_path = os.path.join(work_dir, "scf", f)
        dst = os.path.join(raman_dir, f)
        if not os.path.exists(src_path):
            print(f"  [setup] WARNING: {f} not found in scf/ — binaries may fail")
        else:
            if os.path.islink(dst) or os.path.exists(dst):
                os.remove(dst)
            os.symlink(f"../scf/{f}", dst)

    write_vasp_inputs(raman_dir, work_dir, ctx.config, "resonant_vasp",
                      ctx.raman_kpoints_mesh, ctx.raman_kpoints_shift,
                      "K-points for resonant Raman")

    # Raman displacements
    for b in ("ramdiscar", "genRApos610", "runRA"):
        require_file(os.path.join(bin_dir, b), b)
    run_command(f"{bin_dir}/ramdiscar", cwd=raman_dir, check_success=not is_cpu)
    go_input = os.path.join(raman_dir, ".go_input")
    with open(go_input, "w") as f:
        f.write("go\n")
    run_command(f"{bin_dir}/genRApos610 < {go_input}", cwd=raman_dir, check_success=not is_cpu)
    os.remove(go_input)
    run_command(f"{bin_dir}/runRA", cwd=raman_dir)

    # Symlink CHGCAR/WAVECAR into every ra_pos_*/ dir so VASP can seed from
    # the relaxed charge density.  The dielec INCAR has LCHARG=.FALSE. and
    # LWAVE=.FALSE., so VASP reads from but never writes back through these links.
    update_raman_symlinks(raman_dir, work_dir)

    write_status(step, "completed", "Raman setup + displacements done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)


def is_complete(work_dir, config):
    return bool(glob.glob(os.path.join(work_dir, "raman", "ra_pos_*")))
