# Raman Workflow

This repository generates and runs VASP and Phonopy workflows for finite-displacement Raman calculations. It supports complete calculations beginning with a structure as well as Raman-only calculations that reuse existing phonon data.

The production Raman path currently uses Dr. Liangbo Liang's Fortran utilities:

- `raman_symmetry_mapping`
- `raman_dis` or `raman_dis_nosym`
- `raman_poscar`
- `epsilon_derivative`
- `raman_tensor`

SpectroPy remains available for development and plotting, but it is not the default displacement, derivative, or Raman-tensor backend.

## Directory model

Each material has a persistent project directory under `$RAMAN_PROJECT_DIR`, normally `~/vasp_calculations`:

```text
~/vasp_calculations/<material>/
├── input/
│   ├── POSCAR
│   ├── POTCAR
│   ├── workflow_settings.yaml
│   └── template.vesta        # optional
└── output/                   # copied back after post-processing
```

By default, generated scripts and calculations go under scratch:

```text
$SCRATCH/vasp_calculations/<material>/
├── input -> ~/vasp_calculations/<material>/input
├── scf/
├── hf/
├── raman/
├── output/
└── run_all.sh
```

The project directory holds durable inputs and final results. The scratch directory holds active calculations and large intermediate files.

## Installation

Run the installer once for the current cluster:

```bash
cd ~/raman_workflow
bash install.sh pathfinder
# or: bash install.sh nersc
source ~/.bashrc
```

NERSC uses the existing shared Phonopy environment directly through `PATH`; it does not require `CONDA_PREFIX` to be set:

```bash
export CONDA_ENV=/global/common/software/m526/phonopy_env
export PATH="$CONDA_ENV/bin:$PATH"
python3 -c 'import phonopy, yaml; print("OK")'
```

The installer configures paths such as `RAMAN_PROJECT_DIR`, `CONDA_ENV`, `VASP_BINARY*`, `VASP_MODULES`, `BINARY_UTILITIES_DIR`, and `SPECTROPY_DIR`. `SCRATCH` must also be available in the shell environment; the Pathfinder profile defines it as `~/scratch`.

Cluster defaults live in `pathfinder.bashrc` and `nersc.bashrc`. Shared scientific settings and INCAR templates live in:

```text
$RAMAN_PROJECT_DIR/shared_workflow_settings.yaml
```

Per-material settings override the shared settings.

## Generate and run a workflow

For `BN_9X9_0.6`, generate the scripts with:

```bash
source ~/.bashrc
$CONDA_ENV/bin/python3 ~/raman_workflow/generate.py \
    "$RAMAN_PROJECT_DIR/BN_9X9_0.6"
```

The equivalent command with explicit paths is:

```bash
~/phonopy_env/bin/python3 ~/raman_workflow/generate.py \
    ~/vasp_calculations/BN_9X9_0.6
```

This only generates scripts and input files. It does not submit calculations.

Run the generated workflow with:

```bash
bash "$SCRATCH/vasp_calculations/BN_9X9_0.6/run_all.sh"
```

Show generator options with:

```bash
$CONDA_ENV/bin/python3 ~/raman_workflow/generate.py --help
```

Available options are:

- `--no-scratch`: generate directly in the material project directory.
- `--cpu`: select the CPU VASP binary for this generation.
- `--debug`: generate beneath a separate `debug/` directory and add VASP's `--dry-run` flag.
- `--no-monitor`: submit a Raman array and dependent post-processing job, then return immediately.
- `--shared PATH`: use a different shared settings file.

## Selecting workflow stages

The keys under the per-material `steps:` section determine which stages are generated and run. Their order in the YAML file is retained.

The supported stages are:

1. `scf_relax` or `defect_relax_1`
2. `supercell`
3. `hf_setup`
4. `force_consts`
5. `phonon_post`
6. `raman_prep`
7. `resonant_vasp`
8. `post_process`

Do not enable both `scf_relax` and `defect_relax_1`; both use `scf/`.

### Raman-only workflow

When the relaxed structure and phonon data already exist, only these stages are needed:

```yaml
steps:
  raman_prep:
    use_symmetry: true
    displacement: "0.10 0.10 0.60"
  resonant_vasp:
    kpoints: {mesh: "1 1 1"}
    incar_overrides: |
      ISTART = 0
      ICHARG = 2
      ENCUT = 400
      NELECT = 646
      NBANDS = 1536
  post_process:
    desired_energies:
      - "0.00"
      - "1.50"
      - "1.96"
```

The Raman-only stages expect:

```text
scf/CONTCAR
hf/band.yaml
hf/irreps.yaml             # optional; used for labels and filtering
```

If electronic restart files are available, place `CHGCAR` and `WAVECAR` in `scf/` and use compatible `ISTART` and `ICHARG` settings. If they are absent, use `ISTART = 0` and `ICHARG = 2` so VASP starts electronically from scratch.

The `BN_9X9_0.6` scratch directory links its supplied data into the expected locations:

```text
scf/CONTCAR -> ../CONTCAR
hf/band.yaml -> ../band.yaml
hf/irreps.yaml -> ../irreps.yaml
```

## Raman preparation

`raman_prep.use_symmetry` selects Liangbo's displacement program:

- `true` runs `raman_symmetry_mapping` followed by `raman_dis`. Only symmetry-inequivalent atoms are displaced.
- `false` runs `raman_dis_nosym`. Every atom is displaced.

The three displacement values are Cartesian amplitudes in angstroms for x, y, and z:

```yaml
raman_prep:
  use_symmetry: true
  displacement: "0.10 0.10 0.60"
```

The preparation stage creates `atomic_displacement`, `pos_atom*`, and `ra_pos_atom*` calculation directories. Each `ra_pos_*` directory receives a `run_vasp.sh` script.

## Compute modes

`compute_mode` selects one entry from `compute_modes`. Names are user-defined; behavior comes from the keys inside the selected entry.

| Key | Purpose |
|---|---|
| `srun_relax` | `srun` arguments for relaxation |
| `srun_per_dir` | `srun` arguments for one HF or Raman directory |
| `sbatch_relax` | allocation used for relaxation |
| `sbatch` | one allocation used for the main compute phase when arrays are disabled |
| `sbatch_array` | allocation for each independent displacement-array task |
| `array_max_concurrent` | maximum simultaneously running array tasks |
| `sbatch_post` | allocation used for post-processing |

### Pathfinder one-node arrays

Liangbo recommends one-node jobs on Pathfinder's `serial` partition with `--mem=0`. A matching compute mode is:

```yaml
compute_mode: sbatch_mix_pathfinder

compute_modes:
  sbatch_mix_pathfinder:
    sbatch_relax: >-
      -p parallel -q normal --nodes=2 --ntasks=256 -c 1
      --mem=0 -t 04:00:00
    sbatch_array: >-
      -p serial -q normal --nodes=1 --ntasks=128 -c 1
      --mem=0 -t 01:00:00
    array_max_concurrent: 15
    sbatch_post: >-
      -p serial -q normal --nodes=1 --ntasks=1 -c 1
      --mem=0 -t 00:30:00
    srun_relax: "--nodes=2 --ntasks=256 --cpu-bind=cores"
    srun_per_dir: "--nodes=1 --ntasks=128 --cpu-bind=cores"
```

With `sbatch_array` present, `force_consts` and `resonant_vasp` submit Slurm arrays. Every array task runs one displacement directory on one node. `%15` limits the array to 15 simultaneous calculations. Completed directories are skipped when the stage is rerun.

For a Raman-only workflow that should return control after submission, generate with `--no-monitor` or set:

```yaml
monitor: false
```

Detached mode submits the unfinished Raman calculations without `--wait` or automatic retries. It then submits post-processing with an `afterok` dependency on the array. Each array task performs the convergence and dielectric checks itself, so failed or timed-out tasks prevent post-processing from starting. After correcting the problem, rerun `run_all.sh`; completed directories are skipped and a new array is submitted for the remainder.

`--mem=0` is a Pathfinder-specific scheduling choice from Liangbo's instructions. It requests all memory on the allocated node and avoids the oversized request produced by `128 × --mem-per-cpu=4G`.

For Gamma-only calculations on Pathfinder, use:

```yaml
use_gam: true
use_cpu: true
```

This selects the configured CPU `vasp_gam` binary. `OMP_NUM_THREADS=1` and the required compiler, MPI, FFTW, OpenBLAS, and ScaLAPACK modules are loaded through the cluster environment.

## Post-processing

For each requested laser energy, the production workflow runs Liangbo's `epsilon_derivative` and `raman_tensor` programs.

```yaml
post_process:
  raman_tensor:
    incident_polarization: "1.0 0.0 0.0"
    scattered_polarization: "1.0 0.0 0.0"
    surface_normal: "z"
  desired_energies:
    - "0.00"
    - "1.50"
    - "1.96"
```

Optional irrep filtering is configured with:

```yaml
post_process:
  symmetry_filter:
    enabled: true
    allowed_irreps: ["A1'", "E'"]
```

Important Raman outputs include:

```text
raman/dielectric_tensor_<energy>
raman/epsilon_derivative_<energy>
raman/Raman_tensor
raman/Raman_intensity_complex_<energy>eV
raman/Raman_intensity_polarization_averaged_<energy>eV
output/raman_data/
output/raman_spectra/
```

Final `output/` contents are copied back to the material project directory.

## Checking, restarting, and monitoring

Every generated stage supports a non-running status check:

```bash
cd "$SCRATCH/vasp_calculations/BN_9X9_0.6"
bash raman/run_raman_prep.sh --check
bash raman/run_resonant_vasp.sh --check
bash raman/run_post_process.sh --check
```

To remove a stage's generated results and prepare it to run again:

```bash
bash raman/run_resonant_vasp.sh --restart
```

Review the generated script before using `--restart`; restart actions delete that stage's output files.

Monitor ordinary and array jobs with:

```bash
squeue -u "$USER"
sacct -u "$USER" --starttime=today \
    -o JobID,JobName,State,ExitCode,Elapsed,Start,End
```

Array-task scheduler logs normally use names such as `slurm-<array-job-id>_<task-id>.out`. VASP output remains inside each displacement directory:

```text
hf/hf_POSCAR-*/relaxation.stdout
raman/ra_pos_*/stdout
```

Rerunning `run_all.sh` is safe for completed stages: their `--check` commands prevent successful calculations from being repeated. If an array is interrupted, its next invocation includes only unfinished directories.

## Generated files and regeneration

`generate.py` writes scripts and static INCAR/KPOINTS files for the active stages. It does not submit jobs.

Regenerating does not clean scripts left by stages that were subsequently removed from `workflow_settings.yaml`. `run_all.sh` is authoritative: only stages listed there are executed.

A legacy flat file named `input` conflicts with the workflow's required `input/` directory link. Rename such a file, for example to `liangbo_input`, before generating.

## Repository layout

```text
raman_workflow/
├── generate.py
├── common.sh
├── check_convergence.py
├── check_dielectric.py
├── runHF
├── install.sh
├── pathfinder.bashrc
├── nersc.bashrc
├── post/
└── scripts/
    └── share_material.sh
```

## Sharing results

Copy a completed Pathfinder calculation to the configured shared location with:

```bash
bash ~/raman_workflow/scripts/share_material.sh \
    BN_9X9_0.6 --pathfinder --login-node
```

Large `CHGCAR`, `WAVECAR`, `WAVEDER`, and HDF5 files are excluded by default. To include `CHGCAR` and `WAVECAR`:

```bash
bash ~/raman_workflow/scripts/share_material.sh \
    BN_9X9_0.6 --pathfinder --with-chgcar-wavecar
```
