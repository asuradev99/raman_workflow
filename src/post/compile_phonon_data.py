#!/usr/bin/env python3
"""Extract phonon frequencies, irreps, and lattice constants for a material.

Usage:
    compile_phonon_data.py <material_name> [material_name ...]

Searches HOME and SCRATCH vasp_calculations for each material, then writes
to ~/post/<material_name>/:
    phonon_frequencies.csv   — Mode, Irrep, Frequency_cm-1
    lattice_constants.csv    — functional, NELECT, a_prim, c, source
    phonon_spectrum.png      — mode index vs frequency scatter plot
"""

import csv
import math
import os
import sys

import yaml

# Allow imports from raman_workflow root (util package)
_HERE = os.path.dirname(os.path.abspath(__file__))
_RAMAN_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _RAMAN_ROOT not in sys.path:
    sys.path.insert(0, _RAMAN_ROOT)

from util.config import load_config as _load_config

HOME_BASE    = os.path.expanduser("~/vasp_calculations")
SCRATCH_BASE = "/pscratch/sd/e/easuresh/vasp_calculations"
POST_DIR     = os.path.expanduser("~/post")
THz_TO_CM    = 33.35641


def find_material_dir(name):
    for base in (SCRATCH_BASE, HOME_BASE):
        p = os.path.join(base, name)
        if os.path.isdir(p):
            return p
    return None


def load_config(name):
    """Return (merged_config, per_material_config) for a material."""
    shared_path = os.path.join(HOME_BASE, "shared_workflow_settings.yaml")
    per_path    = os.path.join(HOME_BASE, name, "input", "workflow_settings.yaml")
    merged = _load_config([(shared_path, "shared"), (per_path, "per-material")])
    per = {}
    if os.path.isfile(per_path):
        with open(per_path) as f:
            per = yaml.safe_load(f) or {}
    return merged, per


def contcar_path(mat_dir, config, per):
    """Return the one correct CONTCAR path for this simulation type, or raise.

    Uses only per-material config for inference to avoid being misled by shared
    defect templates that exist for all materials.
    """
    pipeline_steps = per.get("pipeline_steps")

    if pipeline_steps is not None:
        two_stage = any(s in pipeline_steps for s in ("defect_relax_2", "defect_relax_2_cpu"))
    else:
        # Infer from per-material incar_settings only (not shared templates)
        per_settings = per.get("incar_settings", {})
        two_stage = "defect_relax_full" in per_settings and "defect_relax_fixed" in per_settings

    if two_stage:
        rel, stage = "scf2/CONTCAR", "scf2"
    else:
        rel, stage = "scf/CONTCAR", "scf"

    p = os.path.join(mat_dir, rel)
    if not os.path.isfile(p) or os.path.getsize(p) == 0:
        raise FileNotFoundError(
            f"Expected {rel} for this simulation type but it is missing or empty in {mat_dir}"
        )
    return p, stage


def parse_lattice(contcar_path):
    """Return (a_supercell, c) from a CONTCAR/POSCAR lattice block."""
    with open(contcar_path) as f:
        lines = f.readlines()
    scale = float(lines[1].strip())
    v1 = [float(x) * scale for x in lines[2].split()]
    v3 = [float(x) * scale for x in lines[4].split()]
    a_sup = math.sqrt(sum(x**2 for x in v1))
    c     = abs(v3[2])
    return a_sup, c


def load_gamma_bands(band_yaml):
    with open(band_yaml) as f:
        data = yaml.safe_load(f)
    for pt in data["phonon"]:
        if all(abs(x) < 1e-6 for x in pt["q-position"]):
            return pt["band"]
    raise ValueError("No Gamma point found in band.yaml")


def load_irreps(irreps_yaml):
    """Return {band_index (1-based): ir_label}."""
    if not os.path.isfile(irreps_yaml):
        return {}
    with open(irreps_yaml) as f:
        data = yaml.safe_load(f)
    mapping = {}
    for nm in data.get("normal_modes", []):
        label = nm.get("ir_label", "")
        for idx in nm.get("band_indices", []):
            mapping[idx] = label
    return mapping


def write_phonon_csv(out_path, bands, irreps):
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Mode", "Irrep", "Frequency_cm-1"])
        for i, b in enumerate(bands, 1):
            freq_cm = b["frequency"] * THz_TO_CM
            w.writerow([i, irreps.get(i, ""), f"{freq_cm:.4f}"])


def write_lattice_csv(out_path, mat_dir, stage, a_sup, c, config, per):
    # Extract GGA and NELECT from per-material incar_settings, falling back to relax template
    gga, nelect = "", ""
    for block_key in ("defect_relax_fixed", "relax"):
        block = per.get("incar_settings", {}).get(block_key, "")
        for line in block.splitlines():
            if not gga and "GGA" in line:
                gga = line.split("=")[-1].strip()
            if not nelect and "NELECT" in line:
                nelect = line.split("=")[-1].strip()
        if gga or nelect:
            break
    # Fall back to shared incar_templates if per-material had nothing
    if not gga:
        for block_key in ("relax", "defect_relax_fixed"):
            block = config.get("incar_templates", {}).get(block_key, "")
            for line in block.splitlines():
                if not gga and "GGA" in line:
                    gga = line.split("=")[-1].strip()
                if not nelect and "NELECT" in line:
                    nelect = line.split("=")[-1].strip()
            if gga:
                break

    # a_sup > 5 Å means it's a 6×6×1 supercell — divide by 6 to get primitive a
    a_prim = a_sup / 6 if a_sup > 5.0 else a_sup

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["material", "functional_GGA", "NELECT", "stage",
                    "a_supercell_Ang", "a_primitive_Ang", "c_Ang", "source_dir"])
        w.writerow([os.path.basename(mat_dir), gga, nelect, stage,
                    f"{a_sup:.6f}", f"{a_prim:.6f}", f"{c:.6f}", mat_dir])


STYLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raman.mplstyle")


def plot_spectrum(out_path, bands, mat_name):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  [warn] matplotlib not available — skipping plot")
        return

    if os.path.isfile(STYLE_FILE):
        plt.style.use(STYLE_FILE)
    matplotlib.rcParams["text.usetex"] = False

    modes = list(range(1, len(bands) + 1))
    freqs = [b["frequency"] * THz_TO_CM for b in bands]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.scatter(modes, freqs, s=8, c=["red" if f < 0 else "steelblue" for f in freqs])
    ax.axhline(0, color="gray", lw=0.5, ls="--")
    ax.set_xlabel("Mode index")
    ax.set_ylabel("Frequency (cm⁻¹)")
    ax.set_title(mat_name)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def process(name):
    mat_dir = find_material_dir(name)
    if mat_dir is None:
        print(f"[{name}] ERROR: directory not found in HOME or SCRATCH")
        return

    hf_dir      = os.path.join(mat_dir, "hf")
    band_yaml   = os.path.join(hf_dir, "band.yaml")
    irreps_yaml = os.path.join(hf_dir, "irreps.yaml")

    if not os.path.isfile(band_yaml):
        print(f"[{name}] ERROR: hf/band.yaml not found")
        return

    out_dir = os.path.join(POST_DIR, name)
    os.makedirs(out_dir, exist_ok=True)

    # Phonon frequencies + irreps
    bands  = load_gamma_bands(band_yaml)
    irreps = load_irreps(irreps_yaml)
    freq_csv = os.path.join(out_dir, "phonon_frequencies.csv")
    write_phonon_csv(freq_csv, bands, irreps)
    print(f"[{name}] {len(bands)} modes → {freq_csv}")

    # Lattice constants — one correct location per simulation type, fail if missing
    config, per = load_config(name)
    try:
        contcar, stage = contcar_path(mat_dir, config, per)
        a_sup, c = parse_lattice(contcar)
        lat_csv  = os.path.join(out_dir, "lattice_constants.csv")
        write_lattice_csv(lat_csv, mat_dir, stage, a_sup, c, config, per)
        a_prim = a_sup / 6 if a_sup > 5.0 else a_sup
        print(f"[{name}] a={a_prim:.6f} Å  c={c:.6f} Å  ({stage}) → {lat_csv}")
    except FileNotFoundError as e:
        print(f"[{name}] ERROR: {e}")
        return

    # Spectrum plot
    plot_path = os.path.join(out_dir, "phonon_spectrum.png")
    plot_spectrum(plot_path, bands, name)
    print(f"[{name}] plot → {plot_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for name in sys.argv[1:]:
        process(name.rstrip("/"))
