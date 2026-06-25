#!/usr/bin/env python3
"""
plot_dos.py  —  Density of States plot from VASP DOSCAR

Reads a VASP DOSCAR and plots the total density of states with LaTeX
formatting and a vertical line at the Fermi level.

Usage:
    python3 plot_dos.py --doscar path/to/DOSCAR --output path/to/dos.pdf

Dependencies: numpy, matplotlib (both in phonopy_env)
"""

import argparse
import os
import numpy as np
import matplotlib.pyplot as plt


def read_doscar(filepath):
    """Return (energies, dos, e_fermi) from a VASP DOSCAR.

    Parameters
    ----------
    filepath : str
        Path to VASP DOSCAR.

    Returns
    -------
    energies : (NEDOS,) ndarray  — energy grid (eV)
    dos      : (NEDOS,) ndarray  — total DOS (states/eV)
    e_fermi  : float             — Fermi level (eV)
    """
    with open(filepath) as f:
        lines = f.readlines()

    # Line 6 (0-indexed: line 5): E_max, E_min, NEDOS, E_fermi, ...
    header = lines[5].split()
    e_max = float(header[0])
    e_min = float(header[1])
    nedos = int(header[2])
    e_fermi = float(header[3])

    # Data lines start at line 6, run for nedos lines
    data = np.loadtxt(lines[6:6 + nedos], usecols=(0, 1))
    energies = data[:, 0]
    dos = data[:, 1]

    return energies, dos, e_fermi


def main():
    parser = argparse.ArgumentParser(
        description='Plot total density of states from VASP DOSCAR')
    parser.add_argument('--doscar', required=True,
                        help='Path to VASP DOSCAR')
    parser.add_argument('--output', default='dos.pdf',
                        help='Output file path  (default: dos.pdf)')
    parser.add_argument('--e-range', nargs=2, type=float, default=None,
                        metavar=('EMIN', 'EMAX'),
                        help='Energy window for the plot (eV, optional)')
    args = parser.parse_args()

    # ── Matplotlib mathtext (no LaTeX required) ───────────────────────────
    plt.rcParams.update({
        'text.usetex': False,
        'font.family': 'serif',
        'mathtext.fontset': 'cm',      # Computer Modern for math symbols
    })

    # ── Read data ─────────────────────────────────────────────────────────
    energies, dos, e_fermi = read_doscar(args.doscar)

    # ── Plot ──────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(5.5, 4.0))

    ax.plot(energies, dos, color='#2c7bb6', linewidth=1.2)
    ax.fill_between(energies, dos, alpha=0.12, color='#2c7bb6')

    # Fermi level reference line
    ax.axvline(e_fermi, color='#d62728', linestyle='--', linewidth=0.8,
               alpha=0.7, label=f'$E_{{\\mathrm{{F}}}}$ = ${e_fermi:.2f}\\,\\mathrm{{eV}}$')
    ax.legend(fontsize=9, loc='upper right', frameon=True)

    ax.set_xlabel(r'Energy (eV)', fontsize=11)
    ax.set_ylabel(r'DOS (states/eV)', fontsize=11)
    ax.tick_params(axis='both', which='major', labelsize=10,
                   direction='in', top=True, right=True)

    # Energy window
    if args.e_range:
        ax.set_xlim(args.e_range[0], args.e_range[1])
    else:
        # Default: show from -10 eV to +10 eV relative to E_F
        ax.set_xlim(e_fermi - 10, e_fermi + 10)

    # Shade the occupied region
    # (filled area from min(energy) to E_F at y=0)
    occupied = (energies <= e_fermi)
    ax.fill_between(energies[occupied], dos[occupied], alpha=0.06,
                    color='#d62728')

    plt.tight_layout()
    fig.savefig(args.output, dpi=300)
    plt.close(fig)
    print(f"  Saved DOS plot  →  {args.output}")
    print(f"    Energy range: {energies[0]:.1f} to {energies[-1]:.1f} eV")
    print(f"    Fermi level:  {e_fermi:.3f} eV")
    print(f"    NEDOS points: {len(energies)}")


if __name__ == '__main__':
    main()
