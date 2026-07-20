#!/usr/bin/env python3
"""Rigorous, dependency-free VASP convergence check by parsing OUTCAR text.

Replaces util/vasp.py's py4vasp/h5py stack. No HDF5 — pure text parsing, so it
runs anywhere python3 does.

Usage:
    python3 check_convergence.py [--static | --relax] <dir>

    --static  (default)  displacement / SCF-only dir (NSW=0): checks completion,
                         electronic convergence, no NELM-exhaustion, no fatal errors.
                         Skips the ionic force check (forces are the *output*).
    --relax              relaxation dir (NSW>0): all of the above PLUS ionic
                         convergence — 'reached required accuracy' AND numeric
                         max|F| <= |EDIFFG| from the last TOTAL-FORCE block.

Exit 0 = converged. Nonzero = not converged; a one-line reason is printed to
stderr. Reads NELM / NSW / EDIFFG from the INCAR in <dir> (falling back to VASP
defaults) so it is usable by hand as well as from the generated bash.
"""
import os
import re
import sys

FATAL_MARKERS = (
    "ZBRENT: fatal error in bracketing",
    "Error EDDDAV",
    "VERY BAD NEWS",
    "Sub-Space-Matrix is not hermitian",
    "internal error",
)


def _fail(msg):
    sys.stderr.write(f"NOT CONVERGED: {msg}\n")
    sys.exit(1)


def _read_incar_tag(incar_path, tag, default):
    """Return the value of an INCAR tag (int/float) or *default* if absent."""
    if not os.path.exists(incar_path):
        return default
    try:
        with open(incar_path) as f:
            for line in f:
                line = line.split("#", 1)[0].split("!", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip().upper() == tag.upper():
                    val = val.strip().split()[0]
                    try:
                        return int(val)
                    except ValueError:
                        return float(val)
    except OSError:
        pass
    return default


def _max_force(content):
    """Max |F| (eV/Å) from the last TOTAL-FORCE block, or None. Ported from
    util/vasp.py:_extract_max_force verbatim (same regex)."""
    blocks = re.findall(
        r"TOTAL-FORCE \(eV/Angst\)\n\s*-+\n(.*?)\n\s*-+", content, re.DOTALL
    )
    if not blocks:
        return None
    rows = [l.split() for l in blocks[-1].strip().split("\n") if len(l.split()) >= 6]
    if not rows:
        return None
    return max(
        (float(p[3]) ** 2 + float(p[4]) ** 2 + float(p[5]) ** 2) ** 0.5 for p in rows
    )


def main():
    args = sys.argv[1:]
    mode = "static"
    rest = []
    for a in args:
        if a == "--static":
            mode = "static"
        elif a == "--relax":
            mode = "relax"
        else:
            rest.append(a)
    if len(rest) != 1:
        sys.stderr.write(__doc__)
        sys.exit(2)
    d = rest[0]

    outcar = os.path.join(d, "OUTCAR")

    # 1. OUTCAR exists and non-empty
    if not os.path.exists(outcar) or os.path.getsize(outcar) == 0:
        _fail(f"{outcar} missing or empty")

    with open(outcar, errors="ignore") as f:
        content = f.read()

    # 2. Run finished (full-file search — the VASP 6.4.3 GPU trailing profiling
    #    section makes a tail-only check unreliable)
    if "General timing and accounting" not in content:
        _fail("run did not finish ('General timing and accounting' absent)")

    # 3. No fatal VASP error markers
    for marker in FATAL_MARKERS:
        if marker in content:
            _fail(f"fatal VASP error: '{marker}'")

    # 4. Electronic convergence — the SCF loop reached EDIFF at least once.
    #    Fatal if absent: this is the NSW=0 case that can silently poison FORCE_SETS.
    if "aborting loop because EDIFF is reached" not in content:
        _fail("electronic SCF did not converge ('EDIFF is reached' absent)")

    # 5. NELM not exhausted in the last ionic step. Count 'Iteration <ionic>(<elec>)'
    #    markers; the final ionic step's electronic-iteration count must be < NELM.
    nelm = _read_incar_tag(os.path.join(d, "INCAR"), "NELM", 60)
    iters = re.findall(r"Iteration\s+(\d+)\(\s*(\d+)\)", content)
    if iters:
        last_ionic = iters[-1][0]
        elec_in_last = max(
            int(e) for (i, e) in iters if i == last_ionic
        )
        if elec_in_last >= nelm:
            _fail(f"SCF hit NELM={nelm} in the last ionic step (not converged)")

    # 6. Ionic convergence — relaxations only.
    if mode == "relax":
        nsw = _read_incar_tag(os.path.join(d, "INCAR"), "NSW", 0)
        if nsw and nsw > 0:
            if "reached required accuracy" not in content:
                _fail("ionic relaxation did not reach required accuracy")
            ediffg = _read_incar_tag(os.path.join(d, "INCAR"), "EDIFFG", None)
            if ediffg is not None and ediffg < 0:  # force-based criterion
                fmax = _max_force(content)
                if fmax is None:
                    _fail("could not extract forces to verify EDIFFG")
                if fmax > abs(ediffg):
                    _fail(f"max|F|={fmax:.4f} > |EDIFFG|={abs(ediffg):.4f} eV/Å")

    sys.exit(0)


if __name__ == "__main__":
    main()
