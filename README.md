# Raman Workflow — _Ab Initio_ Resonant Raman Spectroscopy

Automated VASP + Phonopy pipeline for computing resonant Raman spectra of 2D
materials on NERSC Perlmutter.

## Quick Start

```bash
# 1. Get a GPU node
salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526

# 2. Run the pipeline (from ~/)
bash raman_workflow/scripts/run_raman_pipeline_interactive.sh hBN_LDA

# 3. Monitor
tail -f $RAMAN_PROJECT_DIR/hBN_LDA/workflow.log
```

## Installation & Dependencies

### Environment (add to `~/.bashrc`)

```bash
export RAMAN_PROJECT_DIR=/global/homes/$USER/vasp_calculations
export BINARY_UTILITIES_DIR=/global/cfs/cdirs/m526/vasp_binaries/binary_utility
export VASP_BINARY=/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std
export VASP_BINARY_CPU=/global/cfs/cdirs/m526/liangbo/bin/cpu/vasp_std
export VASP_MODULES="gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu"
```

Then `source ~/.bashrc`.

### Python

```bash
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env
```

### Prerequisites

- NERSC Perlmutter account (project `m526`)
- Compiled Fortran binaries in `$BINARY_UTILITIES_DIR`
- VASP binary at `$VASP_BINARY`

## Architecture

```
raman_workflow/
├── src/                            # Pipeline source
│   ├── automation_raman_analysis.py   # Entry point (8-step dispatcher)
│   ├── scf_relax.py               # 1. VASP relaxation
│   ├── supercell.py               # 2. Phonopy supercell + relaxation
│   ├── hf_setup.py                # 3. hf/ directory setup
│   ├── force_constants.py         # 4. VASP force constants
│   ├── phonon_post.py             # 5. Phonopy postprocessing
│   ├── raman_prep.py              # 6. Raman setup + displacements
│   ├── resonant_vasp.py           # 7. Resonant VASP dielectric runs
│   └── post_process.py            # 8. Kopia, RAMFILE, tensor, plots
├── util/                          # Shared utilities (8 modules)
│   ├── io.py                      #   Tee, run_command, formatting
│   ├── vasp.py                    #   Convergence checks, force analysis
│   ├── incar.py / kpoints.py      #   INCAR & KPOINTS generation
│   ├── config.py                  #   YAML loading, srun args
│   ├── symlinks.py                #   CHGCAR/WAVECAR management
│   ├── status.py                  #   Logging, resume, progress tables
│   ├── phonopy.py                 #   Phonopy config files
│   └── postproc.py                #   Kopia, ramfile
├── scripts/                       # Shell scripts
│   ├── run_raman_pipeline_interactive.sh  # Single-material interactive
│   ├── run_queue.sh               # Multi-material autonomous queue
│   ├── run_raman_pipeline.sbatch  # Batch submission
│   └── show_status.sh             # Extract last status from workflow.log
├── post/                          # Post-processing & plotting
├── workflow_settings.yaml         # Fallback config template
├── queue_materials.conf           # Queue materials list
└── shared_workflow_settings.yaml  # Saved copy of shared config
```

### Config Layering

Per-material settings inherit from a shared base:

```
1. raman_workflow/workflow_settings.yaml      ← Fallback defaults
2. vasp_calculations/shared_workflow_settings.yaml ← Shared base (all materials)
3. <material>/input/workflow_settings.yaml     ← Per-material overrides
```

A material needs only 3 files in `input/`:

```
hBN_LDA/input/
├── POSCAR                  # Crystal structure
├── POTCAR                  # Pseudopotentials
└── workflow_settings.yaml  # Overrides (phonopy dim, k-points, incar_settings)
```

INCARs, KPOINTS, and symmetry.conf are **auto-generated from YAML**.

## How to Run

### Interactive (single material, for testing)

```bash
salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526
bash raman_workflow/scripts/run_raman_pipeline_interactive.sh hBN_LDA
```

Flags: `--restart` (clean start), `--cpu` (CPU VASP), `--no-scratch` (run on HOME).

### Batch (single material)

```bash
sbatch raman_workflow/run_raman_pipeline.sbatch
```

### Autonomous Queue (multiple materials)

Processes all materials in `queue_materials.conf` sequentially — each gets its
own allocation, no terminal needed:

```bash
nohup bash raman_workflow/scripts/run_queue.sh &> queue.log &
```

Monitor: `tail -f queue.log` or `squeue -u $USER`.

### Material Status

```bash
bash raman_workflow/scripts/show_status.sh $RAMAN_PROJECT_DIR/hBN_LDA/workflow.log
```

## Key Features

- **Resume support** — pipeline skips completed steps on rerun
- **`--scratch` flag** — runs VASP I/O on `$SCRATCH` for speed, keeps config on HOME
- **hf_parallel mode** — runs force-constant VASP directories concurrently
- **start_from_supercell** — for materials that begin with a supercell (defect systems)
- **YAML INCAR templates** — no flat INCAR files to manage

## See Also

- `CLAUDE.md` — detailed project documentation
- `instructions.md` — tmux-based interactive workflow guide
- `reports/tips.md` — known issues, fixes, and future improvements
- `SpectroPy/` — portable Python post-processing toolkit
