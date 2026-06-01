# hBN Functional Comparison

| Functional | `GGA =` | `a` (Å) | ω(A₂″) (cm⁻¹) | ω(E′) (cm⁻¹) | VASP time |
|---|---|---|---|---|---|
| LDA | *(none)* | 2.490 | 819.4 | 1380.0 | 1h 24m |
| PBE | *(none)* | 2.513 | 801.5 | 1337.6 | 1h 23m |
| PBEsol | `PS` | 2.506 | 800.9 | 1357.5 | 1h 22m |
| PBEsol_new | `PS` | 2.506 | 800.9 | 1357.5 | 1h 19m |
| PS | `PS` | 2.506 | 800.9 | 1357.5 | 1h 22m |
| PBE_X | `PBE_X` | 2.555 | 788.5 | 1250.9 | 1h 19m |
| PBE_C | `PBE_C` | 3.246 | 279.3 | 1340.9 | 1h 56m |
| RE | `RE` | 2.522 | 794.1 | 1324.9 | 1h 18m |
| RP | `RP` | 2.525 | 792.2 | 1321.2 | 1h 19m |

- **`a`** = in-plane lattice constant from relaxed CONTCAR (Å)
- **ω(A₂″)** = out-of-plane Raman mode (cm⁻¹), extracted from [`band.yaml`] at Γ
- **ω(E′)** = in-plane E₂g Raman mode (cm⁻¹), extracted from [`band.yaml`] at Γ
- **VASP time** = total wall-clock time summed from all VASP OUTCAR elapsed times (scf relaxation + force constants + Raman displacements) on 4× A100 GPUs. Post-processing steps (phonopy, kopia, RAMFILE) not included.

## Supercell size convergence (PBEsol)

All supercell calculations share the same computational settings, derived from the base [`hBN_PBEsol/input/`](vasp_calculations/hBN_PBEsol/input) template:

| Setting | Value |
|---|---|
| Functional | `GGA = PS` (PBEsol) |
| POTCAR | PBE (B\_GW, N\_GW) |
| Plane-wave cutoff | `ENCUT = 500` eV |
| SCF/relaxation k-point mesh | 36×36×1 Gamma-centred (on primitive cell) |
| Phonon force-constant k-point mesh | See per-supercell values below |
| Electronic convergence | `EDIFF = 1×10⁻⁸` eV |
| Ionic relaxation | `EDIFFG = −0.001` eV/Å, `ISIF = 4` |
| Smearing | `ISMEAR = 0`, `SIGMA = 0.001` eV |
| Dielectric / optics | `LOPTICS = .TRUE.`, `NBANDS = 64`, `OMEGAMAX = 50` |
| Parallelisation | `KPAR = 4`, `NPAR = 1` |
| Starting geometry | LDA-optimised hBN monolayer (from Liangbo) |
| Laser energies | 1.96 eV (633 nm), 2.33 eV (532 nm) |

| Supercell | FC k-mesh | Eff. prim. k-mesh | N_atoms | `a` (Å) | ω(A₂″) (cm⁻¹) | ω(E′) (cm⁻¹) | I(E₂g) @ 1.96 eV | I(E₂g) @ 2.33 eV | VASP time |
|---|---|---|---|---|---|---|---|---|---|
| 3×3×1 | 2×2×1 | 6×6×1 | 18 | 2.506 | 801.2 | 1357.2 | 0.365 | 0.446 | 1h 14m |
| 4×4×1 | 6×6×1 | 24×24×1 | 32 | 2.506 | 800.9 | 1357.5 | 0.366 | 0.446 | 1h 22m |
| 5×5×1 | 1×1×1 | 5×5×1 | 50 | 2.506 | 801.2 | 1358.9 | 0.365 | 0.446 | 2h 8m |
| 6×6×1 | 1×1×1 | 6×6×1 | 72 | 2.506 | 800.8 | 1357.6 | 0.366 | 0.446 | 2h 48m |

- **FC k-mesh** = electronic k-point mesh used in VASP for the supercell force-constant calculations (phonopy finite-displacement method), from [`hf/KPOINTS`]
- **Eff. prim. k-mesh** = equivalent k-point sampling density in the primitive (1×1×1) Brillouin zone, obtained as (FC mesh) × (supercell dimensions)
- **I(E₂g)** = total Raman intensity (sum of both degenerate E₂g components) at the specified laser energy, from [`Raman_intensity_complex`] files
- **N_atoms** = number of atoms in the supercell (B + N). Wall-clock times from `OUTCAR` (summed over scf, force-constant, and Raman-displacement VASP runs; post-processing excluded).
