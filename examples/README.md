# Examples — Starting Template for New Materials

Copy the `hBN_PBEsol_6x6x1` directory to your project area:

```bash
cp -r examples/hBN_PBEsol_6x6x1 $RAMAN_PROJECT_DIR/my_material
```

## What Goes in `input/`

Each material needs only 3 files:

| File | Purpose |
|------|---------|
| `POSCAR` | Crystal structure (conventional or primitive cell) |
| `POTCAR` | VASP pseudopotentials for each element |
| `workflow_settings.yaml` | Per-material overrides (supercell size, k-points, functional) |

Everything else — INCARs, KPOINTS, symmetry.conf — is **auto-generated from YAML config**.

## Per-Material Config

Edit `workflow_settings.yaml` to set:

```yaml
phonopy:
  dim: "6 6 1"       # Supercell dimensions
scf_kpoints:
  mesh: "24 24 1"    # Fine k-point mesh for unit cell
hf_kpoints:
  mesh: "4 4 1"      # Coarse mesh for force constants (≈ fine ÷ dim)
sup_relax_kpoints:
  mesh: "4 4 1"      # Coarse mesh for supercell relaxation
raman_kpoints:
  mesh: "24 24 1"    # Fine mesh for Raman calculation
incar_settings:       # Functional-specific VASP tags (optional)
  relax: |
    GGA = PS
  dielec: |
    GGA = PS
```

## Shared Config

`shared_workflow_settings.yaml` (copy this to `$RAMAN_PROJECT_DIR/`) provides
defaults for all materials — srun args, polarization, energies, INCAR templates.
Per-material configs override only what differs.

## Supercell Starting Point

If your POSCAR is already a supercell (e.g., defect systems), add:

```yaml
start_from_supercell: true
phonopy:
  dim: "1 1 1"
```

This skips the supercell relaxation step and seeds force constants from the
unit-cell relaxation.
