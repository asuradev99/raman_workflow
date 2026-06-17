"""Raman pipeline utilities — split by concern.

Re-exports from submodules for backward compatibility with
``from util import ...`` imports.
"""

from .io import (Tee, run_command, fmt_time, calc_duration,
                 make_pipeline_excepthook, print_job_header,
                 do_restart_cleanup, require_path, require_file)
from .vasp import (
    check_vasp_convergence, check_dielectric_complete, count_ionic_steps,
    is_calculation_complete, is_vasprun_valid, check_no_selective_dynamics,
)
from .relax import run_relaxation
from .incar import build_incar_content, write_incar, write_kpoints, write_vasp_inputs
from .config import load_config, merge_config, get_srun_args, split_srun_args, validate_config
from .symlinks import update_hf_symlinks, update_raman_symlinks
from .status import (
    STEP_HISTORY, EXPECTED_LABELS,
    write_status, make_write_status, parse_resume_step,
    print_step_header, print_step_result, begin_step, finish_dispatch_step,
    set_expected_labels, relax_labels,
    RELAX_LABEL_SINGLE, RELAX_LABEL_DEFECT_1, RELAX_LABEL_DEFECT_2,
)
from .phonopy import ensure_dim_in_conf, write_eigenvectors_conf
from .postproc import generate_kopia_script, inject_ramfile_energies
from .vasp_loop import list_hf_dirs, run_vasp_in_dirs
