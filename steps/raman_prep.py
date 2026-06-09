"""Step 6 — Raman directory setup + displacement generation."""
import os, time, glob
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.status import print_step_header, print_step_result

def run(ctx):
    ws = ctx["write_status"]
    W = ctx["work_dir"]
    R = ctx["raman_dir"]
    BU = ctx["binary_utilities_dir"]
    CF = ctx["cpu_flag"]

    print_step_header(6)
    ws(6, "running", "Raman setup + displacement generation")
    _t0 = time.time()

    # Copy CONTCAR + VASP inputs to raman/
    run_command(f"mkdir -p {R}", cwd=W)
    run_command(f"cp scf/CONTCAR {R}/CONTCAR", cwd=W)
    for f in ("CHGCAR", "WAVECAR"):
        src = os.path.join(W, "scf", f)
        dst = os.path.join(R, f)
        if os.path.exists(src):
            os.symlink(src, dst)
        else:
            print(f"  [setup] WARNING: {f} not found in scf/")
    write_incar(os.path.join(R, "INCAR"), ctx["config"], "dielec")
    write_kpoints(os.path.join(R, "KPOINTS"), "K-points for resonant Raman",
                  ctx["raman_kpoints_mesh"], ctx["raman_kpoints_shift"])
    run_command(f"cp input/POTCAR {R}/", cwd=W)

    # Navigate to raman/
    os.chdir(R)

    # Raman displacements
    for b in ("ramdiscar", "genRApos610", "runRA"):
        p = os.path.join(BU, b)
        if not os.path.exists(p):
            raise FileNotFoundError(f"{b} not found at {p}")
    run_command(f"{BU}/ramdiscar", check_success=not CF)
    gf = os.path.join(R, ".go_input")
    with open(gf, "w") as f:
        f.write("go\n")
    run_command(f"{BU}/genRApos610 < {gf}", check_success=not CF)
    os.remove(gf)
    run_command(f"{BU}/runRA")

    # Propagate CHGCAR/WAVECAR symlinks into ra_pos_* dirs
    cs = os.path.join(W, "scf", "CHGCAR")
    ws2 = os.path.join(W, "scf", "WAVECAR")
    cnt = 0
    for d in sorted(glob.glob(os.path.join(R, "ra_pos_*"))):
        for fn, sp in [("CHGCAR", cs), ("WAVECAR", ws2)]:
            dst = os.path.join(d, fn)
            if os.path.exists(sp) and not os.path.exists(dst):
                os.symlink(sp, dst)
                cnt += 1
    n = len(glob.glob(os.path.join(R, "ra_pos_*")))
    if cnt:
        print(f"  [setup] Created {cnt} symlinks across {n} ra_pos_* dirs")

    ws(6, "completed", "Raman setup + displacements done")
    print_step_result(6, ok=True, duration_s=time.time() - _t0)
