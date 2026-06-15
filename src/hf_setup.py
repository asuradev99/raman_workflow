"""Step 3 — hf/ directory setup (copy, verify, runHF, symlinks)."""
import os, shutil, time
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.phonopy import ensure_dim_in_conf
from util.symlinks import update_hf_symlinks
from util.status import print_step_header, print_step_result


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_step
    hf_dir = ctx.hffiles_dir
    work_dir = ctx.work_dir
    bin_dir = ctx.binary_utilities_dir

    print_step_header(step)
    write_status(step, "running", "hf/ directory setup")
    t_start = time.time()

    run_command(f"mkdir -p {hf_dir}", cwd=work_dir)

    # ── Caching guard: skip if hf_POSCAR-* dirs already exist ─────────────
    existing_hf_dirs = [
        d for d in os.listdir(hf_dir)
        if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(hf_dir, d))
    ]
    if existing_hf_dirs:
        write_status(step, "completed",
                     f"hf/ setup already done ({len(existing_hf_dirs)} dirs, cached)")
        print_step_result(step, ok=True, duration_s=time.time() - t_start,
                          message=f"{len(existing_hf_dirs)} hf dirs exist (cached)")
        return

    # Copy POSCAR, INCAR, KPOINTS, POTCAR to hf/
    run_command(f"cp scf/CONTCAR {hf_dir}/POSCAR_unitcell", cwd=work_dir)
    write_incar(os.path.join(hf_dir, "INCAR"), ctx.config, "hf")
    write_kpoints(os.path.join(hf_dir, "KPOINTS"), "K-points for force-constant",
                  ctx.hf_kpoints_mesh, ctx.hf_kpoints_shift)
    run_command(f"cp input/POTCAR {hf_dir}/", cwd=work_dir)
    ensure_dim_in_conf(os.path.join(hf_dir, "symmetry.conf"), "symmetry.conf", ctx.phonopy_dim)

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
    if ctx.start_from_supercell:
        # groundstate/ geometry reference only — VASP doesn't run here.
        # POSCAR is already set by supercell.py (cp SPOSCAR groundstate/POSCAR).
        # Add CHGCAR/WAVECAR symlinks so groundstate/ is valid if ever run manually.
        if not os.path.isdir(gs_dir):
            run_command(f"mkdir -p {gs_dir}", cwd=work_dir)
        for fname in ("CHGCAR", "WAVECAR"):
            link = os.path.join(gs_dir, fname)
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            os.symlink(f"../../scf/{fname}", link)
        src_subdir = "scf"
    else:
        src_subdir = "groundstate"
    update_hf_symlinks(hf_dir, source_subdir=src_subdir)

    write_status(step, "completed", "hf/ directory setup done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)
