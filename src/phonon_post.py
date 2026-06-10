"""Step 10 — Phonon postprocessing."""

import os, time, glob, shutil
from util.io import run_command
from util.phonopy import ensure_dim_in_conf, write_eigenvectors_conf
from util.status import print_step_header, print_step_result


def run(ctx):
    write_status = ctx["write_status"]
    step = ctx["_step"]
    hf_dir = ctx["hffiles_dir"]
    bin_dir = ctx["binary_utilities_dir"]
    print_step_header(step)
    write_status(step, "running", "Phonon postprocessing")
    t_start = time.time()
    print("  [10a] Extracting force constants...")
    hf_dirs = sorted(glob.glob(os.path.join(hf_dir, "hf_POSCAR-*")))
    if not hf_dirs:
        write_status(step, "failed", "No hf_POSCAR-* dirs")
        raise RuntimeError("No hf_POSCAR-*")
    last_idx = os.path.basename(hf_dirs[-1]).split("-")[-1]
    run_command(f"phonopy -f hf_POSCAR-{{001..{last_idx}}}/vasprun.xml", cwd=hf_dir)
    print("  [10b] Eigenvectors + symmetry...")
    write_eigenvectors_conf(
        os.path.join(hf_dir, "eigenvectors.conf"),
        ctx["phonopy_dim"],
        ctx["eigvec_band_path"],
        ctx["eigvec_band_labels"],
        ctx["eigvec_band_points"],
    )
    run_command("phonopy -c POSCAR_unitcell eigenvectors.conf", cwd=hf_dir)
    ensure_dim_in_conf(
        os.path.join(hf_dir, "symmetry.conf"), "symmetry.conf", ctx["phonopy_dim"]
    )
    run_command("phonopy -c POSCAR_unitcell symmetry.conf", cwd=hf_dir)
    if int(ctx["phonopy_band_points"]) > 1:
        print("  [10c] Full band-path — visualization + symmetry...")
        contcar_dst = os.path.join(hf_dir, "CONTCAR")
        if not (os.path.exists(contcar_dst) and os.path.getsize(contcar_dst) > 0):
            for alt_src in (
                os.path.join(hf_dir, "relax", "CONTCAR"),
                os.path.join(hf_dir, "SPOSCAR"),
            ):
                if os.path.exists(alt_src) and os.path.getsize(alt_src) > 0:
                    shutil.copy2(alt_src, contcar_dst)
                    break
        run_command(
            f"export PATH={bin_dir}:$PATH && echo -e '1\\nno' | phonopy_visualization",
            cwd=hf_dir,
            check_success=False,
        )
        allmode = os.path.join(hf_dir, "all_mode.txt")
        if os.path.exists(allmode) and os.path.getsize(allmode) > 0:
            sym_bin = os.path.join(bin_dir, "phonopy_symmetry")
            if os.path.exists(sym_bin):
                run_command(sym_bin, cwd=hf_dir)
    else:
        print("  [10c] Gamma-only — visualization skipped")
    write_status(step, "completed", "Phonon postprocessing done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)
