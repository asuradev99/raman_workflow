#!/usr/bin/env python3
"""
plot_phonon_modes.py  —  2D eigenvector (mode arrow) plots from Phonopy output

Reads band.yaml, CONTCAR, and irreps.yaml to generate a figure per phonon mode
showing the crystal structure with arrows indicating eigenvector displacements.

Usage:
    python3 plot_phonon_modes.py \\
        --band-yaml path/to/band.yaml \\
        --contcar   path/to/CONTCAR \\
        --output-dir  path/to/mode_plots \\
        --irreps    path/to/irreps.yaml   (optional)

Dependencies: numpy, matplotlib, pyyaml (all in phonopy_env)
"""

import argparse
import os
import sys
import re
import yaml
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch


# ── Helpers ─────────────────────────────────────────────────────────────────

def read_contcar(filepath):
    """Return dict with lattice vectors, atomic positions (Cartesian), symbols."""
    with open(filepath) as f:
        lines = f.readlines()
    lat = np.array([list(map(float, lines[i].split())) for i in range(2, 5)])
    toks = lines[5].split()
    counts = list(map(int, lines[6].split()))
    # Direct or Cartesian
    coord_type = lines[7].strip().lower()
    pos_start = 8
    total_atoms = sum(counts)
    pos_lines = [list(map(float, lines[i].split()[:3])) for i in range(pos_start, pos_start + total_atoms)]
    pos_frac = np.array(pos_lines)
    if coord_type.startswith('c'):
        pos_cart = pos_frac
    else:
        pos_cart = pos_frac @ lat
    symbols = []
    for s, c in zip(toks, counts):
        symbols.extend([s] * c)
    return {"lat": lat, "pos_cart": pos_cart, "symbols": symbols, "natom": total_atoms}


def read_band_yaml_gamma(filepath):
    """Return (frequencies_cm1, eigenvectors_norm) at the Gamma point (first q)."""
    with open(filepath) as f:
        data = yaml.safe_load(f)
    phonon = data['phonon'][0]  # first q-point (Gamma)
    bands = phonon['band']
    freqs = np.array([b['frequency'] for b in bands])
    natom = data['natom']
    nmodes = 3 * natom
    # eigenvectors: shape (nmodes, natom, 3) — real part only
    eig = np.zeros((nmodes, natom, 3))
    for i, b in enumerate(bands):
        for j, atom_eig in enumerate(b['eigenvector']):
            # each atom_eig is [[dx_re, dx_im], [dy_re, dy_im], [dz_re, dz_im]]
            eig[i, j, :] = [atom_eig[k][0] for k in range(3)]
    freqs_cm1 = freqs * 33.35641  # THz → cm⁻¹
    return freqs_cm1, eig, natom


def read_irreps(filepath):
    """Return dict mapping band_index (1-based) -> ir_label."""
    if not os.path.exists(filepath):
        return {}
    with open(filepath) as f:
        data = yaml.safe_load(f)
    mapping = {}
    for entry in data.get('normal_modes', []):
        label = entry.get('ir_label', '?')
        for idx in entry.get('band_indices', []):
            mapping[idx] = label
    return mapping


def get_projection(eig_vec, view='xy'):
    """Project 3D eigenvector onto 2D view plane."""
    if view == 'xy':
        return eig_vec[0], eig_vec[1]
    elif view == 'xz':
        return eig_vec[0], eig_vec[2]
    elif view == 'yz':
        return eig_vec[1], eig_vec[2]
    return eig_vec[0], eig_vec[1]


# ── Plotting ────────────────────────────────────────────────────────────────


def format_label_for_latex(label):
    """Convert a plain-text irrep label (e.g. 'A2'' ) to a LaTeX math string.

    Examples
    --------
    >>> format_label_for_latex('A2')
    '$\\\\mathrm{A}_{2}$'
    >>> format_label_for_latex("E'")
    "$\\\\mathrm{E}'$"
    >>> format_label_for_latex("A2''")
    '$\\\\mathrm{A}_{2}′′$'
    """
    if not label:
        return ''
    match = re.match(r"^([A-Za-z]+)(\d*)(['\"]*)$", label)
    if match:
        base, subscript, primes = match.groups()
        tex = f"$\\mathrm{{{base}}}"
        if subscript:
            tex += f"_{{{subscript}}}"
        if primes:
            tex += primes
        tex += "$"
        return tex
    return f"$\\mathrm{{{label}}}$"

# Relative atomic radii (covalent, in pm) — used to scale scatter marker sizes
# so larger atoms get visibly bigger markers while keeping absolute sizes small.
ATOM_RADII = {
    'H':  31, 'He': 28,
    'Li': 67, 'Be': 45, 'B':  84, 'C':  73, 'N':  71, 'O':  66, 'F':  57, 'Ne': 58,
    'Na': 99, 'Mg': 87, 'Al': 82, 'Si': 85, 'P':  80, 'S': 102, 'Cl': 79, 'Ar': 71,
    'K': 119, 'Ca': 99, 'Sc': 94, 'Ti': 88, 'V':  84, 'Cr': 82, 'Mn': 80, 'Fe': 77,
    'Co': 74, 'Ni': 71, 'Cu': 68, 'Zn': 69, 'Ga': 82, 'Ge': 85, 'As': 90, 'Se':116,
    'Br': 93, 'Kr': 88,
    'Rb':128, 'Sr':103, 'Y': 98, 'Zr': 94, 'Nb': 92, 'Mo':154, 'Tc': 90, 'Ru': 88,
    'Rh': 86, 'Pd': 84, 'Ag': 80, 'Cd': 80, 'In': 93, 'Sn': 97, 'Sb': 98, 'Te':135,
    'I':  96, 'Xe': 96,
    'Cs':149, 'Ba':106, 'W': 162, 'Pt': 83, 'Au': 86, 'Hg': 86,
    'Pb': 87, 'Bi': 92,
}

# Colour map for atom types (module-level so both plot_mode and main can use it)
COLOR_MAP = {
    'B': '#1f78b4', 'N': '#33a02c', 'S': '#e31a1c', 'Mo': '#6a3d9a',
    'W': '#b15928', 'Se': '#ff7f00', 'Te': '#cab2d6',
}


def plot_mode(ax, structure, freqs_cm1, eigenvectors, mode_idx, ir_label,
              view='xy'):
    """
    Draw a single phonon mode on the provided *ax*.
    mode_idx: 0-based index into eigenvectors array.
    """
    natom = structure['natom']
    pos = structure['pos_cart']
    sym = structure['symbols']
    eig = eigenvectors[mode_idx]

    # Determine scale: set max arrow length to ~15% of max lattice extent
    lat = structure['lat']
    max_extent = max(np.linalg.norm(lat[0]), np.linalg.norm(lat[1]))
    max_disp = np.max(np.linalg.norm(eig, axis=1))
    arrow_scale = (0.15 * max_extent) / max_disp if max_disp > 0 else 1.0

    # Project positions and displacements
    px, py = get_projection(pos.T, view)
    dx, dy = get_projection(eig.T, view)

    # Plot unit cell outline
    origin = np.array([0.0, 0.0])
    if view == 'xy':
        corners = np.array([
            [0, 0],
            [lat[0, 0], lat[0, 1]],
            [lat[0, 0] + lat[1, 0], lat[0, 1] + lat[1, 1]],
            [lat[1, 0], lat[1, 1]],
            [0, 0],
        ])
    else:
        # fallback: just use x-range, y-range
        cx, cy = get_projection(lat.T, view)
        corners = np.array([[0, 0], [cx[0], cy[0]], [cx[0]+cx[1], cy[0]+cy[1]], [cx[1], cy[1]], [0, 0]])

    ax.plot(corners[:, 0], corners[:, 1], 'k-', linewidth=0.8, alpha=0.4)

    default_colors = plt.cm.tab10(np.linspace(0, 1, len(set(sym))))

    # Compute relative marker sizes from atomic radii (area ∝ r²)
    radii = np.array([ATOM_RADII.get(s, 72) for s in sym])
    max_radius = radii.max()
    base_size = 60   # scatter area for the largest atom in the system
    sizes = base_size * (radii / max_radius) ** 2

    # Draw arrows (zorder=5 — on top of atoms)
    for i in range(natom):
        if np.linalg.norm(eig[i]) < 1e-8:
            continue
        x, y = px[i], py[i]
        u, v = dx[i] * arrow_scale, dy[i] * arrow_scale
        if abs(u) < 1e-10 and abs(v) < 1e-10:
            continue
        # Scale arrow length for visibility
        arrow_len = np.sqrt(u**2 + v**2)
        if arrow_len < 0.01:
            continue
        arrow = FancyArrowPatch(
            (x, y), (x + u, y + v),
            arrowstyle='->',
            mutation_scale=10.0,
            lw=1.2, color='#d62728', alpha=0.85, zorder=5
        )
        ax.add_patch(arrow)

    # Draw atoms with scatter markers scaled relatively by atomic radius.
    # Marker area (s) is proportional to r² so larger elements are visibly
    # bigger, but the absolute size is small enough not to overwhelm the plot.
    for i in range(natom):
        c = COLOR_MAP.get(sym[i], default_colors[i % len(default_colors)])
        ax.scatter(px[i], py[i], s=sizes[i], c=[c], edgecolors='k',
                   linewidths=0.6, zorder=4)

    # For out-of-plane modes (e.g. A₂'' in hBN) the xy-projected eigenvectors
    # are zero, so no arrows are drawn.  Place a small red dot at each atom
    # position to indicate out-of-page displacement.
    #
    # Two detection methods:
    #   (a) The irrep label matches "A2*" — by symmetry this is purely out-of-plane
    #       → draw dots at ALL atoms unconditionally.
    #   (b) No label match, but some atom numerically has dx=dy≈0, dz≠0
    #       → draw dots at those individual atoms.
    has_a2_label = bool(ir_label and ir_label.startswith('A2'))
    if has_a2_label:
        # Symmetry-guaranteed out-of-plane — dot every atom
        for i in range(natom):
            ax.scatter(px[i], py[i], s=18, c='#d62728', edgecolors='none',
                       zorder=6)
    else:
        # Fallback numerical check for modes without an irrep label
        for i in range(natom):
            dx_i, dy_i = eig[i][0], eig[i][1]
            dz_i = eig[i][2]
            in_plane = np.sqrt(dx_i**2 + dy_i**2)
            if in_plane < 1e-10 and abs(dz_i) > 1e-8:
                ax.scatter(px[i], py[i], s=18, c='#d62728', edgecolors='none',
                           zorder=6)

    # Centered annotation inside the graph (top centre in axes coords)
    # instead of a title above the subplot — this saves vertical space.
    # Strings are formatted for LaTeX (usetex=True).
    freq_val = freqs_cm1[mode_idx]
    ir_label_latex = format_label_for_latex(ir_label) if ir_label else ''
    freq_str = f"${freq_val:.1f}\\,\\mathrm{{cm}}^{{-1}}$"
    if ir_label_latex:
        label_text = f"{ir_label_latex}:  {freq_str}"
    else:
        label_text = freq_str
    ax.text(0.5, 0.97, label_text, transform=ax.transAxes,
            ha='center', va='top', fontsize=8,
            bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                      edgecolor='none', alpha=0.75))

    # Tick labels (shared axes: x visible only on bottom row, y only on left col)
    ax.tick_params(labelsize=8)
    ax.set_aspect('equal')

    # Centre the unit cell in the plot frame
    # ---------------------------------------
    # 1. Compute the bounding box of the unit cell outline
    cell_verts = corners[:-1]                     # drop the closing (0,0) vertex
    cell_min  = cell_verts.min(axis=0)
    cell_max  = cell_verts.max(axis=0)
    cell_ctr  = (cell_min + cell_max) / 2.0

    # 2. Compute the bounding box of all visible content (atoms + arrow tips)
    arrow_tips = np.column_stack([
        px + dx * arrow_scale,
        py + dy * arrow_scale,
    ])
    content = np.vstack([cell_verts, np.column_stack([px, py]), arrow_tips])
    c_min = content.min(axis=0)
    c_max = content.max(axis=0)

    # 3. The frame half-extent must fit both the cell and the content,
    #    measured from the cell centre.
    half_w = max(cell_ctr[0] - c_min[0], c_max[0] - cell_ctr[0])
    half_h = max(cell_ctr[1] - c_min[1], c_max[1] - cell_ctr[1])
    half_extent = max(half_w, half_h)            # square frame
    pad = 0.15 * half_extent

    # Use strictly identical limits on both axes so the y-axis scaling
    # exactly matches the x-axis scaling (essential for shared axes).
    x_lo = cell_ctr[0] - half_extent - pad
    x_hi = cell_ctr[0] + half_extent + pad
    y_lo = cell_ctr[1] - half_extent - pad
    y_hi = cell_ctr[1] + half_extent + pad
    lo = min(x_lo, y_lo)
    hi = max(x_hi, y_hi)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)

    # ── Done drawing on this subplot ──


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Plot phonon mode eigenvectors')
    parser.add_argument('--band-yaml', required=True, help='Path to band.yaml')
    parser.add_argument('--contcar', required=True, help='Path to CONTCAR')
    parser.add_argument('--output-dir', default='mode_plots', help='Output directory')
    parser.add_argument('--irreps', default=None, help='Path to irreps.yaml (optional)')
    parser.add_argument('--view', default='xy', choices=['xy', 'xz', 'yz'],
                        help='Projection plane (default: xy)')
    args = parser.parse_args()

    # Matplotlib mathtext (no LaTeX required)
    plt.rcParams.update({
        'text.usetex': False,
        'font.family': 'serif',
        'mathtext.fontset': 'cm',      # Computer Modern for math symbols
    })

    # Read inputs
    structure = read_contcar(args.contcar)
    freqs, eigvecs, natom = read_band_yaml_gamma(args.band_yaml)
    ir_map = read_irreps(args.irreps) if args.irreps else {}

    nmodes = len(freqs)
    os.makedirs(args.output_dir, exist_ok=True)

    # Layout: 2 rows × 3 columns for 6 modes, shared axes, no whitespace
    ncols = 3
    nrows = (nmodes + ncols - 1) // ncols

    # Compute figure size so each subplot box is naturally square in display
    # space.  With wspace=0, hspace=0 and subplots_adjust margins, the
    # per-column width and per-row height are:
    #   col_w = fig_w * (right - left) / ncols
    #   row_h = fig_h * (top - bottom) / nrows
    # For square subplots: col_w == row_h
    left, right, bottom, top = 0.06, 0.97, 0.08, 0.97
    sub_w = 2.1                         # desired subplot width  (inches)
    sub_h = sub_w                       # square
    fig_w = sub_w * ncols / (right - left)
    fig_h = sub_h * nrows / (top - bottom)

    fig, axes = plt.subplots(nrows, ncols, figsize=(fig_w, fig_h),
                              sharex=True, sharey=True,
                              gridspec_kw={'wspace': 0, 'hspace': 0})

    print(f"  Generating {nmodes} mode subplots …")

    for i in range(nmodes):
        row = i // ncols
        col = i % ncols
        ax = axes[row, col] if nrows > 1 else axes[col]
        ir_label = ir_map.get(i + 1, '')
        plot_mode(ax, structure, freqs, eigvecs, i, ir_label, view=args.view)

    # Hide any unused subplots
    for i in range(nmodes, nrows * ncols):
        row = i // ncols
        col = i % ncols
        ax = axes[row, col] if nrows > 1 else axes[col]
        ax.set_visible(False)

    # Axis labels only on the leftmost column and bottom row
    for i in range(nmodes):
        row = i // ncols
        col = i % ncols
        ax = axes[row, col] if nrows > 1 else axes[col]
        if col == 0:
            ax.set_ylabel('y (Å)', fontsize=9)
        if row == nrows - 1:
            ax.set_xlabel('x (Å)', fontsize=9)

    # Shared legend for the whole figure
    unique_sym = sorted(set(structure['symbols']))
    leg_handles = []
    for s in unique_sym:
        c = COLOR_MAP.get(s, plt.cm.tab10(hash(s) % 10))
        leg_handles.append(plt.Line2D([0], [0], marker='o', color='w',
                                      markerfacecolor=c, markersize=6, label=s))
    fig.legend(handles=leg_handles, fontsize=9, loc='lower center',
               ncol=len(unique_sym), frameon=True,
               bbox_to_anchor=(0.5, 1.02))

    # Tighten figure margins — no unnecessary whitespace
    fig.subplots_adjust(left=left, right=right, bottom=bottom, top=top,
                        wspace=0, hspace=0)

    # Save composite figure
    fname = os.path.join(args.output_dir, "phonon_modes.png")
    fig.savefig(fname, dpi=250, bbox_inches='tight', pad_inches=0.02)
    plt.close(fig)
    print(f"  Saved composite mode plot  →  {fname}")


if __name__ == '__main__':
    main()
