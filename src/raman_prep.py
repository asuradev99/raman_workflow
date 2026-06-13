"""Step 6 — Raman directory setup + displacement generation."""
import os, time, glob
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.status import print_step_header, print_step_result

def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_step
    work_dir = ctx.work_dir
    raman_dir = ctx.raman_dir
    bin_dir = ctx.binary_utilities_dir
    is_cpu = ctx.cpu_flag

    print_step_header(step)
    write_status(step, "running", "Raman setup + displacement generation")
    t_start = time.time()

    # Copy CONTCAR + VASP inputs to raman/
    run_command(f"mkdir -p {raman_dir}", cwd=work_dir)
    run_command(f"cp scf/CONTCAR {raman_dir}/CONTCAR", cwd=work_dir)
    for f in ("CHGCAR", "WAVECAR"):
        src = os.path.join(work_dir, "scf", f)
        dst = os.path.join(raman_dir, f)
        if not os.path.exists(src):
            print(f"  [setup] WARNING: {f} not found in scf/")
        elif not os.path.exists(dst) and not os.path.islink(dst):
            os.symlink(src, dst)
    write_incar(os.path.join(raman_dir, "INCAR"), ctx.config, "dielec")
    write_kpoints(os.path.join(raman_dir, "KPOINTS"), "K-points for resonant Raman",
                  ctx.raman_kpoints_mesh, ctx.raman_kpoints_shift)
    run_command(f"cp input/POTCAR {raman_dir}/", cwd=work_dir)

    # Raman displacements
    for b in ("ramdiscar", "genRApos610", "runRA"):
        p = os.path.join(bin_dir, b)
        if not os.path.exists(p):
            raise FileNotFoundError(f"{b} not found at {p}")
    run_command(f"{bin_dir}/ramdiscar", cwd=raman_dir, check_success=not is_cpu)
    go_input = os.path.join(raman_dir, ".go_input")
    with open(go_input, "w") as f:
        f.write("go\n")
    run_command(f"{bin_dir}/genRApos610 < {go_input}", cwd=raman_dir, check_success=not is_cpu)
    os.remove(go_input)
    run_command(f"{bin_dir}/runRA", cwd=raman_dir)

    # Propagate CHGCAR/WAVECAR symlinks into ra_pos_* dirs
    chgcar_src = os.path.join(work_dir, "scf", "CHGCAR")
    wavecar_src = os.path.join(work_dir, "scf", "WAVECAR")
    symlink_count = 0
    for d in sorted(glob.glob(os.path.join(raman_dir, "ra_pos_*"))):
        for fn, sp in [("CHGCAR", chgcar_src), ("WAVECAR", wavecar_src)]:
            dst = os.path.join(d, fn)
            if os.path.exists(sp) and not os.path.exists(dst):
                os.symlink(sp, dst)
                symlink_count += 1
    num_dirs = len(glob.glob(os.path.join(raman_dir, "ra_pos_*")))
    if symlink_count:
        print(f"  [setup] Created {symlink_count} symlinks across {num_dirs} ra_pos_* dirs")

    write_status(step, "completed", "Raman setup + displacements done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)
