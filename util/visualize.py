"""Phonon mode visualization — calls SpectroPy's visualize_modes functions."""

import os
import sys


def generate_phonon_visuals(hf_dir, ctx):
    """Generate VESTA/VMD phonon mode files from band.yaml after postprocessing.

    Loads SpectroPy's visualize_modes.py from system_paths.spectroPy_dir, reads
    the material's input/template.vesta, then writes per-mode files to
    hf_dir/VESTA_MODES/ and/or hf_dir/VMD_MODES/.

    Visualization is best-effort and never fatal: any failure — a missing
    template.vesta, a missing/unreadable band.yaml, or SpectroPy calling
    sys.exit() (which raises SystemExit) — is caught here, logged as a warning,
    and the pipeline continues. Only KeyboardInterrupt is allowed to propagate.
    """
    try:
        _run(hf_dir, ctx)
    except (Exception, SystemExit) as e:
        print(f"  [viz] WARNING: visualization failed ({e!r}) — skipping, pipeline continues")


def _run(hf_dir, ctx):
    import numpy as np

    # ── Load SpectroPy ────────────────────────────────────────────────────────
    spectroPy_dir = ctx.system_paths.get("spectroPy_dir", "")
    if not spectroPy_dir or not os.path.isdir(spectroPy_dir):
        # Fall back: SpectroPy/ sibling of raman_workflow/
        _util_dir = os.path.dirname(os.path.abspath(__file__))
        spectroPy_dir = os.path.join(os.path.dirname(os.path.dirname(_util_dir)), "SpectroPy")
    if not os.path.isdir(spectroPy_dir):
        print(f"  [viz] WARNING: SpectroPy not found at '{spectroPy_dir}' — skipping visualization")
        return

    if spectroPy_dir not in sys.path:
        sys.path.insert(0, spectroPy_dir)
    from visualize_modes import read_contcar, read_band_yaml, write_vesta_file, write_vmd_script

    # ── Settings from ctx ────────────────────────────────────────────────────
    scale_factor    = ctx.viz_scale_factor
    output_format   = ctx.viz_output_format   # "vesta", "vmd", or "both"
    template_name   = ctx.viz_vesta_template
    write_vesta     = output_format in ("vesta", "both")
    write_vmd_flag  = output_format in ("vmd", "both")

    # ── Read template.vesta from the material's own input/ directory ─────────
    # Each material carries its own template (matching its supercell, so the
    # per-site colours/bonds in SITET are correct). The STRUC/CELLP geometry is
    # regenerated from the real structure by write_vesta_file.
    vesta_template_content = ""
    if write_vesta:
        template_src = os.path.join(ctx.material_dir, "input", template_name)
        if os.path.exists(template_src):
            with open(template_src) as f:
                vesta_template_content = f.read()
        else:
            print(f"  [viz] WARNING: template '{template_name}' not found in "
                  f"{ctx.material_dir}/input/ — skipping VESTA output")
            write_vesta = False

    # ── Read structure + phonon data ──────────────────────────────────────────
    poscar_path   = os.path.join(hf_dir, "POSCAR_unitcell")
    band_yaml_path = os.path.join(hf_dir, "band.yaml")
    structure = read_contcar(poscar_path)
    frequencies, eigendisps, masses, n_atoms = read_band_yaml(band_yaml_path)
    n_modes = len(frequencies)

    # ── Scale factor (mirrors run_visualization) ──────────────────────────────
    factor     = scale_factor * np.sqrt(np.max(masses))
    l_cylinder = 4.0 * factor
    l_cone     = 1.5 * factor
    vesta_scale = l_cylinder + l_cone
    thz_to_cm1 = 33.35641

    # ── Output directories ────────────────────────────────────────────────────
    if write_vesta:
        os.makedirs(os.path.join(hf_dir, "VESTA_MODES"), exist_ok=True)
    if write_vmd_flag:
        os.makedirs(os.path.join(hf_dir, "VMD_MODES"), exist_ok=True)

    # ── Write per-mode files ──────────────────────────────────────────────────
    for i in range(n_modes):
        freq_cm1 = frequencies[i] * thz_to_cm1

        if write_vesta:
            fname = os.path.join(hf_dir, "VESTA_MODES",
                                 f"mode_{i+1:03d}_({freq_cm1:.1f}cm-1).vesta")
            write_vesta_file(fname, vesta_template_content,
                             eigendisps[i], n_atoms, vesta_scale, freq_cm1,
                             structure=structure)

        if write_vmd_flag:
            fname = os.path.join(hf_dir, "VMD_MODES", f"mode_{i+1:03d}.vmd")
            write_vmd_script(fname, structure["positions_cart"],
                             eigendisps[i], l_cylinder, l_cone)

    # ── Frequency table ───────────────────────────────────────────────────────
    modes_txt = os.path.join(hf_dir, "all_modes.txt")
    if not os.path.exists(modes_txt):
        with open(modes_txt, "w") as f:
            f.write("# mode  freq(cm-1)\n")
            for i in range(n_modes):
                f.write(f"{i+1:4d}   {frequencies[i] * thz_to_cm1:10.4f}\n")

    n_written = n_modes if (write_vesta or write_vmd_flag) else 0
    fmt_label = {"vesta": "VESTA", "vmd": "VMD", "both": "VESTA+VMD"}.get(output_format, output_format)
    print(f"  [viz] {n_written} mode files written ({fmt_label}) → {os.path.basename(hf_dir)}/")
    if write_vesta:
        print(f"  [viz] VESTA_MODES/ — {n_modes} .vesta files")
    if write_vmd_flag:
        print(f"  [viz] VMD_MODES/   — {n_modes} .vmd files")
