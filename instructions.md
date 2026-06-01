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

#### GPU mode (default — for hBN_LDA, hBN_PBE, hBN_PBEsol, etc.)

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

Other material names: `hBN_PBE`, `hBN_PBEsol`, `hBN_PS`, `hBN_PBE_X`, `hBN_PBE_C`, `hBN_RE`, `hBN_RP`, `hBN_PBEsol_3x3x1`, `hBN_PBEsol_5x5x1`, `hBN_PBEsol_6x6x1`.

#### CPU material:

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_CPU --cpu
```

With restart:

```bash
bash raman_workflow/run_raman_pipeline_interactive.sh hBN_PBEsol_CPU --cpu --restart
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

## Batch processing (run_gga_batch.sh)

To process multiple materials sequentially without manual intervention:

```bash
# Edit the MATERIALS array in the script first:
#   raman_workflow/run_gga_batch.sh (line ~59)

# GPU materials:
bash raman_workflow/run_gga_batch.sh              # resume
bash raman_workflow/run_gga_batch.sh --restart     # fresh start

# CPU materials:
bash raman_workflow/run_gga_batch.sh --cpu         # resume
bash raman_workflow/run_gga_batch.sh --cpu --restart  # fresh start
```

---

## Checking progress without reattaching

From **any** login node, check the status file:

```bash
cat $RAMAN_PROJECT_DIR/<material_name>/workflow_status.txt
```

Example:

```bash
cat $RAMAN_PROJECT_DIR/hBN_LDA/workflow_status.txt
```

This works regardless of which login node you're on. The status file shows:
- Which step is currently running
- Which steps completed (with timestamps)
- How long each step took
- Which steps remain

---

## Resume from last completed step

If the pipeline fails partway through (e.g., due to a transient error), re-run the **same command** — the script auto-detects `workflow_status.txt` and resumes from the failed step:

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

| Item | Pipeline Step |
|------|--------------|
| `scf/` | Step 3 VASP output |
| `hf/` | Steps 4–9 phonopy displacements + force constants |
| `raman/` | Steps 10–18 Raman displacements + spectra |
| `output/` | Aggregated results (plots, summaries) |
| `workflow_status.txt` | Resume checkpoint |
| Root-level files (INCAR, CONTCAR, POSCAR-*, etc.) | Step 3 intermediate files |

What `--restart` **preserves**:
- `input/` directory (POSCAR, INCARs, POTCAR, KPOINTS, symmetry.conf)
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
cat $RAMAN_PROJECT_DIR/hBN_LDA/workflow_status.txt
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
| `hBN_PBEsol` | PBEsol | ✅ Completed |
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
