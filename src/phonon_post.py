"""Step 5 — Phonon postprocessing."""

import os, time, glob
from util.io import run_command
from util.phonopy import ensure_dim_in_conf, write_eigenvectors_conf
from util.status import begin_step, print_step_result


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_label
    hf_dir = ctx.hffiles_dir
    bin_dir = ctx.binary_utilities_dir
    t_start = begin_step(ctx, "Phonon postprocessing")
    print("  [10a] Extracting force constants...")
    hf_dirs = sorted(glob.glob(os.path.join(hf_dir, "hf_POSCAR-*")))
    if not hf_dirs:
        write_status(step, "failed", "No hf_POSCAR-* dirs")
        raise RuntimeError("No hf_POSCAR-*")
    # Build the vasprun.xml file list explicitly instead of relying on bash
    # brace expansion ({001..NNN}/vasprun.xml) — that silently breaks if any
    # displacement directory is missing/non-contiguous (e.g. 003 failed):
    # bash expands the gap to a literal nonexistent path and phonopy fails
    # outright with "file not found," blocking this whole step.
    vasprun_files = sorted(glob.glob(os.path.join(hf_dir, "hf_POSCAR-*", "vasprun.xml")))
    if len(vasprun_files) < len(hf_dirs):
        missing = len(hf_dirs) - len(vasprun_files)
        print(f"  WARNING: {missing} hf_POSCAR-* dir(s) missing vasprun.xml — "
              f"using {len(vasprun_files)}/{len(hf_dirs)} available")
    if not vasprun_files:
        write_status(step, "failed", "No hf_POSCAR-*/vasprun.xml files found")
        raise RuntimeError("No hf_POSCAR-*/vasprun.xml files found")
    rel_files = " ".join(os.path.relpath(f, hf_dir) for f in vasprun_files)
    run_command(f"phonopy -f {rel_files}", cwd=hf_dir)
    print("  [10b] Eigenvectors + symmetry...")
    write_eigenvectors_conf(
        os.path.join(hf_dir, "eigenvectors.conf"),
        ctx.phonopy_dim,
        ctx.eigvec_band_path,
        ctx.eigvec_band_labels,
        ctx.eigvec_band_points,
    )
    run_command("phonopy -c POSCAR_unitcell eigenvectors.conf", cwd=hf_dir)
    ensure_dim_in_conf(
        os.path.join(hf_dir, "symmetry.conf"), "symmetry.conf", ctx.phonopy_dim
    )
    run_command("phonopy -c POSCAR_unitcell symmetry.conf", cwd=hf_dir)
    print("  [10c] Phonon mode visualization...")
    if ctx.viz_enabled:
        from util.visualize import generate_phonon_visuals
        generate_phonon_visuals(hf_dir, ctx)
    else:
        print("  [10c] Visualization disabled in config")
    write_status(step, "completed", "Phonon postprocessing done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)


def is_complete(work_dir, config):
    p = os.path.join(work_dir, "hf", "band.yaml")
    return os.path.exists(p) and os.path.getsize(p) > 0
