"""Raman pipeline utilities — split by concern.

Re-exports from submodules for backward compatibility with
``from util import ...`` imports.
"""

from .io import Tee, run_command, fmt_time, calc_duration, make_pipeline_excepthook, print_job_header
from .vasp import (
    check_vasp_convergence, check_dielectric_complete, count_ionic_steps,
    is_calculation_complete, is_vasprun_valid, check_no_selective_dynamics,
    _has_zbrent_error, _extract_max_force,
)
from .incar import build_incar_content, write_incar
from .kpoints import write_kpoints
from .config import load_config, merge_config, build_srun_args, split_srun_args, validate_config
from .symlinks import update_wavecar_symlinks, update_chgcar_symlinks
from .status import (
    STEP_DESCRIPTIONS, STEP_HISTORY, TOTAL_STEPS,
    write_status, make_write_status, parse_resume_step,
    print_step_header, print_step_result,
)
from .phonopy import ensure_dim_in_conf, write_eigenvectors_conf
from .postproc import generate_kopia_script, inject_ramfile_energies
from .vasp_loop import run_hf_loop

# ZBRENT retry — kept here since it's used by the main pipeline
import os as _os
import shutil as _shutil
from .io import run_command as _rc
from .vasp import check_vasp_convergence, _has_zbrent_error, _extract_max_force


def run_relaxation_with_zbrent_retry(scf_dir, srun_args, vasp_binary, stage_label="", max_attempts=3):
    """Run VASP relaxation with automatic ZBRENT-crash retry.

    Before each attempt: removes stale OUTCAR so the OUTCAR-fallback convergence
    check does not read a previous run's data.  On a ZBRENT error where forces are
    already small (max |F| < 0.01 eV/Å), copies CONTCAR → POSCAR so the next
    attempt resumes from the near-converged geometry instead of starting over.
    """
    stdout_path = _os.path.join(scf_dir, "relaxation.stdout")
    outcar_path = _os.path.join(scf_dir, "OUTCAR")
    contcar_path = _os.path.join(scf_dir, "CONTCAR")
    poscar_path = _os.path.join(scf_dir, "POSCAR")

    for attempt in range(1, max_attempts + 1):
        # Remove stale OUTCAR so convergence check reads this run only
        if _os.path.exists(outcar_path):
            _os.remove(outcar_path)

        print(f"\n  [relax] Attempt {attempt}/{max_attempts}...")
        _rc(
            f"srun {srun_args} {vasp_binary} > relaxation.stdout",
            cwd=scf_dir,
            check_success=False,
        )
        try:
            check_vasp_convergence(scf_dir, stage_label)
            print(f"  [relax] Relaxation succeeded on attempt {attempt}.")
            return True
        except RuntimeError as e:
            print(f"  [relax] Attempt {attempt} failed: {e}")
            if attempt < max_attempts:
                if _has_zbrent_error(stdout_path):
                    max_f = _extract_max_force(outcar_path)
                    if max_f is not None and max_f < 0.01:
                        _shutil.copy(contcar_path, poscar_path)
                        print(f"  [zbrent] Forces converged (max |F| = {max_f:.6f} eV/Å). "
                              f"Copied CONTCAR → POSCAR for retry.")
                    else:
                        print(f"  [zbrent] ZBRENT error detected "
                              f"(max |F| = {max_f:.6f} eV/Å — not yet converged).")
                continue
    print(f"  [relax] Max attempts ({max_attempts}) reached.")
    return False
