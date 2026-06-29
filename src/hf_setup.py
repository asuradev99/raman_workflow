"""Step 3 — hf/ directory setup (copy, verify, runHF, symlinks)."""
import os, time, glob
from util.io import run_command, require_file
from util.incar import write_vasp_inputs
from util.phonopy import ensure_dim_in_conf
from util.symlinks import update_hf_symlinks
from util.status import begin_step, print_step_result


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_label
    hf_dir = ctx.hffiles_dir
    work_dir = ctx.work_dir
    bin_dir = ctx.binary_utilities_dir

    t_start = begin_step(ctx, "hf/ directory setup")
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

    # ── Find relaxed structure from whichever step last ran ──────────────────
    # Prefer scf2/ (after defect_relax_2_cpu/defect_relax_2), fall back to scf/.
    relax_subdir = "scf"
    for candidate in ("scf2", "scf"):
        p = os.path.join(work_dir, candidate, "CONTCAR")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            relax_subdir = candidate
            break
    print(f"  [hf_setup] Using relaxed structure from {relax_subdir}/CONTCAR")

    # Copy POSCAR_unitcell; write INCAR, KPOINTS, POTCAR; symmetry.conf
    run_command(f"cp {relax_subdir}/CONTCAR {hf_dir}/POSCAR_unitcell", cwd=work_dir)
    write_vasp_inputs(hf_dir, work_dir, ctx.config, "force_consts",
                      ctx.hf_kpoints_mesh, ctx.hf_kpoints_shift,
                      "K-points for force-constant")
    ensure_dim_in_conf(os.path.join(hf_dir, "symmetry.conf"), "symmetry.conf", ctx.phonopy_dim)

    # ── phonopy -d: generate SPOSCAR + displacement POSCARs if missing ───────
    # Normally done by the supercell step; run here when supercell is omitted.
    if not os.path.exists(os.path.join(hf_dir, "SPOSCAR")):
        print("  [hf_setup] SPOSCAR not found — running phonopy -d")
        run_command(
            f'phonopy -d --dim="{ctx.phonopy_dim}" --amplitude={ctx.phonopy_amplitude}'
            f" -c POSCAR_unitcell",
            cwd=hf_dir,
        )
    require_file(os.path.join(hf_dir, "SPOSCAR"), "SPOSCAR")
    require_file(os.path.join(bin_dir, "runHF"), "runHF")
    run_command(os.path.join(bin_dir, "runHF"), cwd=hf_dir)

    # WAVECAR + CHGCAR symlinks in displacement dirs
    gs_dir = os.path.join(hf_dir, "groundstate")
    if ctx.start_from_supercell:
        # groundstate/ geometry reference only — VASP doesn't run here.
        # Add CHGCAR/WAVECAR symlinks so groundstate/ is valid if ever run manually.
        if not os.path.isdir(gs_dir):
            run_command(f"mkdir -p {gs_dir}", cwd=work_dir)
        for fname in ("CHGCAR", "WAVECAR"):
            link = os.path.join(gs_dir, fname)
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            os.symlink(f"../../{relax_subdir}/{fname}", link)
        src_subdir = relax_subdir
    else:
        src_subdir = "groundstate"
    update_hf_symlinks(hf_dir, source_subdir=src_subdir)

    write_status(step, "completed", "hf/ directory setup done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)


def is_complete(work_dir, config):
    return bool(glob.glob(os.path.join(work_dir, "hf", "hf_POSCAR-*")))
