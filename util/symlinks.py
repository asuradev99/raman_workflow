"""CHGCAR/WAVECAR symlink management for displacement directories."""

import os


def update_wavecar_symlinks(hffiles_dir, source_subdir="groundstate"):
    """Replace runHF's dangling ``../WAVECAR`` symlinks with correct targets.

    Args:
        hffiles_dir: Path to the hf/ directory.
        source_subdir: Subdirectory containing WAVECAR.  Default ``"groundstate"``;
            use ``"scf"`` for supercell-started runs.
    """
    if source_subdir == "scf":
        rel_target = "../../scf/WAVECAR"
        work_dir = os.path.dirname(hffiles_dir)
        src_path = os.path.join(work_dir, "scf", "WAVECAR")
    else:
        rel_target = "../groundstate/WAVECAR"
        src_path = os.path.join(hffiles_dir, "groundstate", "WAVECAR")

    if not os.path.exists(src_path):
        print(f"  WARNING: {src_path} not found — displacement runs will start from scratch")
        return 0

    displacement_dirs = sorted(
        d for d in os.listdir(hffiles_dir)
        if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(hffiles_dir, d))
    )
    for d in displacement_dirs:
        wav = os.path.join(hffiles_dir, d, "WAVECAR")
        if os.path.islink(wav):
            os.remove(wav)
        os.symlink(rel_target, wav)

    print(f"  Replaced symlinks in {len(displacement_dirs)} displacement dirs:")
    print(f"    hf_POSCAR-*/WAVECAR → {rel_target}")
    return len(displacement_dirs)


def update_chgcar_symlinks(hffiles_dir, source_subdir="groundstate"):
    """Create CHGCAR symlinks in each ``hf_POSCAR-*/``.

    Args:
        hffiles_dir: Path to the hf/ directory.
        source_subdir: Subdirectory containing CHGCAR.  Default ``"groundstate"``;
            use ``"scf"`` for supercell-started runs.
    """
    if source_subdir == "scf":
        rel_target = "../../scf/CHGCAR"
        work_dir = os.path.dirname(hffiles_dir)
        src_path = os.path.join(work_dir, "scf", "CHGCAR")
    else:
        rel_target = "../groundstate/CHGCAR"
        src_path = os.path.join(hffiles_dir, "groundstate", "CHGCAR")

    if not os.path.exists(src_path) and not os.path.islink(src_path):
        print(f"  WARNING: {src_path} not found — displacement runs will start without charge-density seeding")
        return 0

    displacement_dirs = sorted(
        d for d in os.listdir(hffiles_dir)
        if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(hffiles_dir, d))
    )
    for d in displacement_dirs:
        chg = os.path.join(hffiles_dir, d, "CHGCAR")
        if os.path.islink(chg) or os.path.exists(chg):
            os.remove(chg)
        os.symlink(rel_target, chg)

    print(f"  Created CHGCAR symlinks in {len(displacement_dirs)} displacement dirs:")
    print(f"    hf_POSCAR-*/CHGCAR → {rel_target}")
    return len(displacement_dirs)
