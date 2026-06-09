"""Raman pipeline utilities — split by concern.

Re-exports from submodules for backward compatibility with
``from util import ...`` imports.
"""

from .io import Tee, run_command, fmt_time, calc_duration, make_pipeline_excepthook
from .vasp import (
    check_vasp_convergence, check_dielectric_complete, count_ionic_steps,
    is_calculation_complete, is_vasprun_valid, check_no_selective_dynamics,
)
from .incar import build_incar_content, write_incar
from .kpoints import write_kpoints
from .config import load_config, merge_config, build_srun_args, split_srun_args
from .symlinks import update_wavecar_symlinks, update_chgcar_symlinks
from .status import (
    STEP_DESCRIPTIONS, STEP_HISTORY, TOTAL_STEPS,
    write_status, make_write_status, parse_resume_step,
    print_step_header, print_step_result,
)
from .phonopy import ensure_dim_in_conf, write_eigenvectors_conf
from .postproc import generate_kopia_script, inject_ramfile_energies

# ZBRENT retry — tightly coupled to pipeline flow, kept here for now
from .io import run_command as _rc
def run_relaxation_with_zbrent_retry(scf_dir, srun_args, vasp_binary, stage_label="", max_attempts=3):
    """Run VASP relaxation with automatic ZBRENT-crash retry."""
    for attempt in range(1, max_attempts + 1):
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
                continue
    print(f"  [relax] Max attempts ({max_attempts}) reached.")
    return False
