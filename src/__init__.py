"""Pipeline step dispatch — Step registry and PipelineContext."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from . import (
    scf_relax, supercell, hf_setup, force_constants,
    phonon_post, raman_prep, resonant_vasp, post_process,
)
from util.status import relax_labels, RELAX_LABEL_DEFECT_2_CPU

# Steps that must complete before per-directory parallel dispatch begins.
# In sbatch_parallel these run inside a salloc; in sbatch_mix they run in
# a dedicated Phase 1 sbatch job.  Everything after these is dispatched as
# concurrent per-dir srun/sbatch calls.
PRE_DISPATCH_STEP_NAMES = frozenset({
    "scf_relax", "supercell", "hf_setup",
    "defect_relax_1", "defect_relax_2", "defect_relax_2_cpu",
})


@dataclass(frozen=True)
class Step:
    """Immutable descriptor for one pipeline step.

    Step *numbers* are never part of a Step's identity — they're purely a
    cosmetic, 1-based display position computed fresh at render time from
    where this step's label(s) fall in EXPECTED_LABELS (see util/status.py).
    All resume/completion logic keys off `labels` instead.

    `labels` is either a static list (steps with exactly one label) or a
    callable `(config, start_from_supercell) -> list[str]` for steps whose
    label(s) depend on config — currently only the relax step, which uses
    one label normally or two for the defect two-stage relax.

    `_is_complete` is an optional `(work_dir, config) -> bool` function used
    for file-based resume: if it returns True the step is skipped without
    consulting workflow.log. None means "always run".
    """
    name: str           # stable slug used in dispatch log lines + salloc boundary checks
    labels: Any          # list[str] OR Callable[[dict, bool], list[str]]
    _run: Callable[[PipelineContext], None]
    _is_complete: Any = None   # Callable[[str, dict], bool] | None

    def run(self, ctx: PipelineContext) -> None:
        self._run(ctx)

    def resolved_labels(self, config: dict, start_from_supercell: bool) -> list:
        if callable(self.labels):
            return self.labels(config, start_from_supercell)
        return list(self.labels)


PIPELINE: list[Step] = [
    Step("scf_relax",     relax_labels,                            scf_relax.run,         scf_relax.is_complete),
    Step("supercell",     ["Supercell generation + relaxation"],   supercell.run,         supercell.is_complete),
    Step("hf_setup",      ["hf/ directory setup"],                 hf_setup.run,          hf_setup.is_complete),
    Step("force_consts",  ["VASP force constants"],                force_constants.run,   force_constants.is_complete),
    Step("phonon_post",   ["Phonon postprocessing"],               phonon_post.run,       phonon_post.is_complete),
    Step("raman_prep",    ["Raman setup + displacements"],         raman_prep.run,        raman_prep.is_complete),
    Step("resonant_vasp", ["Resonant VASP (dielectric)"],          resonant_vasp.run,     resonant_vasp.is_complete),
    Step("post_process",  ["Post-processing + output"],            post_process.run,      post_process.is_complete),
]


STEP_REGISTRY: dict = {s.name: s for s in PIPELINE}
STEP_REGISTRY.update({
    "defect_relax_1": Step(
        "defect_relax_1",
        ["Defect relax 1 (lattice fixed)"],
        scf_relax.run_defect_1,
        scf_relax.is_complete_defect_1,
    ),
    "defect_relax_2": Step(
        "defect_relax_2",
        ["Defect relax 2 (full)"],
        scf_relax.run_defect_2,
        scf_relax.is_complete_defect_2,
    ),
    "defect_relax_2_cpu": Step(
        "defect_relax_2_cpu",
        [RELAX_LABEL_DEFECT_2_CPU],
        scf_relax.run_defect_2_cpu,
        scf_relax.is_complete_defect_2,
    ),
})


def expected_labels(config: dict, start_from_supercell: bool) -> list:
    """Full ordered label list for this material's config.

    Used to seed the status table (including not-yet-started rows) and as
    the canonical resume sequence — the single source of truth for "what
    are all the steps and what order do they run in."
    """
    labels: list = []
    for step in PIPELINE:
        labels.extend(step.resolved_labels(config, start_from_supercell))
    return labels



@dataclass
class PipelineContext:
    """Typed context passed to every pipeline step.

    All YAML-derived fields are extracted in ``__post_init__`` so step modules
    never touch the raw config dict for scalar lookups.
    """

    # ── Required constructor arguments ───────────────────────────────────────
    raw_config: dict
    material_dir: str
    material_name: str
    work_dir: str
    srun_args: str
    vasp_binary: str
    hffiles_dir: str
    raman_dir: str
    script_dir: str
    binary_utilities_dir: str
    cpu_flag: bool
    scratch_flag: bool
    run_relaxation: Any   # Callable — typed as Any to avoid circular import with util
    write_status: Any     # Callable
    inside_salloc: bool = False

    # ── Derived from raw_config (populated in __post_init__) ─────────────────
    system_paths: dict = field(init=False)
    compute_mode: str = field(init=False)
    phonopy_dim: str = field(init=False)
    phonopy_amplitude: Any = field(init=False)
    scf_kpoints_mesh: str = field(init=False)
    scf_kpoints_shift: str = field(init=False)
    sup_relax_kpoints_mesh: str = field(init=False)
    sup_relax_kpoints_shift: str = field(init=False)
    hf_kpoints_mesh: str = field(init=False)    # owned by force_consts step
    hf_kpoints_shift: str = field(init=False)
    raman_kpoints_mesh: str = field(init=False) # owned by resonant_vasp step
    raman_kpoints_shift: str = field(init=False)
    defect_kpoints_mesh: str = field(init=False)  # shared by defect_relax_1/2 steps
    defect_kpoints_shift: str = field(init=False)
    desired_energies: list = field(init=False)
    raman_incident_pol: str = field(init=False)
    raman_scattered_pol: str = field(init=False)
    raman_surface_normal: str = field(init=False)
    vasp_max_restarts: int = field(init=False)
    hf_parallel: bool = field(init=False)
    vasp_srun_per_dir: str = field(init=False)
    vasp_sbatch_per_dir: str = field(init=False)
    vasp_gpus_per_dir: int = field(init=False)
    salloc_relax: str = field(init=False)
    salloc_per_dir: str = field(init=False)
    start_from_supercell: bool = field(init=False)
    cpu_relax_srun_args: str = field(init=False)
    cpu_relax_vasp_binary: str = field(init=False)
    cpu_relax_setup_cmd: str = field(init=False)
    eigvec_band_path: str = field(init=False)
    eigvec_band_labels: str = field(init=False)
    eigvec_band_points: Any = field(init=False)
    viz_enabled: bool = field(init=False)
    viz_scale_factor: float = field(init=False)
    viz_output_format: str = field(init=False)
    viz_vesta_template: str = field(init=False)
    broadening_mode: int = field(init=False)
    broadening_hwhm: Any = field(init=False)
    broadening_interpolation: int = field(init=False)
    broadening_normalization: int = field(init=False)

    # ── Mutable dispatch state (set by the dispatch loop, not at construction) ─
    # The label (description string) of the step currently being dispatched —
    # NOT a number. Step modules pass this straight through to write_status()/
    # print_step_header()/print_step_result(), which key everything off labels.
    current_label: str = field(default="", init=False)

    def __post_init__(self) -> None:
        cfg = self.raw_config
        c_mode = cfg.get("compute_mode", "interactive_manual")
        mode_cfg = cfg.get("compute_modes", {}).get(c_mode, {})

        def _step_kp(step, key, default=""):
            return cfg.get("steps", {}).get(step, {}).get("kpoints", {}).get(key, default)

        self.compute_mode = c_mode
        self.system_paths = cfg.get("system_paths", {})
        self.phonopy_dim = cfg["phonopy"]["dim"]
        self.phonopy_amplitude = cfg["phonopy"]["amplitude"]
        self.scf_kpoints_mesh        = _step_kp("scf_relax",    "mesh")
        self.scf_kpoints_shift       = _step_kp("scf_relax",    "shift", "0 0 0")
        self.sup_relax_kpoints_mesh  = _step_kp("supercell",    "mesh")
        self.sup_relax_kpoints_shift = _step_kp("supercell",    "shift", "0 0 0")
        self.hf_kpoints_mesh         = _step_kp("force_consts", "mesh")
        self.hf_kpoints_shift        = _step_kp("force_consts", "shift", "0 0 0")
        self.raman_kpoints_mesh      = _step_kp("resonant_vasp","mesh")
        self.raman_kpoints_shift     = _step_kp("resonant_vasp","shift", "0 0 0")
        self.defect_kpoints_mesh     = (_step_kp("defect_relax_1", "mesh")
                                        or _step_kp("scf_relax", "mesh"))
        self.defect_kpoints_shift    = (_step_kp("defect_relax_1", "shift", "")
                                        or _step_kp("scf_relax", "shift", "0 0 0"))
        _post = cfg.get("steps", {}).get("post_process", {})
        self.desired_energies = _post.get("desired_energies", [])
        _rt = _post.get("raman_tensor", {})
        self.raman_incident_pol  = _rt.get("incident_polarization",  "1.0 0.0 0.0")
        self.raman_scattered_pol = _rt.get("scattered_polarization", "1.0 0.0 0.0")
        self.raman_surface_normal = _rt.get("surface_normal", "z")
        self.vasp_max_restarts = cfg["vasp_loop"]["max_restarts"]
        self.hf_parallel = cfg.get("hf_parallel", False)
        self.vasp_srun_per_dir = mode_cfg.get("srun_per_dir", "")
        self.vasp_sbatch_per_dir = mode_cfg.get("sbatch_per_dir", "")
        import re as _re
        _gpn = _re.search(r'--gpus-per-node[=\s]+(\d+)', self.vasp_srun_per_dir)
        self.vasp_gpus_per_dir = int(_gpn.group(1)) if _gpn else mode_cfg.get("gpus_per_dir", 4)
        self.salloc_relax = mode_cfg.get("salloc") or mode_cfg.get("salloc_relax", "")
        self.salloc_per_dir = mode_cfg.get("salloc") or mode_cfg.get("salloc_per_dir", "")
        self.start_from_supercell = cfg.get("start_from_supercell", False)
        _cr = cfg.get("steps", {}).get("defect_relax_2_cpu", {}).get("cpu_relax", {})
        self.cpu_relax_srun_args   = mode_cfg.get("srun_cpu_relax", "")
        self.cpu_relax_vasp_binary = _cr.get("vasp_binary", "")
        _parts = []
        if _cr.get("vasp_modules"):
            _parts.append(f"module load {_cr['vasp_modules']} 2>/dev/null")
        _omp = _cr.get("omp_env", {})
        for k, v in _omp.items():
            _parts.append(f"export {k}={v}")
        self.cpu_relax_setup_cmd = " && ".join(_parts) if _parts else ""
        _phonon_post = cfg.get("steps", {}).get("phonon_post", {})
        _eigvec = _phonon_post.get("eigenvectors_band", {})
        self.eigvec_band_path   = _eigvec.get("path", "0.0 0.0 0.0  0.0 0.0 0.0")
        self.eigvec_band_labels = _eigvec.get("labels", "GAMMA GAMMA")
        self.eigvec_band_points = _eigvec.get("points", 1)
        _viz = _phonon_post.get("visualization", {})
        self.viz_enabled        = _viz.get("enabled", False)
        self.viz_scale_factor   = float(_viz.get("scale_factor", 0.5))
        self.viz_output_format  = _viz.get("output_format", "vesta").lower()
        self.viz_vesta_template = _viz.get("vesta_template", "template.vesta")
        _broadening = cfg.get("steps", {}).get("post_process", {}).get("broadening", {})
        self.broadening_mode          = _broadening.get("mode", 2)
        self.broadening_hwhm          = _broadening.get("hwhm", 1)
        self.broadening_interpolation = _broadening.get("interpolation", 200)
        self.broadening_normalization = _broadening.get("normalization", 2)

    @property
    def config(self) -> dict:
        """Raw merged config dict — for utility functions that accept the full config."""
        return self.raw_config
