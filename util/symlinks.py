"""CHGCAR/WAVECAR symlink management for displacement directories."""

import os


def update_hf_symlinks(hffiles_dir, source_subdir="groundstate"):
    """Create/replace WAVECAR and CHGCAR symlinks in all hf_POSCAR-* dirs.

    Args:
        hffiles_dir: Path to the hf/ directory.
        source_subdir: Subdirectory containing the charge/wave files.
            ``"groundstate"`` (default) for normal runs; ``"scf"`` when
            ``start_from_supercell`` skipped the groundstate relaxation.
    """
    _LINKS = [
        ("WAVECAR", "displacement runs will start from scratch"),
        ("CHGCAR",  "displacement runs will start without charge-density seeding"),
    ]
    work_dir = os.path.dirname(hffiles_dir)

    for filename, warn_msg in _LINKS:
        if source_subdir == "scf":
            rel_target = f"../../scf/{filename}"
            src_path = os.path.join(work_dir, "scf", filename)
        else:
            rel_target = f"../groundstate/{filename}"
            src_path = os.path.join(hffiles_dir, "groundstate", filename)

        if not os.path.exists(src_path):
            print(f"  WARNING: {src_path} not found — {warn_msg}")
            continue

        dirs = sorted(
            d for d in os.listdir(hffiles_dir)
            if d.startswith("hf_POSCAR-") and os.path.isdir(os.path.join(hffiles_dir, d))
        )
        for d in dirs:
            link = os.path.join(hffiles_dir, d, filename)
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            os.symlink(rel_target, link)

        print(f"  {filename} symlinks → {rel_target} in {len(dirs)} dirs")


# Backward-compatible aliases kept for any external scripts that call the old names.
def update_wavecar_symlinks(hffiles_dir, source_subdir="groundstate"):
    """Deprecated: use update_hf_symlinks instead."""
    update_hf_symlinks(hffiles_dir, source_subdir)


def update_chgcar_symlinks(hffiles_dir, source_subdir="groundstate"):
    """Deprecated: use update_hf_symlinks instead."""
    update_hf_symlinks(hffiles_dir, source_subdir)
