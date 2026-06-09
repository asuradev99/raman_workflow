"""Step 10 — Phonon postprocessing."""

import os, time, glob, shutil
from util.io import run_command
from util.phonopy import ensure_dim_in_conf, write_eigenvectors_conf
from util.status import print_step_header, print_step_result


def run(ctx):
    ws = ctx["write_status"]
    H = ctx["hffiles_dir"]
    BU = ctx["binary_utilities_dir"]
    print_step_header(5)
    ws(5, "running", "Phonon postprocessing")
    _t0 = time.time()
    print("  [10a] Extracting force constants...")
    dirs = sorted(glob.glob(os.path.join(H, "hf_POSCAR-*")))
    if not dirs:
        ws(5, "failed", "No hf_POSCAR-* dirs")
        raise RuntimeError("No hf_POSCAR-*")
    N = os.path.basename(dirs[-1]).split("-")[-1]
    run_command(f"phonopy -f hf_POSCAR-{{001..{N}}}/vasprun.xml", cwd=H)
    print("  [10b] Eigenvectors + symmetry...")
    write_eigenvectors_conf(
        os.path.join(H, "eigenvectors.conf"),
        ctx["phonopy_dim"],
        ctx["eigvec_band_path"],
        ctx["eigvec_band_labels"],
        ctx["eigvec_band_points"],
    )
    run_command("phonopy -c POSCAR_unitcell eigenvectors.conf", cwd=H)
    ensure_dim_in_conf(
        os.path.join(H, "symmetry.conf"), "symmetry.conf", ctx["phonopy_dim"]
    )
    run_command("phonopy -c POSCAR_unitcell symmetry.conf", cwd=H)
    if int(ctx["phonopy_band_points"]) > 1:
        print("  [10c] Full band-path — visualization + symmetry...")
        c = os.path.join(H, "CONTCAR")
        if not (os.path.exists(c) and os.path.getsize(c) > 0):
            for alt in (
                os.path.join(H, "relax", "CONTCAR"),
                os.path.join(H, "SPOSCAR"),
            ):
                if os.path.exists(alt) and os.path.getsize(alt) > 0:
                    shutil.copy2(alt, c)
                    break
        run_command(
            f"export PATH={BU}:$PATH && echo -e '1\\nno' | phonopy_visualization",
            cwd=H,
            check_success=False,
        )
        a = os.path.join(H, "all_mode.txt")
        if os.path.exists(a) and os.path.getsize(a) > 0:
            ps = os.path.join(BU, "phonopy_symmetry")
            if os.path.exists(ps):
                run_command(ps, cwd=H)
    else:
        print("  [10c] Gamma-only — visualization skipped")
    ws(5, "completed", "Phonon postprocessing done")
    print_step_result(5, ok=True, duration_s=time.time() - _t0)
