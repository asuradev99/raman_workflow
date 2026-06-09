"""Step 8 — Post-processing (kopia, RAMFILE, energy loop, SpectroPy, output)."""
import os, time, glob, shutil
from util.io import run_command
from util.postproc import generate_kopia_script, inject_ramfile_energies
from util.status import print_step_header, print_step_result

def run(ctx):
    ws = ctx["write_status"]
    R = ctx["raman_dir"]
    BU = ctx["binary_utilities_dir"]
    CF = ctx["cpu_flag"]
    DE = ctx["desired_energies"]
    W = ctx["work_dir"]
    M = ctx["material_dir"]
    SF = ctx["scratch_flag"]
    SD = ctx["script_dir"]
    C = ctx["config"]
    H = ctx["hffiles_dir"]

    print_step_header(8)
    ws(8, "running", "Post-processing")
    _t0 = time.time()

    # ── Kopia ─────────────────────────────────────────────────────────
    ra = sorted(glob.glob(os.path.join(R, "ra_pos_*")))
    if not ra:
        raise RuntimeError("No ra_pos_* directories found")
    generate_kopia_script(R, ra)
    ax = os.path.join(R, "AXML")
    if not os.path.isdir(ax):
        raise RuntimeError("AXML/ not created")
    af = [f for f in os.listdir(ax) if f.endswith(".xml")]
    if not af:
        raise RuntimeError("AXML/ empty")
    empty = [f for f in af if os.path.getsize(os.path.join(ax, f)) == 0]
    if empty:
        raise RuntimeError(f"{len(empty)} empty XML files")
    print(f"  [verify] AXML/ contains {len(af)} valid XML files")

    # ── RAMFILE ───────────────────────────────────────────────────────
    src = os.path.join(BU, "ramfile_dynamic.sh")
    if not os.path.exists(src):
        raise RuntimeError(f"ramfile_dynamic.sh not found at {src}")
    sr = os.path.join(R, "store_ramfile")
    se = os.path.join(R, "store_epsilon")
    os.makedirs(sr, exist_ok=True)
    os.makedirs(se, exist_ok=True)
    dst = os.path.join(R, "ramfile_dynamic.sh")
    inject_ramfile_energies(src, dst, DE)
    run_command(f"export PATH={BU}:$PATH && bash ramfile_dynamic.sh", cwd=R)
    for e in DE:
        if not os.path.exists(os.path.join(sr, f"RAMFILE_{e}")):
            raise RuntimeError(f"RAMFILE_{e} not produced")

    # ── Static copy to output/ ────────────────────────────────────────
    run_command(f"cp {H}/band.yaml .", cwd=R, check_success=False)
    run_command(f"cp {H}/irreps.yaml .", cwd=R, check_success=False)
    od = os.path.join(W, "output")
    run_command(f"mkdir -p {od}", cwd=W)
    for sb in ("band.yaml", "irreps.yaml", "POSCAR_unitcell", "SPOSCAR",
               "FORCE_SETS", "phonopy.yaml", "CONTCAR",
               "eigenvectors.conf", "symmetry.conf"):
        s = os.path.join(H, sb)
        if os.path.exists(s) and os.path.getsize(s) > 0:
            run_command(f"cp {s} {od}/", cwd=W, check_success=False)
    for a in [os.path.join(H, "all_mode.txt")] + glob.glob(os.path.join(H, "mode*")):
        if os.path.exists(a) and os.path.getsize(a) > 0:
            run_command(f"cp {a} {od}/", cwd=W, check_success=False)
    idd = os.path.join(od, "incar")
    run_command(f"mkdir -p {idd}", cwd=W)
    incar_pairs = [
        (os.path.join(W, "scf", "INCAR"), "relax.incar"),
        (os.path.join(H, "groundstate", "INCAR"), "supercell_relax.incar"),
        (os.path.join(H, "INCAR"), "hf_force_constants.incar"),
        (os.path.join(R, "INCAR"), "dielec_raman.incar"),
    ]
    for s, dn in incar_pairs:
        if os.path.exists(s) and os.path.getsize(s) > 0:
            shutil.copy2(s, os.path.join(idd, dn))

    # ── Energy loop (raman_tensor + broadening) ───────────────────────
    for b in ("raman_tensor", "broadening"):
        if not os.path.exists(os.path.join(BU, b)):
            raise FileNotFoundError(f"{b} not found")
    for e in DE:
        if not os.path.exists(os.path.join(sr, f"RAMFILE_{e}")):
            raise RuntimeError(f"RAMFILE_{e} not found")
    for e in DE:
        print(f"\n  [energy] Processing {e} eV —")
        run_command("rm -f RAMFILE", cwd=R, check_success=False)
        run_command(f"cp store_ramfile/RAMFILE_{e} RAMFILE", cwd=R)
        ri = f"{ctx['raman_incident_pol']}\n{ctx['raman_scattered_pol']}\n{ctx['raman_surface_normal']}\n"
        rf = os.path.join(R, ".raman_tensor_input")
        with open(rf, "w") as f:
            f.write(ri)
        run_command(f"{BU}/raman_tensor < .raman_tensor_input > /dev/null",
                    cwd=R, check_success=not CF)
        os.remove(rf)
        _b = C.get("broadening", {})
        bi = os.path.join(R, "broadening_input")
        b_content = (
            f"Raman_intensity_complex  !!! the file name\n"
            f"{int(_b.get('mode', 2))}            !!! peak broadening mode\n"
            f"{int(_b.get('hwhm', 1))}            !!! half width at half maximum (cm-1)\n"
            f"{int(_b.get('interpolation', 200))}  !!! interpolation points\n"
            f"{int(_b.get('normalization', 2))}    !!! normalization\n"
        )
        with open(bi, "w") as f:
            f.write(b_content)
        run_command(f"{BU}/broadening", cwd=R)
        rp1 = os.path.join(R, "Raman_intensity_complex")
        rp2 = os.path.join(R, "Raman_intensity_complex_broadening")
        if os.path.exists(rp1):
            run_command(f"mv Raman_intensity_complex Raman_intensity_complex_{e}eV", cwd=R)
        if os.path.exists(rp2):
            run_command(f"mv Raman_intensity_complex_broadening Raman_intensity_complex_broadening_{e}eV", cwd=R)

    # ── SpectroPy plots ───────────────────────────────────────────────
    sp = os.path.normpath(os.path.join(SD, "..", "SpectroPy"))
    gp = os.path.join(sp, "generate_raman_plots.py")
    for e in DE:
        el = f"{e}eV"
        ed = os.path.join(R, el)
        os.makedirs(ed, exist_ok=True)
        sf = os.path.join(R, f"Raman_intensity_complex_{el}")
        if os.path.exists(sf):
            with open(os.path.join(ed, "Raman_intensity_specific.dat"), "w") as f:
                f.write("# Freq(cm-1)   Intensity(arb.)   Irrep.\n")
                with open(sf) as src:
                    f.write(src.read())
    if os.path.exists(gp):
        run_command(f"echo -e '5.0\\nl' | python3 {gp}", cwd=R, check_success=False)
    else:
        print("WARNING: SpectroPy plotter not found")

    # ── Aggregate output ──────────────────────────────────────────────
    rp = os.path.join(od, "raman_spectra")
    rd = os.path.join(od, "raman_data")
    os.makedirs(rp, exist_ok=True)
    os.makedirs(rd, exist_ok=True)
    for e in DE:
        el = f"{e}eV"
        ps = os.path.join(R, el, "Raman_plot_styled.png")
        if os.path.exists(ps):
            shutil.copy2(ps, os.path.join(rp, f"{el}.png"))
    for pat in ("Raman_intensity_complex_*eV", "Raman_intensity_complex_broadening_*eV"):
        for f in glob.glob(os.path.join(R, pat)):
            shutil.copy2(f, os.path.join(rd, os.path.basename(f)))

    ws("final", "completed", "Automation workflow complete")

    # ── --scratch: copy output/ from WORK_DIR to HOME ─────────────────
    if SF:
        ho = os.path.join(M, "output")
        so = os.path.join(W, "output")
        if os.path.exists(so):
            run_command(f"mkdir -p {ho}", cwd=M)
            run_command(f"cp -r {so}/* {ho}/", cwd=M, check_success=False)
            print(f"\n  [scratch] Results saved to: {ho}")

    ws(8, "completed", "Post-processing done")
    print_step_result(8, ok=True, duration_s=time.time() - _t0)
