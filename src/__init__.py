"""Pipeline step dispatch — Step registry and PipelineContext."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from . import (
    scf_relax, supercell, hf_setup, force_constants,
    phonon_post, raman_prep, resonant_vasp, post_process,
)


@dataclass(frozen=True)
class Step:
    """Immutable descriptor for one pipeline step."""
    number: int
    name: str          # slug used in dispatch log lines
    description: str   # human-readable label
    _run: Callable[[PipelineContext], None]

    def run(self, ctx: PipelineContext) -> None:
        self._run(ctx)


PIPELINE: list[Step] = [
    Step(1, "scf_relax",     "Initial VASP relaxation",          scf_relax.run),
    Step(2, "supercell",     "Supercell generation + relaxation", supercell.run),
    Step(3, "hf_setup",      "hf/ directory setup",              hf_setup.run),
    Step(4, "force_consts",  "VASP force constants",             force_constants.run),
    Step(5, "phonon_post",   "Phonon postprocessing",            phonon_post.run),
    Step(6, "raman_prep",    "Raman setup + displacements",      raman_prep.run),
    Step(7, "resonant_vasp", "Resonant VASP (dielectric)",       resonant_vasp.run),
    Step(8, "post_process",  "Post-processing + output",         post_process.run),
]

STEP_BY_NUMBER: dict[int, Step] = {s.number: s for s in PIPELINE}


@dataclass
class PipelineContext:
    """Typed context passed to every pipeline step.

    Construct via ``build_context()`` — do not instantiate directly.
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
    vasp_loop_fn: Any     # Callable
    write_status: Any     # Callable
    inside_salloc: bool = False

    # ── Derived from raw_config (populated in __post_init__) ─────────────────
    system_paths: dict = field(init=False)
    compute_mode: str = field(init=False)
    phonopy_dim: str = field(init=False)
    phonopy_amplitude: Any = field(init=False)
    phonopy_band_points: Any = field(init=False)
    scf_kpoints_mesh: str = field(init=False)
    scf_kpoints_shift: str = field(init=False)
    sup_relax_kpoints_mesh: str = field(init=False)
    sup_relax_kpoints_shift: str = field(init=False)
    hf_kpoints_mesh: str = field(init=False)
    hf_kpoints_shift: str = field(init=False)
    raman_kpoints_mesh: str = field(init=False)
    raman_kpoints_shift: str = field(init=False)
    desired_energies: list = field(init=False)
    raman_incident_pol: str = field(init=False)
    raman_scattered_pol: str = field(init=False)
    raman_surface_normal: str = field(init=False)
    vasp_max_restarts: int = field(init=False)
    hf_parallel: bool = field(init=False)
    vasp_srun_per_dir: str = field(init=False)
    vasp_sbatch_per_dir: str = field(init=False)
    salloc_relax: str = field(init=False)
    salloc_per_dir: str = field(init=False)
    start_from_supercell: bool = field(init=False)
    eigvec_band_path: str = field(init=False)
    eigvec_band_labels: str = field(init=False)
    eigvec_band_points: Any = field(init=False)

    # ── Mutable dispatch state (set by the dispatch loop, not at construction) ─
    current_step: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        cfg = self.raw_config
        c_mode = cfg.get("compute_mode", "interactive_manual")
        mode_cfg = cfg.get("compute_modes", {}).get(c_mode, {})

        self.compute_mode = c_mode
        self.system_paths = cfg.get("system_paths", {})
        self.phonopy_dim = cfg["phonopy"]["dim"]
        self.phonopy_amplitude = cfg["phonopy"]["amplitude"]
        self.phonopy_band_points = cfg["phonopy"]["band_points"]
        self.scf_kpoints_mesh = cfg["scf_kpoints"]["mesh"]
        self.scf_kpoints_shift = cfg["scf_kpoints"]["shift"]
        self.sup_relax_kpoints_mesh = cfg["sup_relax_kpoints"]["mesh"]
        self.sup_relax_kpoints_shift = cfg["sup_relax_kpoints"]["shift"]
        self.hf_kpoints_mesh = cfg["hf_kpoints"]["mesh"]
        self.hf_kpoints_shift = cfg["hf_kpoints"]["shift"]
        self.raman_kpoints_mesh = cfg["raman_kpoints"]["mesh"]
        self.raman_kpoints_shift = cfg["raman_kpoints"]["shift"]
        self.desired_energies = cfg["desired_energies"]
        self.raman_incident_pol = cfg["raman_tensor"]["incident_polarization"]
        self.raman_scattered_pol = cfg["raman_tensor"]["scattered_polarization"]
        self.raman_surface_normal = cfg["raman_tensor"]["surface_normal"]
        self.vasp_max_restarts = cfg["vasp_loop"]["max_restarts"]
        self.hf_parallel = cfg.get("hf_parallel", False)
        self.vasp_srun_per_dir = mode_cfg.get("srun_per_dir", "")
        self.vasp_sbatch_per_dir = mode_cfg.get("sbatch_per_dir", "")
        self.salloc_relax = mode_cfg.get("salloc") or mode_cfg.get("salloc_relax", "")
        self.salloc_per_dir = mode_cfg.get("salloc") or mode_cfg.get("salloc_per_dir", "")
        self.start_from_supercell = cfg.get("start_from_supercell", False)
        self.eigvec_band_path = cfg["eigenvectors_band"]["path"]
        self.eigvec_band_labels = cfg["eigenvectors_band"]["labels"]
        self.eigvec_band_points = cfg["eigenvectors_band"]["points"]

    @property
    def config(self) -> dict:
        """Raw merged config dict — for utility functions that accept the full config."""
        return self.raw_config


def build_context(write_status, config, material_dir, material_name,
                  work_dir, srun_args, vasp_binary, hffiles_dir, raman_dir,
                  script_dir, binary_utilities_dir, cpu_flag, scratch_flag,
                  run_relaxation, vasp_loop_fn, inside_salloc=False) -> PipelineContext:
    """Construct a PipelineContext from pipeline-level values."""
    return PipelineContext(
        raw_config=config,
        material_dir=material_dir,
        material_name=material_name,
        work_dir=work_dir,
        srun_args=srun_args,
        vasp_binary=vasp_binary,
        hffiles_dir=hffiles_dir,
        raman_dir=raman_dir,
        script_dir=script_dir,
        binary_utilities_dir=binary_utilities_dir,
        cpu_flag=cpu_flag,
        scratch_flag=scratch_flag,
        run_relaxation=run_relaxation,
        vasp_loop_fn=vasp_loop_fn,
        write_status=write_status,
        inside_salloc=inside_salloc,
    )
