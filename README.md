# Raman Workflow — _Ab Initio_ Resonant Raman Spectroscopy

VASP + phonopy pipeline for computing resonant Raman spectra of 2D materials
(hBN, MoS2, etc.), cluster-agnostic (NERSC Perlmutter, ORNL CADES Pathfinder).

Built using Fortran code by Dr. Liangbo Liang (ORNL) and the SpectroPy
project (https://github.com/TheorySpectroPy/SpectroPy).

## Architecture

```
raman_workflow/
├── generate.py           # the only thing you run by hand
├── common.sh             # static env preamble, sourced by generated scripts
├── check_convergence.py
├── check_dielectric.py
├── runHF
├── post/                 # phonon/Raman plotting + VESTA symmetry visualization
├── scripts/
│   └── share_material.sh # copy a finished material to a shared location
├── install.sh
├── pathfinder.bashrc     # cluster-specific env defaults
└── nersc.bashrc
```

See `CLAUDE.md` for the full breakdown of each piece, known sharp edges, and
how the `~/SpectroPy/` companion toolchain (a separate, standalone set of
Python scripts exploring alternatives to the Fortran Raman binaries) relates
to this pipeline.

## Setup

```bash
bash raman_workflow/install.sh <pathfinder|nersc>
source ~/.bashrc
```

This creates `$RAMAN_PROJECT_DIR` (default `~/vasp_calculations`), appends
`PATH`/`RAMAN_PROJECT_DIR` plus the cluster's env vars (`CONDA_INIT`,
`CONDA_ENV`, `VASP_MODULES`, `VASP_BINARY*`, `BINARY_UTILITIES_DIR`,
`SPECTROPY_DIR`, ...) into `~/.bashrc`'s `raman_workflow install.sh` block,
and reports which paths/binaries it could and couldn't confirm exist.

## Adding a material

```
$RAMAN_PROJECT_DIR/<name>/input/
├── POSCAR
├── POTCAR
└── workflow_settings.yaml
```

`workflow_settings.yaml` defines `phonopy:` (supercell dim, displacement
amplitude), `use_cpu`/`start_from_supercell`, `compute_mode` + `compute_modes:`
(fully user-defined — no hardcoded mode names; a mode just needs
`srun_relax`/`srun_per_dir`, plus `sbatch`/`sbatch_relax`/`sbatch_post` if it
should be sbatch-dispatched rather than run inside an existing allocation),
and `steps:` (which pipeline phases are active, with their VASP/phonopy
settings). See `MoS2`/`MoS2_nosym` for a complete, working example, including
`raman_prep.use_symmetry`.

## Running

```bash
$CONDA_ENV/bin/python3 raman_workflow/generate.py $RAMAN_PROJECT_DIR/<name>
bash $SCRATCH/vasp_calculations/<name>/run_all.sh
```

`generate.py` writes into `$SCRATCH/vasp_calculations/<name>/` by default
(`--no-scratch` to write directly into the material dir instead — only if
explicitly asked for). `--cpu` picks the CPU VASP binary variant. `--debug`
bakes VASP's real `--dry-run` into every call and writes into a nested
`debug/` subdirectory, so it never touches real data.

## Monitoring

```bash
sacct -u $USER --starttime=today -o JobID,JobName,State,ExitCode,Start,End
tail -f $SCRATCH/vasp_calculations/<name>/slurm-<jobid>.out
```

Each generated step script prints timestamped progress markers at every
major point (start, checkpoint resume, srun launch/exit) — a silent gap
between two log lines pinpoints where a run died. Run a step's own
`--check` (e.g. `bash raman/run_post_process.sh --check`) to see status
without launching anything.

## Sharing a finished material

```bash
bash raman_workflow/scripts/share_material.sh <name> [--pathfinder] [--login-node] [--with-chgcar-wavecar]
```

Copies to `$SHARE_MATERIAL_DIR/<name>` (set by `<cluster>.bashrc`).
`--login-node` runs a single plain rsync directly, no Slurm allocation —
the right choice for a modest-size material. Excludes CHGCAR/WAVECAR/WAVEDER/
`*.h5`/`*.ispin1_backup` by default.
