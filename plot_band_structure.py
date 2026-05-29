#!/usr/bin/env python3
"""
plot_band_structure.py  —  Phonon band structure with mode-type legend

Reads band.yaml and irreps.yaml, plots all bands coloured by their irreducible
representation at the Γ point, with LaTeX formatting and a legend identifying
each mode type.

Usage:
    python3 plot_band_structure.py \\
        --band-yaml path/to/band.yaml \\
        --irreps   path/to/irreps.yaml \\
        --output   path/to/band_structure.pdf

Dependencies: numpy, matplotlib, pyyaml (all in phonopy_env)
"""

import argparse
import os
import re
import yaml
import numpy as np
import matplotlib.pyplot as plt


# ── Colour map for mode irreps ──────────────────────────────────────────────
# Consistent palette so E' always looks the same across different plots.
IREP_COLORS = {
    "E'":   '#1f78b4',   # blue
    "E''":  '#33a02c',   # green
    "A1'":  '#e31a1c',   # red
    "A1''": '#ff7f00',   # orange
    "A2'":  '#6a3d9a',   # purple
    "A2''": '#b15928',   # brown
}
_DEFAULT_COLOR = '#888888'


def format_label_for_latex(label):
    """Convert plain-text irrep label to LaTeX math string."""
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


def read_band_yaml(filepath):
    """Return (distances, frequencies, tick_positions, tick_labels).

    distances : (nq,) array of cumulative path distances
    frequencies : (nq, nbands) array of frequencies in cm⁻¹
    tick_positions : x-coordinates for high-symmetry-point tick marks
    tick_labels : display names for those ticks (e.g. 'GAMMA' → :math:`\\Gamma`)
    """
    with open(filepath) as f:
        data = yaml.safe_load(f)

    phonon_data = data['phonon']
    nq = len(phonon_data)
    nbands = len(phonon_data[0]['band'])

    distances = np.array([q['distance'] for q in phonon_data])
    frequencies = np.zeros((nq, nbands))

    for i, q in enumerate(phonon_data):
        for j, b in enumerate(q['band']):
            frequencies[i, j] = b['frequency']   # stored in THz

    # ── Path labels and tick positions ───────────────────────────────────
    segments = data.get('labels', [])
    seg_nq = data.get('segment_nqpoint', [])
    tick_positions = []
    tick_labels = []

    if segments and seg_nq:
        # First tick at the start of the first segment
        tick_positions.append(distances[0])
        tick_labels.append(segments[0][0])

        # One tick at the end of each segment
        cum = 0
        for i, nq_seg in enumerate(seg_nq):
            cum += nq_seg - 1                    # last index of this segment
            tick_positions.append(distances[cum])
            tick_labels.append(segments[i][1])

    return distances, frequencies, tick_positions, tick_labels


def read_irreps(filepath):
    """Return dict mapping band_index (1-based) → ir_label.

    Also returns the point-group symbol (e.g. '-6m2') for reference.
    """
    if not os.path.exists(filepath):
        return {}, ''
    with open(filepath) as f:
        data = yaml.safe_load(f)
    mapping = {}
    for entry in data.get('normal_modes', []):
        label = entry.get('ir_label', '?')
        for idx in entry.get('band_indices', []):
            mapping[idx] = label
    point_group = data.get('point_group', '')
    return mapping, point_group


def main():
    parser = argparse.ArgumentParser(
        description='Plot phonon band structure with mode legend')
    parser.add_argument('--band-yaml', required=True,
                        help='Path to band.yaml from phonopy')
    parser.add_argument('--irreps', default=None,
                        help='Path to irreps.yaml (optional, but needed for legend)')
    parser.add_argument('--output', default='band_structure.pdf',
                        help='Output file path  (default: band_structure.pdf)')
    args = parser.parse_args()

    # ── Matplotlib mathtext (no LaTeX required) ──────────────────────────
    plt.rcParams.update({
        'text.usetex': False,
        'font.family': 'serif',
        'mathtext.fontset': 'cm',      # Computer Modern for math symbols
    })

    # ── Read data ────────────────────────────────────────────────────────
    distances, frequencies, tick_positions, tick_labels = \
        read_band_yaml(args.band_yaml)
    ir_map, point_group = read_irreps(args.irreps) if args.irreps else ({}, '')

    nq, nbands = frequencies.shape

    # Build a colour per band index (1-based) from its irrep at Γ
    band_colour = {}
    for bidx in range(1, nbands + 1):
        label = ir_map.get(bidx, '')
        band_colour[bidx] = IREP_COLORS.get(label, _DEFAULT_COLOR)

    # ── Plot ─────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(6.5, 4.5))

    for bidx in range(nbands):
        color = band_colour[bidx + 1]
        ax.plot(distances, frequencies[:, bidx], color=color, linewidth=1.0)

    # High-symmetry-point ticks
    ax.set_xticks(tick_positions)
    latex_tick_labels = []
    for lbl in tick_labels:
        if lbl.upper() == 'GAMMA':
            latex_tick_labels.append(r'$\Gamma$')
        else:
            latex_tick_labels.append(f'${lbl}$')
    ax.set_xticklabels(latex_tick_labels)

    # Vertical dashed lines at segment boundaries (excluding first/last)
    for pos in tick_positions[1:-1]:
        ax.axvline(pos, color='gray', linestyle='--', linewidth=0.5, alpha=0.5)

    ax.set_ylabel(r'Frequency (THz)', fontsize=11)
    ax.tick_params(axis='both', which='major', labelsize=10,
                   direction='in', top=True, right=True)

    # ── Legend: one entry per unique irrep ───────────────────────────────
    unique_irreps = sorted(set(ir_map.values()), key=_irrep_sort_key)
    if unique_irreps:
        handles = []
        for label in unique_irreps:
            color = IREP_COLORS.get(label, _DEFAULT_COLOR)
            latex_label = format_label_for_latex(label)
            handles.append(
                plt.Line2D([0], [0], color=color, linewidth=2.0,
                           label=latex_label))
        ax.legend(handles=handles, fontsize=10, loc='upper right',
                  frameon=True, title='Irrep',
                  title_fontsize=11)

    plt.tight_layout()
    fig.savefig(args.output, dpi=300)
    plt.close(fig)
    print(f"  Saved band structure  →  {args.output}")


def _irrep_sort_key(label):
    """Sort irreducible representations in a conventional ordering.

    Groups by the base letter (A before E before B …) and then by
    subscript number and prime type.
    """
    match = re.match(r"^([A-Za-z]+)(\d*)(['\"]*)$", label)
    if match:
        base, subscript, primes = match.groups()
        sub_num = int(subscript) if subscript else 0
        prime_order = {"'": 0, "''": 1, '"': 2}.get(primes, 3)
        return (base, sub_num, prime_order)
    return (label, 0, 0)


if __name__ == '__main__':
    main()
