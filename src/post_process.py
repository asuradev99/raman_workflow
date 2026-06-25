"""Step 8 — Post-processing (kopia, RAMFILE, energy loop, SpectroPy, output)."""
import os, time, glob, shutil
from util.io import run_command, require_file
from util.postproc import generate_kopia_script, inject_ramfile_energies
from util.status import begin_step, print_step_result

def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_label
    raman_dir = ctx.raman_dir
    bin_dir = ctx.binary_utilities_dir
    is_cpu = ctx.cpu_flag
    energies = ctx.desired_energies
    work_dir = ctx.work_dir
    material_dir = ctx.material_dir
    use_scratch = ctx.scratch_flag
    script_dir = ctx.script_dir
    config = ctx.config
    hf_dir = ctx.hffiles_dir

    t_start = begin_step(ctx, "Post-processing")

    # ── Kopia ─────────────────────────────────────────────────────────
    ra_dirs = sorted(glob.glob(os.path.join(raman_dir, "ra_pos_*")))
    if not ra_dirs:
        raise RuntimeError("No ra_pos_* directories found")
    generate_kopia_script(raman_dir, ra_dirs)
    axml_dir = os.path.join(raman_dir, "AXML")
    if not os.path.isdir(axml_dir):
        raise RuntimeError("AXML/ not created")
    xml_files = [f for f in os.listdir(axml_dir) if f.endswith(".xml")]
    if not xml_files:
        raise RuntimeError("AXML/ empty")
    empty_xml = [f for f in xml_files if os.path.getsize(os.path.join(axml_dir, f)) == 0]
    if empty_xml:
        raise RuntimeError(f"{len(empty_xml)} empty XML files")
    print(f"  [verify] AXML/ contains {len(xml_files)} valid XML files")

    # ── RAMFILE ───────────────────────────────────────────────────────
    ramfile_src = os.path.join(bin_dir, "ramfile_dynamic.sh")
    if not os.path.exists(ramfile_src):
        raise RuntimeError(f"ramfile_dynamic.sh not found at {ramfile_src}")
    store_ramfile = os.path.join(raman_dir, "store_ramfile")
    store_epsilon = os.path.join(raman_dir, "store_epsilon")
    os.makedirs(store_ramfile, exist_ok=True)
    os.makedirs(store_epsilon, exist_ok=True)
    ramfile_dst = os.path.join(raman_dir, "ramfile_dynamic.sh")
    inject_ramfile_energies(ramfile_src, ramfile_dst, energies)
    run_command(f"export PATH={bin_dir}:$PATH && bash ramfile_dynamic.sh", cwd=raman_dir)
    for e in energies:
        if not os.path.exists(os.path.join(store_ramfile, f"RAMFILE_{e}")):
            raise RuntimeError(f"RAMFILE_{e} not produced")

    # ── Static copy to output/ ────────────────────────────────────────
    for yaml_name in ("band.yaml", "irreps.yaml"):
        src = os.path.join(hf_dir, yaml_name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(raman_dir, yaml_name))
    output_dir = os.path.join(work_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    for filename in ("band.yaml", "irreps.yaml", "POSCAR_unitcell", "SPOSCAR",
                     "FORCE_SETS", "phonopy.yaml", "CONTCAR",
                     "eigenvectors.conf", "symmetry.conf"):
        src_path = os.path.join(hf_dir, filename)
        if os.path.exists(src_path) and os.path.getsize(src_path) > 0:
            shutil.copy2(src_path, output_dir)
    for mode_file in [os.path.join(hf_dir, "all_mode.txt")] + glob.glob(os.path.join(hf_dir, "mode*")):
        if os.path.exists(mode_file) and os.path.getsize(mode_file) > 0:
            shutil.copy2(mode_file, output_dir)
    incar_dir = os.path.join(output_dir, "incar")
    os.makedirs(incar_dir, exist_ok=True)
    incar_pairs = [
        (os.path.join(work_dir, "scf", "INCAR"), "relax.incar"),
        (os.path.join(hf_dir, "groundstate", "INCAR"), "supercell_relax.incar"),
        (os.path.join(hf_dir, "INCAR"), "hf_force_constants.incar"),
        (os.path.join(raman_dir, "INCAR"), "dielec_raman.incar"),
    ]
    for src_path, dest_name in incar_pairs:
        if os.path.exists(src_path) and os.path.getsize(src_path) > 0:
            shutil.copy2(src_path, os.path.join(incar_dir, dest_name))

    # ── Energy loop (raman_tensor + broadening) ───────────────────────
    for binary in ("raman_tensor", "broadening"):
        require_file(os.path.join(bin_dir, binary), binary)
    for e in energies:
        if not os.path.exists(os.path.join(store_ramfile, f"RAMFILE_{e}")):
            raise RuntimeError(f"RAMFILE_{e} not found")
    for e in energies:
        print(f"\n  [energy] Processing {e} eV —")
        run_command("rm -f RAMFILE", cwd=raman_dir, check_success=False)
        run_command(f"cp store_ramfile/RAMFILE_{e} RAMFILE", cwd=raman_dir)
        raman_input = f"{ctx.raman_incident_pol}\n{ctx.raman_scattered_pol}\n{ctx.raman_surface_normal}\n"
        input_file = os.path.join(raman_dir, ".raman_tensor_input")
        with open(input_file, "w") as f:
            f.write(raman_input)
        run_command(f"{bin_dir}/raman_tensor < .raman_tensor_input > /dev/null",
                    cwd=raman_dir, check_success=not is_cpu)
        os.remove(input_file)
        broadening_cfg = config.get("broadening", {})
        broadening_file = os.path.join(raman_dir, "broadening_input")
        b_content = (
            f"Raman_intensity_complex  !!! the file name\n"
            f"{int(broadening_cfg.get('mode', 2))}            !!! peak broadening mode\n"
            f"{int(broadening_cfg.get('hwhm', 1))}            !!! half width at half maximum (cm-1)\n"
            f"{int(broadening_cfg.get('interpolation', 200))}  !!! interpolation points\n"
            f"{int(broadening_cfg.get('normalization', 2))}    !!! normalization\n"
        )
        with open(broadening_file, "w") as f:
            f.write(b_content)
        run_command(f"{bin_dir}/broadening", cwd=raman_dir)
        raman_raw = os.path.join(raman_dir, "Raman_intensity_complex")
        raman_broad = os.path.join(raman_dir, "Raman_intensity_complex_broadening")
        if os.path.exists(raman_raw):
            run_command(f"mv Raman_intensity_complex Raman_intensity_complex_{e}eV", cwd=raman_dir)
        if os.path.exists(raman_broad):
            run_command(f"mv Raman_intensity_complex_broadening Raman_intensity_complex_broadening_{e}eV", cwd=raman_dir)

    # ── SpectroPy plots ───────────────────────────────────────────────
    spectropy_dir = os.path.normpath(os.path.join(script_dir, "..", "..", "SpectroPy"))
    plot_script = os.path.join(spectropy_dir, "generate_raman_plots.py")
    for e in energies:
        label = f"{e}eV"
        energy_dir = os.path.join(raman_dir, label)
        os.makedirs(energy_dir, exist_ok=True)
        raman_file = os.path.join(raman_dir, f"Raman_intensity_complex_{label}")
        if os.path.exists(raman_file):
            with open(os.path.join(energy_dir, "Raman_intensity_specific.dat"), "w") as f:
                f.write("# Freq(cm-1)   Intensity(arb.)   Irrep.\n")
                with open(raman_file) as src:
                    f.write(src.read())
    if os.path.exists(plot_script):
        run_command(f"echo -e '5.0\\nl' | python3 {plot_script}", cwd=raman_dir, check_success=False)
    else:
        print("WARNING: SpectroPy plotter not found")

    # ── Aggregate output ──────────────────────────────────────────────
    png_dir = os.path.join(output_dir, "raman_spectra")
    data_dir = os.path.join(output_dir, "raman_data")
    os.makedirs(png_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    for e in energies:
        label = f"{e}eV"
        png_src = os.path.join(raman_dir, label, "Raman_plot_styled.png")
        if os.path.exists(png_src):
            shutil.copy2(png_src, os.path.join(png_dir, f"{label}.png"))
    for glob_pat in ("Raman_intensity_complex_*eV", "Raman_intensity_complex_broadening_*eV"):
        for f in glob.glob(os.path.join(raman_dir, glob_pat)):
            shutil.copy2(f, os.path.join(data_dir, os.path.basename(f)))

    # ── --scratch: copy output/ from WORK_DIR to HOME ─────────────────
    if use_scratch:
        home_output = os.path.join(material_dir, "output")
        scratch_output = os.path.join(work_dir, "output")
        if os.path.exists(scratch_output):
            shutil.copytree(scratch_output, home_output, dirs_exist_ok=True)
            print(f"\n  [scratch] Results saved to: {home_output}")

    write_status(step, "completed", "Post-processing done")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)
    write_status("final", "completed", "Automation workflow complete")


def is_complete(work_dir, config):
    raman_data = os.path.join(work_dir, "output", "raman_data")
    return os.path.isdir(raman_data) and bool(os.listdir(raman_data))
