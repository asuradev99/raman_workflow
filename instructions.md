# CURRENT JOB:

node: login40
name: raman

# Interactive Workflow with tmux

This workflow lets you run the Raman pipeline on a compute node via `salloc` (instant queue grant) while surviving SSH disconnects using `tmux`.

All material directories are now configured for **Gamma-only** phonon calculation (single point at Γ instead of the full Γ–M–K–Γ band path). This saves ~100× in band structure computation time. The Gamma-only frequencies (E' and A₂″ modes) are still fully accurate — only the band dispersion plot is skipped.

---

## Prerequisite: Gamma-only settings

All workflow settings files have been updated to compute phonons **at the Gamma point only**:

| Setting | Old (full band) | New (Gamma-only) |
|---------|-----------------|------------------|
| `phonopy.band_path` | `"0 0 0  0.5 0 0  0.333333 0.333333 0  0 0 0"` | `"0 0 0  0 0 0"` |
| `phonopy.band_points` | `101` | `1` |
| `eigenvectors_band.path` | `"0.0 0.0 0.0  0.5 0.0 0.0 … 0.0 0.0 0.0"` | `"0.0 0.0 0.0  0.0 0.0 0.0"` |
| `eigenvectors_band.points` | `101` | `1` |

---

## Procedure

### 1. Log into Perlmutter

```bash
ssh <username>@perlmutter.nersc.gov
```

Note which login node you land on (e.g. `login11`, `login12`, `login13`, or `login14`). Check with:

```bash
hostname
```

### 2. Start a tmux session

```bash
tmux new-session -s raman
```

The prompt will change — you're now inside `tmux`.

### 3. Allocate a compute node

#### GPU mode (default — for hBN_LDA, hBN_PBE, hBN_PBEsol_4x4x1, etc.)

```bash
salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526
```

Wait for the allocation to be granted (usually instant). The prompt changes to show the compute node hostname.

#### CPU mode (for hBN_PBEsol_CPU)

```bash
salloc -N 1 -C cpu -t 04:00:00 --qos=interactive -A m526
```

> **Important**: Do NOT use `--gpus-per-node` with CPU allocations. Only `hBN_PBEsol_CPU` is configured for CPU.

### 4. Run the pipeline

Inside the allocation (still inside `tmux`), run:

#### GPU material (default):

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_LDA
```

With restart (deletes all generated files, keeps `input/` and `workflow_settings.yaml`):

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_LDA --restart
```

Using scratch (runs VASP on `$SCRATCH` for fast I/O, results copied back to HOME):

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_6x6x1 --scratch
```

With both scratch and restart:

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_6x6x1 --scratch --restart
```

Other material names: `hBN_PBE`, `hBN_PBEsol_4x4x1`, `hBN_PS`, `hBN_PBE_X`, `hBN_PBE_C`, `hBN_RE`, `hBN_RP`, `hBN_PBEsol_3x3x1`, `hBN_PBEsol_5x5x1`, `hBN_PBEsol_6x6x1`.

#### CPU material:

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_CPU --cpu
```

With restart:

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_CPU --cpu --restart
```

#### Combining flags:

Flags can be combined freely. Examples:

```bash
# GPU + scratch + restart
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_6x6x1 --scratch --restart

# CPU + scratch
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_CPU --cpu --scratch
```

### 5. Detach from tmux

Press: **`Ctrl+B`**, then **`d`**

You'll return to your original shell on the login node. The pipeline keeps running inside `tmux`.

### 6. Log out

```bash
exit
```

The SSH connection closes, but `tmux` stays alive on the login node, and `salloc` (still running inside `tmux`) keeps the compute node allocation alive.

### 7. Reconnect later

Log back into Perlmutter:

```bash
ssh <username>@perlmutter.nersc.gov
```

You'll likely land on a different login node. First, SSH to the **original** login node:

```bash
hostname                     # check where you are
ssh loginX                   # replace X with the original node number (e.g. ssh login11)
```

### 8. Reattach to tmux

Once on the correct login node:

```bash
tmux attach -t raman
```

You'll see the pipeline output. If it's still running, you can monitor it. If it finished, you'll see the completion message and the final status.

---

## `--cpu` flag details

The `--cpu` flag switches the pipeline to CPU-compatible settings:

| Aspect | GPU (default) | CPU (`--cpu`) |
|--------|---------------|---------------|
| VASP binary | `/gpu/vasp_std` | `/cpu/vasp_std` |
| Modules | `gpu PrgEnv-nvidia` … `vasp/6.4.3-gpu` | `cpu PrgEnv-gnu` … `vasp/6.4.3-cpu` |
| `srun --ntasks` | `4` | `32` |
| `srun --cpus-per-task` | `32` | `4` |
| `srun --gpus` | `4` | (none) |
| `salloc -C` | `gpu` | `cpu` |
| `salloc --gpus-per-node` | `4` | (omit) |
| Compatible dirs | all GPU materials | `hBN_PBEsol_CPU` only |

The CPU material directory [`hBN_PBEsol_CPU/workflow_settings.yaml`](vasp_calculations/hBN_PBEsol_CPU/workflow_settings.yaml:31) already has the correct `vasp_srun` settings:
- `gpus: 0`
- `ntasks: 32`
- `cpus_per_task: 4`
- `constraint: "cpu"`

---

---

## `--scratch` flag details

The `--scratch` flag redirects VASP output to `$SCRATCH` (NERSC's fast scratch filesystem) instead of `$HOME`. This is beneficial for large supercells (5×5×1, 6×6×1) where VASP writes large output files (WAVECAR, CHGCAR) that would consume `$HOME` quota.

### How it works

| Aspect | Default (HOME) | `--scratch` |
|--------|---------------|-------------|
| Config source (`input/`, `workflow_settings.yaml`) | `$HOME/vasp_calculations/<material>/` | Same — stays on HOME |
| Intermediate dirs (`scf/`, `hf/`, `raman/`) | `$HOME/vasp_calculations/<material>/` | `$SCRATCH/vasp_calculations/<material>/` — **not on HOME** |
| Unified workflow log (`workflow.log`) | `$MATERIAL_DIR/workflow.log` on HOME | `$SCRATCH/vasp_calculations/<material>/workflow.log` — on scratch for fast I/O |
| Final `output/` | `$HOME/vasp_calculations/<material>/output/` | Copied from SCRATCH → HOME on completion |
| Sync at start | N/A | `input/` + `workflow_settings.yaml` copied from HOME → SCRATCH |
| Cleanup on `--restart` | Removes `scf/`, `hf/`, `raman/`, `output/` from HOME | Removes from SCRATCH + also cleans `output/` on HOME |

### When to use

- **Large supercells** (5×5×1, 6×6×1) that write large WAVECAR/CHGCAR files
- Materials approaching `$HOME` quota limits
- Jobs where fast I/O is beneficial (`$SCRATCH` is typically faster than `$HOME`)

### When NOT to use

- Small supercells (3×3×1 or smaller) — the sync overhead isn't worth it
- Quick test runs where you want results instantly visible in HOME
- Materials where you don't need `$HOME` quota relief

### Crash recovery

- `workflow.log` lives on SCRATCH under `--scratch` for faster I/O; the `STATUS_FILE` env var can override to HOME if needed
- VASP output stays on SCRATCH — same as without the flag
- If `$SCRATCH` is purged (90-day retention on NERSC), the sync re-creates `input/` at pipeline start. Use `--restart` to re-run VASP steps.
- `$SCRATCH` is a shared Lustre filesystem at NERSC — visible from all login/compute nodes.

### Example with large supercell

```bash
# 1. Allocate (no special allocation needed — scratch is transparent)
salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526

# 2. Run with scratch flag
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_6x6x1 --scratch
```

---

## Batch processing (run_gga_batch.sh)

To process multiple materials sequentially without manual intervention:

```bash
# Edit the MATERIALS array in the script first:
#   raman_workflow/run_gga_batch.sh (line ~59)

# GPU materials (default):
bash raman_workflow/run_gga_batch.sh                 # resume
bash raman_workflow/run_gga_batch.sh --restart        # fresh start

# GPU materials with scratch (large supercells):
bash raman_workflow/run_gga_batch.sh --scratch        # resume on scratch
bash raman_workflow/run_gga_batch.sh --scratch --restart  # fresh start on scratch

# CPU materials:
bash raman_workflow/run_gga_batch.sh --cpu            # resume
bash raman_workflow/run_gga_batch.sh --cpu --restart  # fresh start on CPU
bash raman_workflow/run_gga_batch.sh --cpu --scratch  # CPU + scratch
```

---

## Checking progress without reattaching

From **any** login node, check the unified workflow log:

```bash
tail -50 $RAMAN_PROJECT_DIR/<material_name>/workflow.log
```

Under `--scratch`:

```bash
tail -50 $SCRATCH/vasp_calculations/<material_name>/workflow.log
```

Example:

```bash
tail -50 $RAMAN_PROJECT_DIR/hBN_LDA/workflow.log
```

This works regardless of which login node you're on. The workflow log shows:
- A box-drawn status table with step-by-step progress (icons: ✓ completed, ▶ running, ✗ failed)
- Chronological log output from all pipeline steps
- Timestamps and durations for each step

---

## Resume from last completed step

If the pipeline fails partway through (e.g., due to a transient error), re-run the **same command** — the script auto-detects `workflow.log` and resumes from the failed step:

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_LDA
```

---

## Full restart with `--restart`

To delete **all generated files** and restart from scratch (keeping `input/` and `workflow_settings.yaml` intact), add `--restart`:

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_LDA --restart
```

What `--restart` removes:

| Item | Pipeline Step | Default (HOME) | With `--scratch` |
|------|--------------|----------------|-------------------|
| `scf/` | ALL Step 3 output + intermediates | `$MATERIAL_DIR/scf/` | `$SCRATCH/vasp_calculations/<material>/scf/` |
| `hf/` | Steps 4–10 supercell relax + phonopy + force constants | `$MATERIAL_DIR/hf/` | `$SCRATCH/vasp_calculations/<material>/hf/` |
| `raman/` | Steps 11–20 Raman displacements + spectra | `$MATERIAL_DIR/raman/` | `$SCRATCH/vasp_calculations/<material>/raman/` |
| `output/` | Aggregated results (plots, summaries) | `$MATERIAL_DIR/output/` | `$SCRATCH/vasp_calculations/<material>/output/` + also cleans `output/` on HOME |
| `workflow.log` | Unified status/log (resume checkpoint) | `$MATERIAL_DIR/workflow.log` | `$SCRATCH/vasp_calculations/<material>/workflow.log` |

What `--restart` **preserves**:
- `input/` directory (POSCAR, INCARs, POTCAR, KPOINTS) — `symmetry.conf` is auto-generated by the pipeline
- `workflow_settings.yaml`
- All environment variable and config settings

---

## Quick-reference: tmux commands

| Action | Command |
|--------|---------|
| Create new session | `tmux new-session -s <name>` |
| Detach from session | `Ctrl+B`, then `d` |
| List sessions | `tmux ls` |
| Reattach to session | `tmux attach -t <name>` |
| Kill a session | `tmux kill-session -t <name>` |

---

## Fallback: sbatch (fully detached, no tmux needed)

If you prefer a "submit and forget" approach that doesn't require tmux:

```bash
sbatch raman_workflow/run_raman_pipeline.sbatch
```

Monitor with:

```bash
squeue -u $USER
tail -50 $RAMAN_PROJECT_DIR/hBN_LDA/workflow.log
```

---

## NBANDS auto-scaling

The pipeline automatically calculates NBANDS from the supercell size:

```
NBANDS = ceil(N_valence_electrons × phonopy.dim_product × 1.3 / 16) × 16
```

Where `N_valence_electrons` is read from POSCAR atom counts × POTCAR ZVALs (B=3, N=5).

---

## Material directories summary

| Directory | Functional | Status |
|-----------|-----------|--------|
| `hBN_LDA` | LDA | ✅ Completed |
| `hBN_PBE` | PBE | ✅ Completed |
| `hBN_PBEsol_4x4x1` | PBEsol | ✅ Completed |
| `hBN_PS` | PBEsol (PS) | ✅ Completed |
| `hBN_PBE_X` | PBE_X | ✅ Completed |
| `hBN_PBE_C` | PBE_C | ✅ Completed |
| `hBN_RE` | RE | ✅ Completed |
| `hBN_RP` | RP | ✅ Completed |
| `hBN_PBEsol_CPU` | PBEsol (CPU) | ❄️ Not started — use `--cpu` |
| `hBN_PBEsol_3x3x1` | PBEsol, 3×3×1 | ❄️ Not started |
| `hBN_PBEsol_5x5x1` | PBEsol, 5×5×1 | ❄️ Not started |
| `hBN_PBEsol_6x6x1` | PBEsol, 6×6×1 | ❄️ Not started |

All directories use Gamma-only phonon settings. Results are tabulated in [`raman_workflow/hBN_functional_comparison.md`](raman_workflow/hBN_functional_comparison.md).

---

## Comparison table

See [`raman_workflow/hBN_functional_comparison.md`](raman_workflow/hBN_functional_comparison.md) for the complete comparison of lattice constants, A₂″ and E′ mode frequencies, and VASP compute times across all functionals.
