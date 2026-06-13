# Tutorial [INCOMPLETE AI DRAFT -- WILL IMPROVE LATER]: hBN Raman Spectrum from Scratch

This tutorial walks through a complete calculation of the resonant Raman spectrum of hexagonal boron nitride (hBN) using PBEsol. It covers every step from input preparation to the final spectrum plot, with explanations of what each step does and why.

The `hBN_PBEsol_6x6x1/` directory in this folder is a ready-to-run example you can copy and modify.

---

## Background

hBN is a 2D layered material with a hexagonal crystal structure (space group P6₃/mmc for the bulk, P6̄m2 for the monolayer). It has:
- 2 atoms per primitive unit cell (one B, one N)
- 6 zone-center phonon modes at Gamma: 2 acoustic (E₁ᵤ, A₂ᵤ), 4 optical (E₂g, B₁ᵤ, A₁g, E₁ᵤ)
- 1 Raman-active mode: **E₂g at ~1370 cm⁻¹** (in-plane B–N stretching)
- The E₂g mode gives a single sharp peak in the Raman spectrum

The goal is to compute the Raman spectrum as a function of laser excitation energy (resonant Raman), which tells us how the scattering intensity varies near the optical gap of hBN (~6 eV for monolayer).

---

## Step 0 — Prerequisites

### Environment

Ensure your `~/.bashrc` has:
```bash
export RAMAN_PROJECT_DIR="$HOME/vasp_calculations"
export BINARY_UTILITIES_DIR="/global/cfs/cdirs/m526/vasp_binaries/binary_utility"
export VASP_BINARY="/global/cfs/cdirs/m526/liangbo/bin/gpu/vasp_std"
export VASP_MODULES="gpu PrgEnv-nvidia cray-hdf5 cray-fftw nccl/2.18.3-cu12 vasp/6.4.3-gpu"
```

### Shared config

The `shared_workflow_settings.yaml` in this directory is the base config for all materials. Copy it to your project directory:

```bash
cp raman_workflow/examples/shared_workflow_settings.yaml $RAMAN_PROJECT_DIR/
```

This file contains the INCAR templates, srun arguments, laser energies, and polarization settings that apply to all materials. You should review it once before running — especially the `laser_energies` list and `raman_tensor` polarization settings.

---

## Step 1 — Prepare the Input Files

Copy the example directory:
```bash
cp -r raman_workflow/examples/hBN_PBEsol_6x6x1 $RAMAN_PROJECT_DIR/
```

Your directory should now contain:
```
vasp_calculations/hBN_PBEsol_6x6x1/
└── input/
    ├── POSCAR
    ├── POTCAR
    └── workflow_settings.yaml
```

### POSCAR — Crystal Structure

The POSCAR in the example contains the hBN primitive cell:

```
hBN primitive cell
   1.00000000000000
     2.5100000000000000    0.0000000000000000    0.0000000000000000
    -1.2550000000000000    2.1736558027719498    0.0000000000000000
     0.0000000000000000    0.0000000000000000   12.0000000000000000
   B    N
   1    1
Direct
  0.3333333333333333  0.6666666666666667  0.0000000000000000
  0.6666666666666667  0.3333333333333333  0.0000000000000000
```

Key features:
- Lattice constant a = 2.51 Å (experimental value; PBEsol gives ~2.505 Å after relaxation)
- Large c = 12 Å (vacuum gap for 2D material calculation)
- Primitive (not conventional) cell with B at 1/3, 2/3 and N at 2/3, 1/3

**For a new material:** Replace this with your primitive cell POSCAR. Make sure atom order matches the POTCAR element order.

### POTCAR — Pseudopotentials

The POTCAR contains PAW pseudopotentials for B and N (in that order, matching the POSCAR). You need access to the VASP PAW library. For PBEsol:

```bash
cat $VASP_PAW_PATH/PAW_PBE/B/POTCAR $VASP_PAW_PATH/PAW_PBE/N/POTCAR > input/POTCAR
```

**Note on functional consistency:** We use PBE PAW potentials with `GGA = PS` (PBEsol). This is the standard approach — VASP uses the PBEsol functional for the exchange-correlation energy but reads PAW data from PBE POTCAR files. The PAW data (core electron treatment) is functional-independent to a good approximation.

### workflow_settings.yaml — Per-Material Config

The example config:
```yaml
phonopy:
  dim: "6 6 1"           # 6×6×1 supercell = 72 atoms

scf_kpoints:
  mesh: "24 24 1"        # Dense mesh for unit cell

hf_kpoints:
  mesh: "4 4 1"          # Coarse mesh for supercell (24/6 = 4)

sup_relax_kpoints:
  mesh: "4 4 1"

raman_kpoints:
  mesh: "24 24 1"        # Dense mesh for dielectric

incar_settings:
  relax: |
    GGA = PS
  dielec: |
    GGA = PS
  hf: |
    GGA = PS
```

**Why 6×6×1?** The Raman-active E₂g mode in hBN has significant interplanar force constants up to the 5th nearest-neighbor shell (~7 Å). A 6×6×1 supercell (15 Å × 15 Å) fully contains these interactions, giving well-converged phonon frequencies. See [concepts.md](../docs/concepts.md) for a more detailed explanation.

**Why 24×24×1 for k-points?** The dielectric tensor must be well-converged for accurate Raman intensities. For the unit cell with 2 atoms, 24×24×1 is sufficient. Coarser meshes (12×12×1) work for quick tests but may give ~10% errors in absolute intensities.

---

## Step 2 — Request a Compute Node

For interactive running (recommended for first-time setup):

```bash
tmux new-session -s hbn-raman
salloc -N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526
```

Wait for the node to be granted (typically 1–10 minutes for interactive queue).

**Wall time estimate for hBN 6×6×1:**

| Step | Typical time |
|---|---|
| Step 1: Unit-cell relaxation | 30–45 min |
| Step 2: Supercell generation + relaxation | 30–45 min |
| Step 3: hf/ setup | ~2 min |
| Step 4: Force constants (2 displacement runs) | 20–40 min |
| Step 5: Phonon postprocessing | ~5 min |
| Step 6: Raman setup | ~5 min |
| Step 7: Raman dielectric runs | 60–120 min |
| Step 8: Post-processing + plots | ~5 min |
| **Total** | **~4 hours** |

For batch submission (unattended):
```bash
sbatch raman_workflow/scripts/run_raman_pipeline.sbatch
```
(Edit the sbatch file to set `MATERIAL=hBN_PBEsol_6x6x1` and `-t 06:00:00`.)

---

## Step 3 — Run the Pipeline

```bash
bash raman_workflow/scripts/run_raman_pipeline_interactive.sh hBN_PBEsol_6x6x1
```

The pipeline prints a step banner at the start of each step:

```
╔══════════════════════════════════════════════════════════════════════╗
║  STEP 1 — VASP relaxation (unit cell / defect supercell)             ║
╚══════════════════════════════════════════════════════════════════════╝
```

And a completion summary when done:

```
  ✓ STEP 1 COMPLETE — VASP relaxation (unit cell / defect supercell) (42m 13s)
```

**Monitoring from another window:**
```bash
bash raman_workflow/scripts/show_status.sh $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/workflow.log
```

If the job gets interrupted, re-run the same command — the pipeline resumes from the last incomplete step automatically.

---

## Step 4 — What Happens Internally

### Step 1: Unit-Cell Relaxation

VASP relaxes the hBN primitive cell. With PBEsol:
- Initial lattice constant: a = 2.51 Å
- After relaxation: a ≈ 2.505 Å (PBEsol slightly underbinds relative to LDA)
- Relaxed CONTCAR written to `scf/CONTCAR`
- WAVECAR and CHGCAR saved for subsequent steps

After Step 1, check the relaxed structure:
```bash
head -10 $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/scf/CONTCAR
```

### Step 2: Supercell Generation

Phonopy tiles the relaxed unit cell into a 6×6×1 supercell (72 atoms). The undisplaced supercell (SPOSCAR) is relaxed in `hf/groundstate/` to ensure the supercell is at its energy minimum.

After Step 2, you can visualize the supercell:
```bash
cp $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/hf/SPOSCAR /tmp/POSCAR
# Open in VESTA or ASE
```

Check how many displacement runs were generated:
```bash
ls $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/hf/POSCAR-*
```
For hBN (D6h symmetry), there should be 2: one B displacement and one N displacement. Symmetry reduces 3×72 = 216 degrees of freedom to just 2 independent displacements.

### Step 4: Force Constants

VASP runs in each `hf_POSCAR-*/` directory with a displaced supercell. It computes the force on every atom due to the displacement. For example, `POSCAR-001` has B displaced by +0.03 Å along x. VASP gives the force on every other atom. The restoring force on the displaced atom plus the forces on all neighbors define the interatomic force constants.

During Step 4, you can watch VASP progress:
```bash
tail -f $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/hf/hf_POSCAR-001/OUTCAR | grep "E0"
```

### Step 5: Phonon Postprocessing

Phonopy assembles the force constants into a dynamical matrix and diagonalizes it to find phonon frequencies. For hBN, the key output from `irreps.yaml`:

```
Irreducible representations at the Gamma point:
  E_2g:  1367.5 cm⁻¹  (Raman active)
  B_1u:  1360.3 cm⁻¹  (IR active, not Raman)
  A_2u:    794.2 cm⁻¹  (IR active)
  E_1u:    790.5 cm⁻¹  (IR active)
  E_2g:      0.0 cm⁻¹  (acoustic)
  A_2u:      0.0 cm⁻¹  (acoustic)
```

The E₂g mode at ~1367 cm⁻¹ is the Raman-active mode. Its exact frequency depends on the functional (LDA: ~1370 cm⁻¹, PBEsol: ~1365 cm⁻¹, PBE: ~1355 cm⁻¹).

### Step 7: Resonant Dielectric Runs

For each displaced geometry in `raman/ra_pos_*/`, VASP computes the full dielectric function ε(ω). With `LOPTICS = .TRUE.`, VASP uses the Kubo-Greenwood formula to compute:

```
ε₂(ω) = (2π/V) Σₙₘₖ |<mk|p|nk>|² · δ(ω - (εmk - εnk))
```

This is the imaginary part of the dielectric tensor as a function of photon energy ω. It encodes all optical transitions in the material.

The **change** in ε(ω) between the displaced and undisplaced structures gives the Raman tensor component — how the optical response changes when atoms move.

Step 7 is the most computationally expensive part. For hBN 6×6×1, there are typically 12–24 `ra_pos_*/` directories.

### Step 8: Raman Tensor → Spectrum

The `raman_tensor` binary contracts ε's displacement derivative with the phonon eigenvectors:
```
R_E₂g = Σ_atoms (∂ε/∂u_atom) · e_E₂g(atom)
```

For hBN in XX geometry (in-plane parallel), R_E₂g has a non-zero component. The `broadening` binary convolves the resulting stick spectrum with a Lorentzian (HWHM = 1 cm⁻¹):

```
I(ω) = I_E₂g · (1/π) · 1/[(ω - 1367)² + 1²]
```

The final spectrum is written to `output/raman_broadened_2.33.dat` (for 532 nm laser), etc.

---

## Step 5 — Inspect the Output

After completion, check `output/`:

```bash
ls $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/output/
```

Key files:
- `band.yaml` — phonon frequencies + eigenvectors
- `irreps.yaml` — mode symmetry labels  
- `raman_broadened_2.33.dat` — spectrum at 532 nm
- `*.png` — SpectroPy plots

View the spectrum:
```bash
cat $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/output/raman_broadened_2.33.dat | head -5
# Columns: wavenumber (cm⁻¹), Raman intensity
```

Or use SpectroPy directly for custom plots:
```bash
python3 SpectroPy/generate_raman_plots.py \
    --data $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1/output/ \
    --output ./my_plot.png
```

---

## Step 6 — Interpreting Results

### Expected Raman spectrum

For hBN, you should see:
- A single sharp peak near 1365–1370 cm⁻¹ (E₂g mode)
- No other peaks above noise (other modes are either IR-active or acoustic)
- Resonance enhancement: the intensity should be highest when the laser energy matches the optical gap (~5.9 eV for bulk hBN, ~6.1 eV for monolayer)

### Comparing with experiment

Experimental hBN Raman spectra show the E₂g peak at:
- Bulk: ~1366 cm⁻¹ 
- Few-layer: ~1366 cm⁻¹ (weakly layer-dependent)
- Monolayer: ~1368 cm⁻¹

PBEsol typically gives 1363–1366 cm⁻¹, in excellent agreement.

### Effect of supercell size

To verify convergence, compare with a smaller supercell:

```bash
cp -r $RAMAN_PROJECT_DIR/hBN_PBEsol_6x6x1 $RAMAN_PROJECT_DIR/hBN_PBEsol_4x4x1
# Edit input/workflow_settings.yaml: change dim to "4 4 1", hf/sup_relax to "6 6 1"
bash raman_workflow/scripts/run_raman_pipeline_interactive.sh hBN_PBEsol_4x4x1
```

The E₂g frequency converges quickly with supercell size (< 1 cm⁻¹ difference between 4×4×1 and 6×6×1). The Raman intensity convergence is slower but typically < 5% between 5×5×1 and 6×6×1.

---

## Step 7 — Starting From a New Material

### For a different 2D hexagonal material (e.g., MoS₂, WSe₂)

1. Copy `hBN_PBEsol_6x6x1/` and rename
2. Replace `input/POSCAR` with your structure (primitive cell)
3. Replace `input/POTCAR` with the correct pseudopotentials (element order must match POSCAR)
4. Adjust `workflow_settings.yaml`:
   - `phonopy.dim`: choose based on the lattice constant (aim for ~15 Å supercell size)
   - `scf_kpoints.mesh`: adjust for the new lattice (larger unit cell → coarser mesh needed)
   - `incar_settings`: change `GGA = PS` if using a different functional
5. Run the pipeline

**k-point mesh scaling rule:** If the unit cell lattice constant is a₀ and the reference hBN uses 24×24×1 for a = 2.51 Å, scale as:
```
N_new ≈ 24 × (2.51 / a_new)   # rounded to even number
```
For MoS₂ with a = 3.19 Å: N ≈ 24 × (2.51/3.19) ≈ 18 → use 18×18×1.

### For a 3D material

- Remove the vacuum (set c to actual interlayer spacing)
- Use a 3D k-mesh: `mesh: "12 12 12"` (or appropriate)
- Use a 3D supercell: `dim: "4 4 4"` (or appropriate)
- The rest of the pipeline is unchanged

---

## Appendix: File Structure After Completion

```
hBN_PBEsol_6x6x1/
├── input/
│   ├── POSCAR
│   ├── POTCAR
│   └── workflow_settings.yaml
├── scf/
│   ├── POSCAR, INCAR, KPOINTS, POTCAR
│   ├── CONTCAR         ← relaxed unit cell (step 1 output)
│   ├── WAVECAR         ← electronic wavefunction
│   ├── CHGCAR          ← charge density
│   └── OUTCAR, relaxation.stdout
├── hf/
│   ├── SPOSCAR         ← undisplaced supercell
│   ├── POSCAR-001, POSCAR-002   ← displaced supercells
│   ├── POSCAR_unitcell, FORCE_SETS, band.yaml, irreps.yaml
│   ├── groundstate/    ← relaxed supercell
│   │   ├── CONTCAR, WAVECAR, CHGCAR
│   └── hf_POSCAR-001/, hf_POSCAR-002/  ← force calculation runs
├── raman/
│   ├── POSCAR, INCAR, KPOINTS, POTCAR
│   ├── WAVECAR → ../scf/WAVECAR
│   ├── RDISP           ← Raman displacement pattern
│   ├── ra_pos_001/ ... ra_pos_NNN/  ← displacement dielectric runs
│   └── store_ramfile/RAMFILE_2.33 ...  ← Raman tensors per energy
└── output/
    ├── band.yaml, irreps.yaml, FORCE_SETS
    ├── raman_2.33.dat            ← stick spectrum at 532 nm
    ├── raman_broadened_2.33.dat  ← broadened spectrum
    ├── raman_spectrum_2.33.png   ← plot
    └── incar/                   ← archived INCARs from all steps
```
