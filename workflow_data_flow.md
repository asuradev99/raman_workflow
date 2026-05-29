# hBN Raman Workflow — End-to-End Data Flow
> **[DEEPSEEK 2026-05-27]** Updated to document the dual-INCAR design (backward-compatible).
> To revert: restore the original single-INCAR version.

> **Dual-INCAR design**: Each material directory contains **two INCAR templates**:
> - [`INCAR_relax`](INCAR_relax) — For Step 3 structural relaxation (`NSW=100`, `IBRION=2`, `ISIF=4`, no `LOPTICS`, `SIGMA=0.05`, `LCHARG=.TRUE.`)
> - [`INCAR_dielec`](INCAR_dielec) — For all dielectric/displacement VASP runs (`NSW=0`, `LOPTICS=.TRUE.`, `NBANDS=64`, `SIGMA=0.001`)
>
> The script swaps `INCAR_relax` → `INCAR` before Step 3, then `INCAR_dielec` → `INCAR` after Step 3. All subsequent steps use the dielectric INCAR.
>
> **Backward compatible**: If only a single `INCAR` file exists (no `INCAR_relax`/`INCAR_dielec`), the script uses it directly with no swapping — preserving the original behavior for legacy/John Ornl data.

```mermaid
%%{init: {'theme': 'neutral', 'themeVariables': { 'fontSize': '13px'}}}%%
flowchart TD
    subgraph INPUTS["Input Files in hBN/input/"]
        A1["POSCAR<br/>(crystal structure)"]
        A2["INCAR_relax<br/>(NSW=100, for Step 3)"]
        A3["INCAR_dielec<br/>(NSW=0, LOPTICS, for Steps 7,13)"]
        A4["KPOINTS<br/>(k-mesh)"]
        A5["POTCAR<br/>(pseudopotentials)"]
        A6["symmetry.conf<br/>(phonopy symmetry)"]
    end

    subgraph STEP3["Step 3: Initial VASP Relaxation"]
        direction TB
        S3A["① cp input/INCAR_relax → INCAR<br/>Swap in relaxation settings"]
        S3B["② VASP srun<br/>NSW=100, IBRION=2, ISIF=4<br/>Structural relaxation<br/>Output: relaxation.stdout"]
        S3C["③ CONTCAR<br/>Relaxed crystal structure"]
        S3D["④ cp input/INCAR_dielec → INCAR<br/>Swap back to dielectric settings"]
        A1 & A2 --> S3A
        S3A --> S3B
        S3B --> S3C
        A3 --> S3D
    end

    subgraph STEP4["Step 4: Copy to hf/"]
        C1["cp CONTCAR → hf/CONTCAR"]
        C2["cp CONTCAR → hf/POSCAR_unitcell"]
        C3["cp INCAR (now dielec) → hf/"]
        C4["cp input/KPOINTS → hf/"]
        C5["cp input/POTCAR → hf/"]
        C6["cp input/symmetry.conf → hf/"]
        S3C --> C1 & C2
        S3D --> C3
        A4 --> C4
        A5 --> C5
        A6 --> C6
    end

    subgraph STEP5["Step 5: Phonopy Displacements"]
        D1["phonopy -d --dim='4 4 1'<br/>--amplitude=0.03<br/>-c POSCAR_unitcell"]
        D2["POSCAR-001..006<br/>(symmetry-distinct displacements)"]
        D3["phonopy_disp.yaml<br/>(displacement metadata)"]
        C2 --> D1
        D1 --> D2
        D1 --> D3
    end

    subgraph STEP6["Step 6: runHF Organizer"]
        E1["runHF binary"]
        E2["hf_POSCAR-001..006/<br/>(subdirs with INCAR_dielec,<br/>KPOINTS, POTCAR, POSCAR)"]
        D2 & C3 & C4 & C5 & C6 --> E1
        E1 --> E2
    end

    subgraph STEP7["Step 7: Force Calculations"]
        F1["automate_hfiles.sh<br/>(loops hf_POSCAR-*)<br/>srun VASP NSW=0 each"]
        F2["hf_POSCAR-*/vasprun.xml<br/>hf_POSCAR-*/OUTCAR<br/>(forces, total energy)"]
        E2 --> F1
        F1 --> F2
    end

    subgraph STEP8["Step 8: Phonon Post-Processing"]
        G1["phonon_postprocessing binary"]
        G2["FORCE_SETS<br/>(2nd order force constants)"]
        G3["all_mode.txt<br/>(mode list for later use)"]
        F2 --> G1
        G1 --> G2
        G1 --> G3
    end

    subgraph STEP9["Step 9: Phonopy Symmetry"]
        H1["phonopy -c CONTCAR symmetry.conf"]
        H2["band.yaml<br/>(phonon dispersion + eigenvectors)"]
        H3["irreps.yaml<br/>(mode irreducible representations)"]
        C1 & C6 --> H1
        H1 --> H2
        H1 --> H3
    end

    subgraph STEP10_11["Steps 10-11: Prepare Raman dir"]
        I1["mkdir -p raman/<br/>(auto-created)"]
        I2["cp CONTCAR → raman/CONTCAR"]
        I3["cd raman/"]
        S3C --> I2
    end

    subgraph STEP12["Step 12: Resonant Raman Displacements"]
        J1["ramdiscar binary<br/>(generate Raman displacements)"]
        J2["genRApos610 binary<br/>(input: 'go')"]
        J3["runRA binary<br/>(organize into folders)"]
        J4["RA_POSCAR-XXX/<br/>(resonant displacement dirs<br/>with INCAR_dielec, KPOINTS, POTCAR)"]
        I2 --> J1
        J1 --> J2
        J2 --> J3
        J3 --> J4
    end

    subgraph STEP13["Step 13: VASP Resonant Raman"]
        K1["run_all_vasp_folders.sh<br/>srun VASP in each RA dir"]
        K2["RA_POSCAR-XXX/vasprun.xml<br/>(dielectric tensors ε(ω)<br/>for each displacement)"]
        J4 --> K1
        K1 --> K2
    end

    subgraph STEP14_15["Steps 14-15: Extract & Collect"]
        L1["./kopia binary<br/>copy vasprun.xml to AXML/"]
        L2["ramfile_dynamic.sh<br/>generate RAMFILEs for each energy"]
        L3["store_ramfile/RAMFILE_1.96<br/>store_ramfile/RAMFILE_2.33"]
        K2 --> L1
        L1 --> L2
        L2 --> L3
    end

    subgraph STEP17["Step 17: Copy Static Mode Files"]
        M1["cp hf/band.yaml → raman/"]
        M2["cp hf/irreps.yaml → raman/"]
        H2 --> M1
        H3 --> M2
    end

    subgraph STEP18_20["Steps 18-20: Per-Energy Spectrum"]
        N1["For each laser energy X.X:"]
        N2["cp RAMFILE_X.X → RAMFILE"]
        N3["raman_tensor binary<br/→ Raman tensor components"]
        N4["broadening binary<br/→ Lorentzian broadened spectrum"]
        N5["Raman_intensity_complex_X.XeV"]
        N6["Raman_intensity_complex_broadening_X.XeV"]
        L3 --> N1
        N1 --> N2
        N2 --> N3
        N3 --> N4
        N4 --> N5
        N4 --> N6
    end

    subgraph OUTPUT["Final Outputs in raman/"]
        O1["Raman_intensity_complex_1.96eV"]
        O2["Raman_intensity_complex_2.33eV"]
        O3["Raman_intensity_complex_broadening_1.96eV"]
        O4["Raman_intensity_complex_broadening_2.33eV"]
        N5 --> O1 & O2
        N6 --> O3 & O4
    end
```

## File Inventory Per Step

| Step | Script / Binary | Input Files | Output Files |
|------|----------------|-------------|--------------|
| 3 | `cp` + `srun vasp_std` + `cp` | POSCAR, **input/INCAR_relax**, **input/INCAR_dielec**, input/KPOINTS, input/POTCAR | CONTCAR, relaxation.stdout (INCAR swapped back to dielec after); other generated files moved to scf/ (CHGCAR, WAVECAR, DOSCAR, OUTCAR, etc.) |
| 4 | `cp` commands | CONTCAR, INCAR (dielec), input/KPOINTS, input/POTCAR, input/symmetry.conf | hf/{CONTCAR, POSCAR_unitcell, INCAR, KPOINTS, POTCAR, symmetry.conf} |
| 5 | `phonopy -d` | POSCAR_unitcell | POSCAR-001..006, phonopy_disp.yaml |
| 6 | `runHF` | POSCAR-*, INCAR, KPOINTS, POTCAR, symmetry.conf | hf_POSCAR-*/ subdirs |
| 7 | `automate_hfiles.sh` | hf_POSCAR-*/ (INCAR_dielec, KPOINTS, POTCAR) | hf_POSCAR-*/vasprun.xml, OUTCAR |
| 8 | `phonon_postprocessing` | vasprun.xml files | FORCE_SETS, all_mode.txt |
| 9 | `phonopy -c CONTCAR symmetry.conf` | CONTCAR, symmetry.conf | band.yaml, irreps.yaml |
| 10-11 | `cp` + `cd` | CONTCAR | raman/CONTCAR |
| 12 | `ramdiscar`, `genRApos610`, `runRA` | CONTCAR | RA_POSCAR-*/ subdirs |
| 13 | `run_all_vasp_folders.sh` | RA_POSCAR-*/ (INCAR_dielec, KPOINTS, POTCAR) | RA_POSCAR-*/vasprun.xml |
| 14 | `kopia` | vasprun.xml files | AXML/ |
| 15 | `ramfile_dynamic.sh` | AXML/ | store_ramfile/RAMFILE_1.96, RAMFILE_2.33 |
| 17 | `cp` | hf/band.yaml, irreps.yaml | raman/band.yaml, raman/irreps.yaml |
| 18-20 | `raman_tensor` + `broadening` | RAMFILE, band.yaml, irreps.yaml | Raman_intensity_complex_X.XeV, Raman_intensity_complex_broadening_X.XeV |

## INCAR Comparison

| INCAR Tag | [`INCAR_relax`](INCAR_relax) | [`INCAR_dielec`](INCAR_dielec) | Purpose |
|-----------|:---:|:---:|---------|
| `NSW` | **100** | **0** | Relax: allow ionic movement; Dielec: single-point |
| `IBRION` | 2 | 2 | Conjugate-gradient (only active when NSW>0) |
| `ISIF` | 4 | 4 | Relax ions + cell shape (only active when NSW>0) |
| `EDIFF` | **1E-07** | **1E-08** | Dielec needs tighter electronic convergence for ε(ω) |
| `SIGMA` | **0.05** | **0.001** | Relax: broader smearing helps convergence; Dielec: cold smearing for accuracy |
| `LOPTICS` | ***(absent)*** | **.TRUE.** | Only compute ε(ω) in displacement VASP runs |
| `LCHARG` | **.TRUE.** | **.FALSE.** | Save charge density during relaxation; skip for dielectric runs |
| `LWAVE` | **.TRUE.** | **.FALSE.** | Save wavefunctions during relaxation; skip for dielectric runs |
| `NBANDS` | *(default)* | **64** | Explicit bands needed for LOPTICS (empty states) |
| `NEDOS` | *(default)* | **50001** | Fine energy grid for dielectric function |
| `OMEGAMAX` | *(default)* | **50** | Calculate ε(ω) up to 50 eV |
| `ISTART` | **0** | **1** | Fresh start for relaxation; try reading WAVECAR for dielec |
| `ICHARG` | **2** | **1** | Start from atomic densities; read CHGCAR for dielec |
