# raman_workflow

VASP + phonopy pipeline that computes Raman spectra for 2D materials (hBN
defect supercells, pristine hBN, MoS2, etc.) across clusters (NERSC
Perlmutter, ORNL CADES Pathfinder). Runs: relax → supercell/force-constants →
phonon post-processing → resonant-Raman VASP runs → post-processing into
Raman spectra.

The old Python-orchestrated pipeline (`src/`/`util/`/`scripts/old/` and
friends) has been removed from this branch (`refactor`) — it's preserved on
another branch if ever needed for reference, but is not part of this
codebase going forward. Everything below describes the only pipeline that
exists here now.

## The pipeline

Four files, nothing else:

- `generate.py` — the only thing you run by hand. Reads layered YAML
  config for one material, writes real INCAR/KPOINTS/phonopy-conf files and
  a set of self-contained bash scripts (one per pipeline step) plus
  `run_all.sh` into a work directory. Everything config-derived is baked in
  as literals at generation time — the generated bash has zero Python
  dependency at runtime, only `common.sh` (sourced) and `check_*.py`
  (called by absolute path).
- `common.sh` — static, checked-in, NOT generated. Env preamble (conda +
  module load) plus two tiny bash functions (`run_until_complete`,
  `phase_steps_done`, `resume_contcar`). Sourced by every generated script.
- `check_convergence.py` — pure-text OUTCAR parsing, no HDF5/py4vasp.
  `--static` or `--relax` mode. Exit 0 = converged.
- `check_dielectric.py` — the one check that needs py4vasp/h5py (reads
  `vaspout.h5` for a nonzero Im(ε)). Non-fatal if the tools aren't available.

### Cluster-agnostic env: `<cluster>.bashrc`

`install.sh <cluster>` sources `<cluster>.bashrc` (e.g. `pathfinder.bashrc`,
`nersc.bashrc`) for its defaults, and appends the resulting `export` lines
into `~/.bashrc`'s `raman_workflow install.sh` block. `common.sh`/
`generate.py` read these purely via environment variables (no
`system_paths:` in any YAML) — CONDA_INIT/CONDA_ENV, VASP_MODULES,
VASP_BINARY(_CPU)/VASP_BINARY_GAM(_CPU), BINARY_UTILITIES_DIR, SPECTROPY_DIR,
SHARE_MATERIAL_DIR, and (Pathfinder-only so far) SCRATCH/OMP_NUM_THREADS.
Not everything a `<cluster>.bashrc` exports is necessarily forwarded by
install.sh's `~/.bashrc` writer yet — check the writer (grep for `export` in
`install.sh`) before assuming a new var reaches a real shell automatically;
export it by hand in the meantime if not.

Run it with the phonopy-env Python, not system python3:

```bash
$CONDA_ENV/bin/python3 generate.py <material_dir>
```

Defaults to writing into `$SCRATCH/vasp_calculations/<material_name>/`
(pass `--no-scratch` to write into the material dir directly — this only
happens if explicitly asked). `--debug` bakes VASP's real `--dry-run` flag
into every VASP call and writes into a nested `debug/` subdirectory so it
never touches real data. `--cpu` picks the CPU binary variant.

### compute_mode is fully config-driven

There are no hardcoded allowed `compute_mode` values (no "sbatch_mix"
special-cased in Python) — a material's `workflow_settings.yaml` defines its
own `compute_modes:` map and picks one via `compute_mode:`. `generate.py`
only requires each mode to define `srun_relax`/`srun_per_dir`; whether it
also defines `sbatch`/`sbatch_relax`/`sbatch_post` determines whether
`emit_run_all` treats it as an sbatch-dispatched mode or a plain
already-inside-an-allocation mode. See `MoS2`/`MoS2_nosym`'s
`workflow_settings.yaml` for the Pathfinder-recommended `sbatch_mix_*`
settings (from Liangbo).

### Raman step is SpectroPy-driven, not the Fortran raman_utility binaries

`raman_prep`/`post_process` used to shell out to Liangbo's Fortran chain
(`raman_symmetry_mapping` → `raman_dis`/`raman_dis_nosym` → `raman_poscar` →
`epsilon_derivative` → `raman_tensor`). That chain has been replaced with
`~/SpectroPy/` (installed into `$CONDA_ENV` via `pip install '.[all]'` — see
its own README for the full CLI/library reference):

- `raman_prep` (`emit_raman_prep`): `phonopy --symmetry -c CONTCAR >
  symmetry`, then either `generate_minimal_displacements.py` (`use_symmetry:
  true`, the default — Phonopy's own `generate_displacements()`, reducing
  both atom count *and* per-atom direction count via site symmetry, deeper
  than the old `raman_dis` ever did) or `create_displacements.py`
  (`use_symmetry: false` — full, unreduced +/-x/y/z for every atom). Both
  write `ra_pos_atom<N><suffix>/POSCAR`; the generated script then copies
  INCAR/KPOINTS/POTCAR into each (SpectroPy deliberately doesn't prescribe a
  DFT setup, so this repo still owns that part) and writes each dir's
  `run_vasp.sh`.
- `post_process` (`emit_post_process`): writes SpectroPy's non-interactive
  `input` file (polarization + `laser_energies:`/`broadening_fwhm:`/
  `broadening_type:`, read from `steps.post_process.raman_tensor.*` and
  `steps.post_process.broadening_fwhm`/`broadening_type`, default 5.0 cm-1
  Lorentzian), then runs `spectropy derivatives` (dielectric-derivative
  reconstruction, including symmetry-equivalent-atom expansion) →
  `spectropy spectrum` (mode Raman tensors/intensities) → `spectropy plot`
  (broadened plots). Toggle `steps.raman_prep.use_symmetry` explicitly in a
  material's `workflow_settings.yaml` when set to `false` (comment it in,
  don't just omit the key), since the default elsewhere is `true`.

Dropped, not yet replaced: the old `symmetry_filter`/`allowed_irreps`
output-filtering feature — SpectroPy's `Raman_intensity_complex_<eV>eV`
files don't carry an irrep column the way the Fortran binary's did, so the
awk-based filter would silently produce empty output; it's been removed
rather than left broken. Re-add if/when irrep labels are threaded through
SpectroPy's output.

### Step order and allocation grouping

```
defect_relax_1 (or scf_relax)   — own sbatch allocation
hf_setup                        — login node (cheap)
force_consts                    — ┐
phonon_post                     —  } ALL ONE sbatch allocation, in this
raman_prep                      —  } exact order — do not split these into
resonant_vasp                   — ┘ separate sbatch calls, it means queuing twice
post_process                    — own sbatch allocation
```

`resonant_vasp` depends on `raman_prep`'s `ra_pos_*` dirs; `raman_prep`
depends on `phonon_post`'s output; `phonon_post` depends on `force_consts`'
vasprun.xml. All four still share one allocation — `phonon_post`/`raman_prep`
don't call `srun` themselves but run fine inside a GPU allocation, so
bundling them costs nothing and avoids a second queue wait. This ordering
was gotten wrong once already (mid-refactor) — verify against
`emit_run_all` in `generate.py` before changing it again.

### Retry model — deliberately minimal

Only **one** retry layer exists, on purpose (stripped down from a much more
elaborate design after observing that nothing else was ever load-bearing):
the per-phase resubmit loop in `run_all.sh` (`phase_steps_done` + `until`),
which exists solely because a Slurm wall-time **TIMEOUT kills the entire
allocation**, including any retry loop running inside it — that's the one
failure mode nothing else can catch. Everything else — `run_until_complete`,
the relax srun call — runs once and fails loudly (`set -euo pipefail`, no
swallowed exit codes) if it doesn't succeed. Don't add more retry layers
back without a concrete, observed failure mode that needs them.

### Known sharp edges (found via live debugging, not theoretical)

- **`set -e` + a function whose last statement is a bare `[ cond ] && cmd`
  chain kills the caller silently** if the condition is false — this is
  *not* true for the same chain written inline at script top level (only
  matters inside a `foo() { ... }` body). This bit us once already
  (`resume_contcar`) and produced zero output, making it very hard to
  diagnose — always end such a function body with `|| true` or an explicit
  `if`. When a generated step script produces *no output at all* on
  failure, suspect this before suspecting Slurm/srun policy issues.
- SpectroPy's `derivatives`/`spectrum` stages (in `post_process`) read
  `band.yaml`/`irreps.yaml` from their own **current directory**, not from
  `hf/` where `phonon_post` actually wrote them — they must be copied into
  `raman/` before running, not after.
- `emit_hf_setup`'s source directory for `CONTCAR` depends on
  `start_from_supercell`: `../scf` if true (the defect-relax path already
  produced the full supercell), or `.` if false (a separate `supercell`
  step relaxed it in `hf/` itself, via `emit_supercell`, and left `CONTCAR`
  there, not in `scf/`).
- `scf_relax` and `defect_relax_1` cannot both be active in one material's
  `steps:` — they'd share and overwrite `scf/INCAR`/`scf/KPOINTS`.
  `generate.py` fails loudly at generation time if both are present.
- On Perlmutter, GPU `sbatch`/`srun` jobs on this account need
  `--gpus-per-node=4 --ntasks-per-node=4` (a full node's worth) to match
  Slurm's GPU policy — `--gpus-per-node=3` was tried and silently rejected
  ("Job request does not match any supported policy"). `KPAR` must evenly
  divide the total MPI rank count (`nodes × 4`).
- On Pathfinder, Slurm requires `-n`/`-c`/`--mem-per-cpu` explicitly on
  every `salloc`/`sbatch` — unlike Perlmutter, omitting any of them is a
  hard error ("Task count undefined", "CPUs per task count undefined",
  "Please specify how much cpu memory your job will use"), not a default.
- `common.sh` must `set +u` around `source ~/.bashrc` (system `/etc/bashrc`
  files can reference unset vars) and re-enable `set -u` after.

### Debugging a stuck/failed run

- `sacct -u <user> --starttime=today -o JobID,JobName,State,ExitCode,Start,End`
  and read the `slurm-<jobid>.out` in the material's work dir — the
  generated scripts print timestamped progress markers at every major step
  (start, checkpoint resume, srun launch/exit, batch boundaries), so a
  silent gap between two log lines pinpoints exactly where it died.
- Run a step's own `--check` by hand (`bash scf/run_defect_relax_1.sh
  --check`) to see current status without launching anything.
- `bash -x <script>.sh` reproduces the exact point of a silent failure.
- Never run a live reproduction test directly inside the real material's
  work directory — copy to a scratch/tmp location first, or it pollutes
  the real run's output files.

## `~/SpectroPy/` — the pipeline's Raman displacement/derivative/spectrum engine

Installed into `$CONDA_ENV` (`pip install '.[all]'` from a checkout — see its
own README for the full CLI/library reference) and invoked by `generate.py`'s
`raman_prep`/`post_process` steps (see "Raman step is SpectroPy-driven"
above) in place of the old Fortran `raman_utility` binaries. Independently
checked out and versioned from this repo — not vendored in.

Key pieces, for when something in the Raman step needs debugging:

- `process_symmetry.py` — parses phonopy's `--symmetry` output (real YAML)
  for atom-equivalence mapping and per-atom site symmetry.
- `generate_minimal_displacements.py` / `create_displacements.py` /
  `generate_atom_displacements.py` — the `minimal`/`full`/`atoms` displacement
  modes (`spectropy displacements --mode ...`); `prepare_vasp_inputs.py`
  turns `full`/`atoms` mode's `displacements.dat` into `ra_pos_atom*/POSCAR`
  dirs (`generate_minimal_displacements.py` does this itself).
- `reconstruct_dielectric_derivatives.py` — reconstructs a full per-atom
  D_ijk tensor from a reduced displacement set via the atom's own site
  symmetry (tensor rotation law), and expands to symmetry-equivalent atoms
  via `process_symmetry.py`'s mapping matrices.
- `calculate_dielectric_derivatives.py` / `calculate_spectrum.py` — the rest
  of the chain (vasprun.xml → D_ijk → mode Raman tensors/intensities),
  reading laser energies/polarization non-interactively from `input`.
- `generate_raman_plots.py` — broadened Raman spectrum plots, also
  non-interactive via `input`'s `broadening_fwhm`/`broadening_type`.
- `spectropy_cli.py` — the installed `spectropy` command wrapping all of the
  above (`displacements`/`derivatives`/`spectrum`/`plot`).

Cross-check against a brute-force (`use_symmetry: false`) run before trusting
results from a reduced (`use_symmetry: true`) displacement set on a new
material — `MoS2_fullsym` in `vasp_calculations/` is a real test bed for
exactly this, and this session's validation surfaced at least one real
finding worth knowing about: a completed real DFT calculation can fail to
respect its own crystal's site symmetry (checked three independent ways on
MoS2's data, ruling out a code/parsing bug) — the *reduced* displacement set
is actually more trustworthy in that sense, since its missing components are
*derived* from symmetry rather than independently (and potentially
inconsistently) computed by DFT.

## `post/` — analysis and visualization, works with either pipeline's output

Generic phonopy/VASP output plotting (`plot_band_structure.py`,
`plot_dos.py`, `plot_phonon_modes.py`, `plot_lattice_constants.py`,
`match_phonon_modes.py`, `compile_phonon_data.py`, `plot_raman_results.sh`)
plus `visualize_site_symmetry.py` — generates VESTA `.vesta` files
visualizing a structure's symmetry operations (mirror planes, displacement
arrows before/after transformation) for any material given a POSCAR/CONTCAR,
via spglib (not tied to a specific material or pipeline).

## `scripts/`

Just `share_material.sh` now — copies a finished material directory to a
shared location (`$SHARE_MATERIAL_DIR/<material>`, cluster-specific value
exported by `<cluster>.bashrc`). `--pathfinder` switches source/group for
Pathfinder instead of the NERSC default; `--login-node` skips
salloc/srun entirely for a plain single-stream rsync (simplest option for a
modest transfer); `--with-chgcar-wavecar` includes the real CHGCAR/WAVECAR
files (excluded by default, along with WAVEDER/*.h5/*.ispin1_backup).

## Git

Work happens on branch `refactor`. Commit after each verified change
(syntax check + `bash -n` on generated scripts) — small, single-purpose
commits, not batched.
