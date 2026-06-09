"""Step 3 — hf/ directory setup (copy, verify, runHF, symlinks)."""
import os, time
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.phonopy import ensure_dim_in_conf
from util.symlinks import update_wavecar_symlinks, update_chgcar_symlinks
from util.status import print_step_header, print_step_result


def run(ctx):
    ws = ctx["write_status"]
    H = ctx["hffiles_dir"]
    W = ctx["work_dir"]
    BU = ctx["binary_utilities_dir"]

    print_step_header(3)
    ws(3, "running", "hf/ directory setup")
    _t0 = time.time()

    # Copy POSCAR, INCAR, KPOINTS, POTCAR to hf/
    run_command(f"mkdir -p {H}", cwd=W)
    run_command(f"cp scf/CONTCAR {H}/POSCAR_unitcell", cwd=W)
    write_incar(os.path.join(H, "INCAR"), ctx["config"], "hf")
    write_kpoints(os.path.join(H, "KPOINTS"), "K-points for force-constant",
                  ctx["hf_kpoints_mesh"], ctx["hf_kpoints_shift"])
    run_command(f"cp input/POTCAR {H}/", cwd=W)
    ensure_dim_in_conf(os.path.join(H, "symmetry.conf"), "symmetry.conf", ctx["phonopy_dim"])

    # Verify SPOSCAR exists
    sp = os.path.join(H, "SPOSCAR")
    if not os.path.exists(sp):
        raise FileNotFoundError(f"SPOSCAR not found in {H}")

    # runHF folder organization
    s = os.path.join(BU, "runHF")
    if not os.path.exists(s):
        raise FileNotFoundError(f"runHF not found at {s}")
    run_command(s, cwd=H)

    # WAVECAR + CHGCAR symlinks in displacement dirs
    gs = os.path.join(H, "groundstate")
    if not os.path.isdir(gs):
        run_command(f"mkdir -p {gs}", cwd=W)
    run_command(f"cp INCAR {gs}/", cwd=H)
    run_command(f"cp KPOINTS {gs}/", cwd=H)
    run_command(f"cp POSCAR_unitcell {gs}/POSCAR", cwd=H)
    if ctx["start_from_supercell"]:
        src = "scf"
    else:
        src = "groundstate"
    update_wavecar_symlinks(H, source_subdir=src)
    update_chgcar_symlinks(H, source_subdir=src)

    ws(3, "completed", "hf/ directory setup done")
    print_step_result(3, ok=True, duration_s=time.time() - _t0)
