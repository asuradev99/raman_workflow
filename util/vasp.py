"""VASP output checks: completion, convergence, force analysis, dielectric."""

import os
import re

import numpy as np

from .io import run_command

# ── Optional py4vasp-core import (h5py only ever used alongside it below) ──
try:
    import h5py
    import py4vasp as _py4vasp
    from py4vasp.exception import FileAccessError as _Py4vaspFileAccessError
    _PY4VASP = True
except ImportError:
    _PY4VASP = False


def _h5_path(dirpath):
    """Return path to vaspout.h5 in *dirpath*, or None if absent."""
    p = os.path.join(dirpath, "vaspout.h5")
    return p if os.path.exists(p) else None


def is_vasprun_valid(filepath):
    """Check vasprun.xml is non-trivial and has ``</modeling>`` closing tag.

    Kept as a fallback for directories that do not have vaspout.h5.
    Prefer :func:`is_calculation_complete` for new code.
    """
    try:
        if not os.path.exists(filepath):
            return False
        size = os.path.getsize(filepath)
        if size <= 1000:
            return False
        # Check last 4 KB for closing tag
        with open(filepath, "rb") as f:
            if size > 4096:
                f.seek(-4096, 2)
            tail = f.read()
        return b"</modeling>" in tail
    except (IOError, OSError):
        return False


def is_calculation_complete(dirpath):
    """Return True if the VASP run in *dirpath* finished cleanly.

    Primary check: ``"General timing and accounting"`` in the tail of OUTCAR.
    This string is the very last thing VASP writes before exiting — if it is
    present, the run finished without crashing and all output files (vasprun.xml,
    vaspout.h5) are guaranteed to be complete and flushed.  No secondary check
    on HDF5 or XML is needed or safe to add: if py4vasp raises or vasprun.xml
    is absent, a secondary check would return False for a genuinely complete run.

    Fallback: if OUTCAR is absent (e.g. the directory was set up but VASP never
    started), fall back to the ``</modeling>`` tag in vasprun.xml so that
    pre-existing runs without an OUTCAR are still recognised.

    Note: completion ≠ convergence.  A NSW=0 run that hit NELM without SCF
    convergence still writes "General timing" and passes here.  SCF convergence
    is checked separately by :func:`check_vasp_convergence`.
    """
    outcar_path = os.path.join(dirpath, "OUTCAR")
    if os.path.isfile(outcar_path):
        try:
            size = os.path.getsize(outcar_path)
            with open(outcar_path, "rb") as f:
                if size > 4096:
                    f.seek(-4096, 2)
                tail = f.read().decode("utf-8", errors="ignore")
            return "General timing and accounting" in tail
        except (IOError, OSError):
            return False
    # No OUTCAR — fall back to vasprun.xml for legacy/unstarted dirs
    return is_vasprun_valid(os.path.join(dirpath, "vasprun.xml"))


def count_ionic_steps(dirpath):
    """Return the number of completed ionic steps.

    Reads ``run_info.num_ionic_steps`` from ``vaspout.h5`` via py4vasp-core when
    available; falls back to counting ``Iteration N(M)`` lines in OUTCAR.
    """
    if _PY4VASP and _h5_path(dirpath):
        try:
            calc = _py4vasp.Calculation.from_path(dirpath)
            return int(calc.run_info.read()["num_ionic_steps"])
        except Exception:
            pass
    # Fallback: OUTCAR regex
    outcar = os.path.join(dirpath, "OUTCAR")
    if not os.path.exists(outcar):
        return 0
    with open(outcar) as f:
        return sum(1 for line in f if re.match(r"\s+Iteration\s+\d+\(\s*\d+\)", line))


def check_no_selective_dynamics(filepath, context=""):
    """Guard: raise a RuntimeError if *filepath* contains VASP Selective Dynamics.

    Selective Dynamics (``T T F``) is only valid for the initial unit-cell
    relaxation (Step 3).  If it propagates into phonopy displacement or Raman
    POSCAR files, force constants or Raman tensors will be silently wrong.
    """
    if not os.path.exists(filepath):
        return  # let the caller decide what to do about missing files
    with open(filepath) as f:
        for i, line in enumerate(f, 1):
            if "selective" in line.lower():
                raise RuntimeError(
                    f"Selective Dynamics detected in {filepath} (line {i})"
                    + (f" — {context}" if context else "")
                )


def _build_atom_labels(hf, n_atoms):
    """Build human-readable atom labels from vaspout.h5 POSCAR data.

    Returns a list like ``['B_1', 'N_1']`` for a 2-atom unit cell or
    ``['B_1', 'B_2', ..., 'N_1', 'N_2', ...]`` for a supercell.
    Falls back to numbered indices if POSCAR data is absent.
    """
    try:
        ion_types_raw = hf["input/poscar/ion_types"][()]
        num_per_type  = hf["input/poscar/number_ion_types"][()]
        # Decode bytes → str; VASP stores as fixed-length byte strings
        symbols = []
        for raw in ion_types_raw:
            s = raw.tobytes().decode("utf-8").strip() if isinstance(raw, np.bytes_) else str(raw).strip()
            symbols.append(s)
        labels = []
        for sym, count in zip(symbols, num_per_type):
            for j in range(int(count)):
                labels.append(f"{sym}_{j + 1}")
        if len(labels) >= n_atoms:
            return labels[:n_atoms]
    except Exception:
        pass
    # Fallback: numbered atoms
    return [f"atom_{i + 1}" for i in range(n_atoms)]


def _print_force_table(prefix, forces, mags, labels, max_idx, max_f):
    """Print a compact per-atom force table to the log.

    For ≤12 atoms prints all rows; for larger systems prints the 5 atoms
    with highest forces plus summary statistics.
    """
    n = len(mags)
    # Sort by force magnitude descending
    order = np.argsort(mags)[::-1]

    if n <= 12:
        # Full table
        lines = [f"{prefix} Per-atom residual forces (eV/Å):",
                 f"{prefix}   {'Atom':>8s}  {'Fx':>10s}  {'Fy':>10s}  {'Fz':>10s}  {'|F|':>10s}",
                 f"{prefix}   " + "─" * 55]
        for i in range(n):
            fx, fy, fz = forces[i]
            flag = " ← MAX" if i == max_idx else ""
            lines.append(
                f"{prefix}   {labels[i]:>8s}  {fx:10.6f}  {fy:10.6f}  "
                f"{fz:10.6f}  {mags[i]:10.6f}{flag}"
            )
        print("\n".join(lines))
    else:
        # Large system: top-5 only
        lines = [f"{prefix} Residual forces — top 5 of {n} atoms (eV/Å):",
                 f"{prefix}   {'Atom':>8s}  {'Fx':>10s}  {'Fy':>10s}  {'Fz':>10s}  {'|F|':>10s}",
                 f"{prefix}   " + "─" * 55]
        for k in range(min(5, n)):
            i = order[k]
            fx, fy, fz = forces[i]
            flag = " ← MAX" if i == max_idx else ""
            lines.append(
                f"{prefix}   {labels[i]:>8s}  {fx:10.6f}  {fy:10.6f}  "
                f"{fz:10.6f}  {mags[i]:10.6f}{flag}"
            )
        # Summary stats
        mean_f = float(np.mean(mags))
        median_f = float(np.median(mags))
        lines.append(f"{prefix}   " + "─" * 55)
        lines.append(f"{prefix}   max={max_f:.6f}  mean={mean_f:.6f}  median={median_f:.6f}  "
                     f"({n} atoms total)")
        print("\n".join(lines))


def _has_zbrent_error(stdout_path):
    """Return True if relaxation.stdout contains a ZBRENT fatal error."""
    if not os.path.exists(stdout_path):
        return False
    try:
        with open(stdout_path) as f:
            return "ZBRENT: fatal error in bracketing" in f.read()
    except OSError:
        return False


def _extract_max_force(outcar_path):
    """Return max force magnitude (eV/Å) from the last ionic step in OUTCAR, or None."""
    if not os.path.exists(outcar_path):
        return None
    try:
        with open(outcar_path) as f:
            content = f.read()
        blocks = re.findall(
            r"TOTAL-FORCE \(eV/Angst\)\n\s*-+\n(.*?)\n\s*-+",
            content, re.DOTALL
        )
        if not blocks:
            return None
        lines = [l.split() for l in blocks[-1].strip().split("\n") if len(l.split()) >= 6]
        if not lines:
            return None
        return max(
            (float(p[3])**2 + float(p[4])**2 + float(p[5])**2)**0.5
            for p in lines
        )
    except Exception:
        return None


def check_vasp_convergence(outcar_dir, stage_label=""):
    """Check that VASP completed and converged in *outcar_dir*.

    Primary path: reads ``vaspout.h5`` via py4vasp-core — checks
    ``run_info.num_ionic_steps`` for completion and compares the final
    ``force[-1]`` magnitude against ``EDIFFG`` from the INCAR block.

    Fallback: greps OUTCAR for the ``"General timing and accounting"`` footer
    and the ionic/electronic convergence strings.
    """
    prefix = f"  [vasp:{stage_label}]" if stage_label else "  [vasp]"

    # ── Primary: py4vasp-core + HDF5 ─────────────────────────────────────────
    if _PY4VASP and _h5_path(outcar_dir):
        try:
            calc = _py4vasp.Calculation.from_path(outcar_dir)
            n_steps = int(calc.run_info.read()["num_ionic_steps"])

            if n_steps == 0:
                raise RuntimeError(
                    f"{prefix} vaspout.h5 contains no ionic steps — "
                    f"VASP crashed before writing any output."
                )

            # All HDF5 reads — including _build_atom_labels(hf, ...) — must
            # happen inside this block. hf is closed the moment the `with`
            # exits, and h5py silently degrades rather than erroring on a
            # closed handle (_build_atom_labels catches everything and falls
            # back to generic atom_N labels), so a handle used outside this
            # block doesn't crash — it just quietly loses real atom labels.
            with h5py.File(_h5_path(outcar_dir), "r") as hf:
                nsw    = int(hf["input/incar/NSW"][()])
                ediffg = float(hf["input/incar/EDIFFG"][()])

                if nsw == 0:
                    # Static run — check SCF convergence via OUTCAR.
                    # Force convergence (EDIFFG) is not meaningful for NSW=0;
                    # the right criterion is electronic: VASP writes
                    # "aborting loop because EDIFF is reached" when the SCF
                    # converges within NELM iterations.  Absence of that string
                    # (with "General timing" confirmed) means NELM was exhausted
                    # without convergence → forces in vasprun.xml are unreliable.
                    _outcar = os.path.join(outcar_dir, "OUTCAR")
                    if os.path.exists(_outcar):
                        _sz = os.path.getsize(_outcar)
                        with open(_outcar, "rb") as _fh:
                            if _sz > 131072:
                                _fh.seek(-131072, 2)
                            _tail = _fh.read().decode("utf-8", errors="ignore")
                        if "aborting loop because EDIFF is reached" in _tail:
                            print(f"{prefix} VASP static SCF converged "
                                  f"({n_steps} ionic step, EDIFF reached)")
                            return
                        raise RuntimeError(
                            f"{prefix} VASP static run finished but SCF did NOT "
                            f"converge (no 'aborting loop because EDIFF is reached' "
                            f"in OUTCAR — NELM likely reached without convergence). "
                            f"Increase NELM in the hf INCAR template, delete this "
                            f"directory's OUTCAR to trigger a rerun, and retry."
                        )
                    # OUTCAR absent — fall through to force check as best effort
                    print(f"{prefix} VASP static run complete ({n_steps} step) "
                          f"— OUTCAR missing, skipping SCF convergence check")
                    return

                f_last = calc.force[-1].read()["forces"]
                f_mags = np.linalg.norm(f_last, axis=1)
                max_f  = float(np.max(f_mags))
                max_idx = int(np.argmax(f_mags))
                tol    = abs(ediffg)

                atom_labels = _build_atom_labels(hf, n_atoms=len(f_mags))

            converged = max_f <= tol
            status = "converged" if converged else "NOT converged"
            print(f"{prefix} VASP {status}: max |F| = {max_f:.6f} eV/Å "
                  f"{'≤' if converged else '>'} EDIFFG = {tol} "
                  f"after {n_steps}/{nsw} step(s) "
                  f"(worst atom: {atom_labels[max_idx]})")

            _print_force_table(prefix, f_last, f_mags, atom_labels, max_idx, max_f)

            if not converged:
                raise RuntimeError(
                    f"{prefix} VASP not converged — max |F| = {max_f:.6f} eV/Å "
                    f"> EDIFFG = {tol} after {n_steps}/{nsw} steps. NSW may be too small."
                )
            return

        except RuntimeError:
            raise
        except _Py4vaspFileAccessError:
            pass
        except Exception as e:
            print(f"{prefix} [py4vasp check failed: {e}] — falling back to OUTCAR")

    # ── Fallback: OUTCAR grep ─────────────────────────────────────────────────
    outcar_path = os.path.join(outcar_dir, "OUTCAR")
    if not os.path.exists(outcar_path):
        raise RuntimeError(
            f"{prefix} OUTCAR not found at {outcar_path} — "
            f"VASP likely did not run or crashed immediately."
        )
    with open(outcar_path) as f:
        content = f.read()
    if "General timing and accounting" not in content:
        stdout_path = os.path.join(outcar_dir, "relaxation.stdout")
        if _has_zbrent_error(stdout_path):
            max_f = _extract_max_force(outcar_path)
            if max_f is not None and max_f < 0.01:
                print(f"{prefix} ZBRENT error — forces converged "
                      f"(max |F| = {max_f:.6f} eV/Å). Continuing.")
                return
        raise RuntimeError(
            f"{prefix} VASP did not complete normally "
            f"(no 'General timing and accounting' footer in OUTCAR)."
        )
    # Read NSW from INCAR so static and relaxation runs use the right criterion
    _nsw_fb = None
    _incar_fb = os.path.join(outcar_dir, "INCAR")
    if os.path.exists(_incar_fb):
        with open(_incar_fb) as _f:
            for _ln in _f:
                _m = re.match(r"^\s*NSW\s*=\s*(\d+)", _ln, re.IGNORECASE)
                if _m:
                    _nsw_fb = int(_m.group(1))
                    break

    if _nsw_fb == 0:
        # Static run: "aborting loop because EDIFF is reached" IS the success signal
        if "aborting loop because EDIFF is reached" in content:
            print(f"{prefix} VASP static SCF converged (EDIFF reached)")
        else:
            print(f"{prefix} WARNING: VASP static SCF did NOT converge "
                  f"(no 'aborting loop because EDIFF is reached' in OUTCAR).")
            raise RuntimeError(
                f"{prefix} VASP static SCF did not converge — NELM likely reached "
                f"without convergence. Increase NELM in the hf INCAR template."
            )
    elif "reached required accuracy" in content:
        print(f"{prefix} VASP converged successfully (ionic)")
    elif "aborting loop because EDIFF is reached" in content:
        print(f"{prefix} WARNING: electronic convergence only — "
              f"VASP may not be ionically converged.")
        raise RuntimeError(
            f"{prefix} VASP stopped: electronic convergence but no ionic convergence signal. "
            f"NSW may be too small or EDIFFG too tight."
        )
    else:
        print(f"{prefix} WARNING: VASP did NOT reach convergence "
              f"(no convergence signal found).")
        raise RuntimeError(
            f"{prefix} VASP did not reach convergence — no convergence signal in OUTCAR."
        )


def check_dielectric_complete(dirpath, stage_label=""):
    """Verify that a LOPTICS run wrote non-zero dielectric tensor data."""
    if not _PY4VASP or not _h5_path(dirpath):
        return

    prefix = f"  [vasp:{stage_label}]" if stage_label else "  [vasp]"
    try:
        with h5py.File(_h5_path(dirpath), "r") as hf:
            if "results/linear_response" not in hf:
                return

        calc = _py4vasp.Calculation.from_path(dirpath)
        diel = calc.dielectric_function.read()
        eps  = diel["dielectric_function"]

        if not np.any(np.abs(eps.imag) > 1e-10):
            raise RuntimeError(
                f"{prefix} Dielectric tensor imaginary part is zero in {dirpath}. "
                f"LOPTICS calculation produced no optical response — "
                f"check VASP INCAR (LOPTICS = .TRUE. required) and rerun Step 14."
            )
        print(f"{prefix} Dielectric tensor OK: shape {eps.shape}, "
              f"max |Im(ε)| = {float(np.max(np.abs(eps.imag))):.4f}")
    except RuntimeError:
        raise
    except _Py4vaspFileAccessError:
        pass
    except Exception as e:
        print(f"{prefix} WARNING: could not verify dielectric data ({e})")
    return False
