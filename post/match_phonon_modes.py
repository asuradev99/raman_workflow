#!/usr/bin/env python3
"""Compare phonon frequencies across materials by mode index.

Modes are aligned by index (mode 1 → mode 1, etc.).  A1' modes are
highlighted in a distinct colour on the plot.

Usage:
    match_phonon_modes.py <material1> <material2> [material3 ...] [--ref <material>]

    --ref <name>   Use <name> as the reference ordering (default: first material)
    --out <path>   CSV output path (default: ~/post/mode_match_<m1>_vs_<m2>.csv)
    --plot <path>  Plot output path (default: ~/post/mode_match_<m1>_vs_<m2>.png)

Output CSV columns:
    mode            — mode index
    ref_irrep       — symmetry label in the reference material
    <mat>_freq_cm-1 — frequency for each material
    <mat>_irrep     — symmetry label in each material
"""

import csv
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_RAMAN_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _RAMAN_ROOT not in sys.path:
    sys.path.insert(0, _RAMAN_ROOT)

POST_DIR = os.path.expanduser("~/post")


# ── Data loading ──────────────────────────────────────────────────────────────

def load_post_csv(name):
    """Load phonon_frequencies.csv from ~/post/<name>/. Returns (freqs, irreps)."""
    p = os.path.join(POST_DIR, name, "phonon_frequencies.csv")
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f"~/post/{name}/phonon_frequencies.csv not found — "
            f"run compile_phonon_data.py {name} first"
        )
    freqs, irreps = [], {}
    with open(p) as f:
        reader = csv.DictReader(f)
        for row in reader:
            mode = int(row["Mode"])
            freqs.append(float(row["Frequency_cm-1"]))
            irreps[mode] = row["Irrep"]
    return np.array(freqs), irreps


# ── Output ────────────────────────────────────────────────────────────────────

def write_csv(out_path, all_names, all_freqs, all_irreps):
    n = len(all_freqs[0])
    header = ["mode", "ref_irrep"]
    for name in all_names:
        header += [f"{name}_freq_cm-1", f"{name}_irrep"]

    rows = []
    ref_irreps = all_irreps[0]
    for i in range(n):
        row = [i + 1, ref_irreps.get(i + 1, "")]
        for k in range(len(all_names)):
            row += [f"{all_freqs[k][i]:.4f}", all_irreps[k].get(i + 1, "")]
        rows.append(row)

    with open(out_path, "w", newline="") as f:
        csv.writer(f).writerows([header] + rows)
    print(f"CSV → {out_path}")


# Matches PALETTE in plot_lattice_constants.py
PALETTE = {
    "CA": ("#1565C0", "#90CAF9"),  # LDA     — blue
    "PE": ("#B71C1C", "#EF9A9A"),  # PBE     — red
    "PS": ("#1B5E20", "#A5D6A7"),  # PBEsol  — green
}

# Clean display labels for legend
LABEL_MAP = {
    "hBN_PBEsol_defect":               "PBEsol",
    "hBN_PBEsol_defect_opt":           "PBEsol (opt)",
    "hBN_PBEsol_defect_fixed":         "PBEsol (fixed)",
    "hBN_LDA_defect":                  "LDA",
    "hBN_LDA_defect_fixed":            "LDA (fixed)",
    "hBN_PBE_defect":                  "PBE",
    "hBN_PBE_defect_fixed":            "PBE (fixed)",
    "hBN_PBEsol_defect_neutral":       "PBEsol neutral",
    "hBN_PBEsol_defect_neutral_fixed": "PBEsol neutral (fixed)",
}


def _gga_from_name(name):
    """Derive GGA key from material name."""
    if "_PBEsol_" in name:
        return "PS"
    if "_PBE_" in name:
        return "PE"
    if "_LDA_" in name:
        return "CA"
    return None


def _mid_color(dark, light):
    r = lambda h: int(h[1:3], 16)
    g = lambda h: int(h[3:5], 16)
    b = lambda h: int(h[5:7], 16)
    return "#{:02x}{:02x}{:02x}".format(
        (r(dark) + r(light)) // 2,
        (g(dark) + g(light)) // 2,
        (b(dark) + b(light)) // 2,
    )


def build_color_map(names):
    """Return {name: (color, is_fixed)} keyed by functional, consistent with PALETTE."""
    result = {}
    for name in names:
        gga = _gga_from_name(name)
        if gga and gga in PALETTE:
            dark, light = PALETTE[gga]
        else:
            dark = light = "#888888"
        is_fixed = name.endswith("_fixed")
        is_opt   = name.endswith("_opt")
        if is_fixed:
            color = light
        elif is_opt:
            color = _mid_color(dark, light)
        else:
            color = dark
        result[name] = (color, is_fixed)
    return result


STYLE_FILE = os.path.join(_HERE, "raman.mplstyle")

# High-intensity peaks from Liangbo's LDA data (V_B_negative_LDA_liangbo),
# ranked by polarization-averaged Raman intensity.
# (mode_index, freq_cm-1, symmetry)
LIANGBO_PEAKS = [
    (191, 1363.3, "E'"),
    (192, 1363.3, "E'"),
    (195, 1407.4, "A1'"),
    (208, 1512.8, "A1'"),
    (207, 1490.7, "E'"),
    (206, 1490.7, "E'"),
    (148, 1241.7, "A1'"),
    (209, 1514.5, "E'"),
    (210, 1514.5, "E'"),
    (122,  892.9, "E'"),
    (123,  892.9, "E'"),
    (151, 1263.8, "E'"),
    (150, 1263.8, "E'"),
    (184, 1337.0, "A1'"),
    (142, 1154.5, "E'"),
    (143, 1154.5, "E'"),
    (204, 1476.0, "A1'"),
    (144, 1189.3, "E'"),
    (145, 1189.3, "E'"),
    ( 47,  388.9, "A1'"),
    (213, 1553.0, "E'"),
    (141, 1147.5, "A1'"),
    ( 50,  481.1, "E'"),
    ( 49,  481.1, "E'"),
    (212, 1553.0, "E'"),
]

# Box colours for each symmetry label
PEAK_COLORS = {
    "E'":  "#E65100",   # orange
    "A1'": "#6A1B9A",   # purple
}


def plot_modes(out_path, all_names, all_freqs, all_irreps):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[warn] matplotlib not available — skipping plot")
        return

    if os.path.isfile(STYLE_FILE):
        plt.style.use(STYLE_FILE)
    matplotlib.rcParams["text.usetex"] = False  # LaTeX not available on login node
    matplotlib.rcParams["axes.labelsize"]  = 20
    matplotlib.rcParams["xtick.labelsize"] = 16
    matplotlib.rcParams["ytick.labelsize"] = 16

    color_map = build_color_map(all_names)
    n = len(all_freqs[0])
    modes = np.arange(1, n + 1)

    fig, ax = plt.subplots(figsize=(13, 18))

    for k, name in enumerate(all_names):
        freqs    = all_freqs[k]
        color, is_fixed = color_map[name]
        alpha    = 0.65 if is_fixed else 0.85
        size     = 8 if is_fixed else 10
        label = LABEL_MAP.get(name, name)
        ax.scatter(modes, freqs, s=size, color=color,
                   marker="o", alpha=alpha, zorder=2, label=label)

    ax.scatter([], [], s=55, marker="*", color="gray", edgecolors="black",
               linewidths=0.5, label="Matches Liangbo peak")

    ax.axhline(0, color="gray", lw=0.5, ls="--")

    # Thin horizontal lines at each unique peak frequency, coloured by symmetry
    from matplotlib.lines import Line2D
    drawn_syms = set()
    seen_freqs = set()
    for _, freq, sym in LIANGBO_PEAKS:
        if freq in seen_freqs:
            continue
        seen_freqs.add(freq)
        color = PEAK_COLORS.get(sym, "#333333")
        ax.axhline(freq, color=color, lw=0.8, alpha=0.6, zorder=1)
        drawn_syms.add(sym)

    ax.set_xlabel("Mode index")
    ax.set_ylabel("Frequency (cm⁻¹)")

    extra_handles = [
        Line2D([0], [0], color=PEAK_COLORS["E'"],  lw=1.5, label="E' peak (Liangbo LDA)"),
        Line2D([0], [0], color=PEAK_COLORS["A1'"], lw=1.5, label="A1' peak (Liangbo LDA)"),
    ]
    ax.legend(fontsize=14, markerscale=1.5, ncol=2,
              handles=ax.get_legend_handles_labels()[0] + extra_handles)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Plot → {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    out_csv = out_plot = None
    materials = []
    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_csv = args[i + 1]; i += 2
        elif args[i] == "--plot" and i + 1 < len(args):
            out_plot = args[i + 1]; i += 2
        else:
            materials.append(args[i].rstrip("/")); i += 1

    if len(materials) < 2:
        print("ERROR: need at least two material names")
        sys.exit(1)

    slug = "_vs_".join(materials)
    out_csv  = out_csv  or os.path.join(POST_DIR, f"mode_match_{slug}.csv")
    out_plot = out_plot or os.path.join(POST_DIR, f"mode_match_{slug}.png")
    os.makedirs(POST_DIR, exist_ok=True)

    all_freqs  = []
    all_irreps = []
    for name in materials:
        print(f"Loading: ~/post/{name}/phonon_frequencies.csv")
        freqs, irreps = load_post_csv(name)
        all_freqs.append(freqs)
        all_irreps.append(irreps)

    n_modes = [len(f) for f in all_freqs]
    if len(set(n_modes)) > 1:
        print(f"[warn] mode counts differ: {dict(zip(materials, n_modes))} — truncating to min")
        n_min = min(n_modes)
        all_freqs  = [f[:n_min] for f in all_freqs]

    write_csv(out_csv, materials, all_freqs, all_irreps)
    plot_modes(out_plot, materials, all_freqs, all_irreps)


if __name__ == "__main__":
    main()
