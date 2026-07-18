#!/usr/bin/env python3
"""Verify a LOPTICS run produced a non-zero imaginary dielectric response.

The one check bash can't do — the dielectric tensor lives in vaspout.h5, so this
needs py4vasp/h5py. Ported from util/vasp.py:check_dielectric_complete.

Usage:
    python3 check_dielectric.py <ra_pos_dir>

Exit 0 = Im(ε) present (or the tools/data aren't available to check, in which
case we don't block). Exit 1 = the run completed but Im(ε) is identically zero,
i.e. LOPTICS produced no optical response (the OOM/failed-LOPTICS failure mode).
"""
import os
import sys


def main():
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__)
        sys.exit(2)
    d = sys.argv[1]

    h5 = os.path.join(d, "vaspout.h5")
    if not os.path.exists(h5):
        # Nothing to check against; don't block the pipeline on a missing HDF5.
        sys.exit(0)

    try:
        import h5py
        import numpy as np
        import py4vasp
    except ImportError:
        sys.stderr.write(
            "WARNING: py4vasp/h5py/numpy unavailable; skipping dielectric check\n"
        )
        sys.exit(0)

    try:
        with h5py.File(h5, "r") as hf:
            if "results/linear_response" not in hf:
                # No linear-response block written — can't verify; don't block.
                sys.exit(0)

        calc = py4vasp.Calculation.from_path(d)
        eps = calc.dielectric_function.read()["dielectric_function"]

        if not np.any(np.abs(eps.imag) > 1e-10):
            sys.stderr.write(
                f"NO DIELECTRIC RESPONSE: Im(ε) is identically zero in {d}. "
                f"LOPTICS produced no optical response (check LOPTICS=.TRUE. and rerun).\n"
            )
            sys.exit(1)

        print(f"dielectric OK: max |Im(ε)| = {float(np.max(np.abs(eps.imag))):.4f}")
        sys.exit(0)
    except Exception as e:
        # Couldn't verify (unexpected data layout, py4vasp version, etc.) — warn,
        # don't block, matching the old checker's non-fatal fallback.
        sys.stderr.write(f"WARNING: could not verify dielectric data ({e})\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
