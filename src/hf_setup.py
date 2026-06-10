"""Step 3 — hf/ directory setup (copy, verify, runHF, symlinks)."""
import os, time
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.phonopy import ensure_dim_in_conf
from util.symlinks import update_wavecar_symlinks, update_chgcar_symlinks
from util.status import print_step_header, print_step_result


def run(ctx):
    write_status = ctx["write_status"]
    step = ctx["_step"]
    hf_dir = ctx["hffiles_dir"]
    work_dir = ctx["work_dir"]
    bin_dir = ctx["binary_utilities_dir"]

    print_step_header(step)
    write_status(step, "running", "hf/ directory setup")
    t_start = time.time()

    # Copy POSCAR, INCAR, KPOINTS, POTCAR to hf/
    run_command(f"mkdir -p {hf_dir}", cwd=work_dir)
    run_command(f"cp scf/CONTCAR {hf_dir}/POSCAR_unitcell", cwd=work_dir)
    write_incar(os.path.join(hf_dir, "INCAR"), ctx["config"], "hf")
    write_kpoints(os.path.join(hf_dir, "KPOINTS"), "K-points for force-constant",
                  ctx["hf_kpoints_mesh"], ctx["hf_kpoints_shift"])
    run_command(f"cp input/POTCAR {hf_dir}/", cwd=work_dir)
    ensure_dim_in_conf(os.path.join(hf_dir, "symmetry.conf"), "symmetry.conf", ctx["phonopy_dim"])

    # Verify SPOSCAR exists
    spos_path = os.path.join(hf_dir, "SPOSCAR")
    if not os.path.exists(spos_path):
        raise FileNotFoundError(f"SPOSCAR not found in {hf_dir}")

    # runHF folder organization
    runhf_path = os.path.join(bin_dir, "runHF")
    if not os.path.exists(runhf_path):
        raise FileNotFoundError(f"runHF not found at {runhf_path}")
    run_command(runhf_path, cwd=hf_dir)

    # WAVECAR + CHGCAR symlinks in displacement dirs
    gs_dir = os.path.join(hf_dir, "groundstate")
    if ctx["start_from_supercell"]:
        # groundstate/ not set up by Step 2 — populate it now so VASP can run
        if not os.path.isdir(gs_dir):
            run_command(f"mkdir -p {gs_dir}", cwd=work_dir)
        run_command(f"cp INCAR {gs_dir}/", cwd=hf_dir)
        run_command(f"cp KPOINTS {gs_dir}/", cwd=hf_dir)
        run_command(f"cp POSCAR_unitcell {gs_dir}/POSCAR", cwd=hf_dir)
        src_subdir = "scf"
    else:
        # Step 2 already ran VASP in groundstate/ with the correct supercell POSCAR
        src_subdir = "groundstate"
    update_wavecar_symlinks(hf_dir, source_subdir=src_subdir)
    update_chgcar_symlinks(hf_dir, source_subdir=src_subdir)

    write_status(step, "completed", "hf/ directory setup done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)
