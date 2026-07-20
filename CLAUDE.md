# raman_workflow

VASP + phonopy pipeline that computes Raman spectra for hBN defect supercells
(and pristine hBN) on Perlmutter. Runs: relax → supercell/force-constants →
phonon post-processing → resonant-Raman VASP runs → post-processing into
Raman spectra.

There are **two parallel pipelines** in this repo right now:

- `src/` + `util/` + `scripts/` — the original Python-orchestrated pipeline.
  **Do not modify these** unless explicitly asked. They stay as-is; the new
  pipeline was built alongside them, never by editing them.
- `new/` — a from-scratch, minimal, bash-first rewrite (branch `refactor`).
  This is the one being actively worked on. See below.

If asked to change pipeline behavior without being told which pipeline,
**ask** — don't guess, and don't touch the old one on a guess.

## The new pipeline (`new/`)

Four files, nothing else:

- `new/generate.py` — the only thing you run by hand. Reads layered YAML
  config for one material, writes real INCAR/KPOINTS/phonopy-conf files and
  a set of self-contained bash scripts (one per pipeline step) plus
  `run_all.sh` into a work directory. Everything config-derived is baked in
  as literals at generation time — the generated bash has zero Python
  dependency at runtime, only `new/common.sh` (sourced) and `new/check_*.py`
  (called by absolute path).
- `new/common.sh` — static, checked-in, NOT generated. Env preamble (conda +
  module load) plus two tiny bash functions (`run_until_complete`,
  `phase_steps_done`, `resume_contcar`). Sourced by every generated script.
- `new/check_convergence.py` — pure-text OUTCAR parsing, no HDF5/py4vasp.
  `--static` or `--relax` mode. Exit 0 = converged.
- `new/check_dielectric.py` — the one check that needs py4vasp/h5py (reads
  `vaspout.h5` for a nonzero Im(ε)). Non-fatal if the tools aren't available.

Run it with the phonopy-env Python, not system python3 (system python3 on
Perlmutter is 3.6, this needs 3.11+):

```bash
/global/common/software/m526/phonopy_env/bin/python3 new/generate.py <material_dir>
```

Defaults to writing into `$SCRATCH/vasp_calculations/<material_name>/`
(pass `--no-scratch` to write into the material dir directly — this only
happens if explicitly asked). `--debug` bakes VASP's real `--dry-run` flag
into every VASP call and writes into a nested `debug/` subdirectory so it
never touches real data. `--cpu` picks the CPU binary variant.

Test bed material: `hBN_VB-_6x6` (config at
`~/vasp_calculations/hBN_VB-_6x6/input/workflow_settings.yaml`).

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
- `genRAram610_dynamic` (the Fortran RAMFILE binary, in `post_process`)
  reads `band.yaml`/`irreps.yaml` from its own **current directory**, not
  from `hf/` where `phonon_post` actually wrote them — they must be copied
  into `raman/` before the energy loop runs, not after.
- `emit_hf_setup`'s source directory for `CONTCAR` depends on
  `start_from_supercell`: `../scf` if true (the defect-relax path already
  produced the full supercell), or `.` if false (a separate `supercell`
  step relaxed it in `hf/` itself, via `emit_supercell`, and left `CONTCAR`
  there, not in `scf/`).
- `scf_relax` and `defect_relax_1` cannot both be active in one material's
  `steps:` — they'd share and overwrite `scf/INCAR`/`scf/KPOINTS`.
  `generate.py` fails loudly at generation time if both are present.
- Perlmutter GPU `sbatch`/`srun` jobs on this account need
  `--gpus-per-node=4 --ntasks-per-node=4` (a full node's worth) to match
  Slurm's GPU policy — `--gpus-per-node=3` was tried and silently rejected
  ("Job request does not match any supported policy").
- `KPAR` must evenly divide the total MPI rank count (`nodes × 4`).

### Debugging a stuck/failed run

- `sacct -u <user> --starttime=today -o JobID,JobName,State,ExitCode,Start,End`
  and read the `slurm-<jobid>.out` in the material's work dir — the
  generated scripts print timestamped progress markers at every major step
  (start, checkpoint resume, srun launch/exit, batch boundaries), so a
  silent gap between two log lines pinpoints exactly where it died.
- Run a step's own `--check` by hand (`bash scf/run_defect_relax_1.sh
  --check`) to see current status without launching anything.
- `bash -x <script>.sh` reproduces the exact point of a silent failure —
  this is how the `resume_contcar` bug above was actually found, after
  several wrong theories about Slurm policy.
- Never run a live reproduction test directly inside the real material's
  work directory — copy to a scratch/tmp location first, or it pollutes
  the real run's output files (this happened once; the fix was to `rm` the
  stray `OUTCAR`/`CONTCAR`/etc. it left behind).

## Old pipeline (`src/`, `util/`, `scripts/`)

Python-orchestrated, py4vasp/HDF5-based convergence checking, ~4600 lines.
Entry point is `src/automation_raman_analysis.py` via `util/provision.py`
for resource allocation. Left untouched throughout the `new/` rewrite —
treat as reference/production-stable unless told otherwise.

## Git

Work happens on branch `refactor`. Commit after each verified change
(syntax check + `bash -n` on generated scripts + confirm `git diff --stat
src/ util/ scripts/` is empty before committing) — small, single-purpose
commits, not batched.
