"""Tall waterfall of VB- 9x9x1 Raman spectra (A1'+E' symmetry-filtered), all energies.

Same style as raman_waterfall_all_energies.py, but built from the raw per-mode
Raman_intensity_complex_<E>eV files (freq, intensity, irrep) filtered down to
the xx-backscattering-allowed irreps (A1', E') before Lorentzian broadening --
since the pipeline's own symmetry_filter option hasn't been run through
post_process for this material yet. Symmetry labels are intentionally left
off the plot for now.

Usage:
    python raman_9x9_waterfall_filtered.py
"""
import glob
import os
import re
import shutil
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ALLOWED_IRREPS = {"A1'", "E'"}
HWHM = 1.0  # cm-1, matches this material's broadening_input (mode=2 Lorentzian, hwhm=1)

_HERE = os.path.dirname(os.path.abspath(__file__))
STYLE_FILE = os.path.join(_HERE, "raman.mplstyle")

RAMAN_DIR = "/pscratch/sd/e/easuresh/vasp_calculations/hBN_VB-_mixed_9x9/raman"
OUT = "/global/u1/e/easuresh/post/graphs"
OUT_NAME = "raman_9x9_all_energies_filtered.png"
XLIM = (0, 1600)

STEP = 1.0
TRACE_SCALE = 0.85

TEXT_PRIMARY = "#000000"
TEXT_SECONDARY = "#000000"
SURFACE = "#ffffff"

# House style (raman.mplstyle: text.usetex=True, serif, dpi 300, tick/line
# sizing) shared with the other ~/post plotting scripts, loaded first so its
# text.usetex setting takes effect; script-specific colors layered on top.
# text.usetex falls back to False if latex isn't on PATH (module load texlive/2024).
if os.path.isfile(STYLE_FILE):
    plt.style.use(STYLE_FILE)
if not shutil.which("latex"):
    plt.rcParams["text.usetex"] = False
    plt.rcParams["mathtext.fontset"] = "cm"

plt.rcParams.update({
    "text.color": TEXT_PRIMARY,
    "axes.labelcolor": TEXT_PRIMARY,
    "xtick.color": TEXT_SECONDARY,
    "ytick.color": TEXT_SECONDARY,
    "axes.edgecolor": "#d8d7d2",
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def parse_modes(path):
    modes = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3:
                freq, inten, irrep = float(parts[0]), float(parts[1]), parts[2]
                modes.append((freq, inten, irrep))
    return modes


def lorentzian_spectrum(modes, xmin, xmax, hwhm=HWHM, npts=6000, keep=None):
    x = np.linspace(xmin, xmax, npts)
    y = np.zeros_like(x)
    for freq, inten, irrep in modes:
        if keep is not None and irrep not in keep:
            continue
        y += inten * (hwhm**2) / ((x - freq)**2 + hwhm**2)
    return x, y


ENERGIES_TO_SHOW = ["0.00", "1.96", "2.33"]

# Discover every computed energy from the raw per-mode files, sorted ascending,
# then keep only the requested subset.
all_energies = sorted(
    re.search(r"Raman_intensity_complex_([\d.]+)eV$", f).group(1)
    for f in glob.glob(os.path.join(RAMAN_DIR, "Raman_intensity_complex_*eV"))
    if "broadening" not in f
)
energies = [e for e in all_energies if e in ENERGIES_TO_SHOW]
missing = set(ENERGIES_TO_SHOW) - set(energies)
if missing:
    print(f"WARNING: requested energies not found: {missing}")
print(f"showing {len(energies)} energies: {', '.join(energies)}")

spectra = {}
for e in energies:
    modes = parse_modes(os.path.join(RAMAN_DIR, f"Raman_intensity_complex_{e}eV"))
    x, y = lorentzian_spectrum(modes, XLIM[0], XLIM[1], keep=ALLOWED_IRREPS)
    spectra[e] = (x, y)

cmap = matplotlib.colormaps["viridis"]
# Cap at 0.75 instead of 1.0 so the top trace lands on green, not viridis's yellow tail.
colors = {e: cmap(0.75 * i / max(len(energies) - 1, 1)) for i, e in enumerate(energies)}

fig, ax = plt.subplots(figsize=(9, 0.95 * len(energies) + 1.6))

for i, e in enumerate(energies):
    x, y = spectra[e]
    peak = y.max() if y.max() > 0 else 1.0
    offset = i * STEP
    ax.axhline(offset, color="#e4e3de", linewidth=0.7, zorder=1)
    ax.plot(x, offset + TRACE_SCALE * (y / peak), color=colors[e],
            linewidth=1.3, zorder=3)
    ax.text(0.012, offset + TRACE_SCALE * 0.90, f"{e} eV",
            transform=ax.get_yaxis_transform(), fontsize=11,
            color=colors[e], fontweight="bold", va="top", ha="left",
            clip_on=False)

ax.set_xlim(*XLIM)
ax.set_ylim(-0.08 * STEP, (len(energies) - 1) * STEP + TRACE_SCALE + 0.20 * STEP)

REFERENCE_LINES = [
    (330, r"D2b"),
    (455, r"D2a"),
    (1283, r"D1"),
    (1357, r"E$_{2g}$"),
]
for freq, label in REFERENCE_LINES:
    ax.axvline(freq, color="#9a9994", linewidth=0.8, linestyle="--", zorder=2)
    ax.text(freq, 1.01, label, transform=ax.get_xaxis_transform(),
            fontsize=10, fontweight="bold", color=TEXT_SECONDARY, ha="center",
            va="bottom", clip_on=False)

for s in ax.spines.values():
    s.set_visible(True)
    s.set_color("#9a9994")
    s.set_linewidth(0.9)
ax.tick_params(left=False, labelleft=False)
ax.tick_params(axis="x", direction="out", length=4, width=0.8)
ax.grid(axis="x", color="#e8e7e2", linewidth=0.7)
ax.set_axisbelow(True)

ax.text(0.985, 0.965, "V$_B^-$ (9$\\times$9$\\times$1)",
        transform=ax.transAxes, fontsize=9, fontweight="bold",
        color=TEXT_PRIMARY, va="top", ha="right")
ax.set_xlabel(r"Raman shift (cm$^{-1}$)", fontsize=12, fontweight="bold")
ax.set_ylabel(r"Intensity (arb.\ units)", fontsize=12, fontweight="bold")
ax.tick_params(axis="x", labelsize=10)
for tick_label in ax.get_xticklabels():
    tick_label.set_fontweight("bold")

fig.tight_layout()
fig.subplots_adjust(right=0.97)
fig.savefig(f"{OUT}/{OUT_NAME}", dpi=200)
print("Wrote", f"{OUT}/{OUT_NAME}")
