"""CHGCAR/WAVECAR symlink management for displacement directories.

Both ``update_hf_symlinks`` and ``update_raman_symlinks`` create *read-only*
symlinks — the target INCAR templates must have ``LCHARG = .FALSE.`` and
``LWAVE = .FALSE.`` so VASP reads from but never writes back through the link.
If either flag is ever set to ``.TRUE.`` in the ``hf`` or ``dielec`` templates,
VASP will overwrite the source files (groundstate CHGCAR or scf CHGCAR) and
corrupt future runs.
"""

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


def update_raman_symlinks(raman_dir, work_dir):
    """Create/replace WAVECAR and CHGCAR symlinks in all ra_pos_* dirs.

    Source is always ``scf/`` regardless of whether this is a pristine or
    defected run:

      - **Pristine**: ``scf/`` holds the unit-cell relaxation CHGCAR/WAVECAR,
        which seeds the dielectric calculation on each displaced unit cell.
      - **Defected**: ``scf/`` holds the defect-supercell relaxation
        CHGCAR/WAVECAR, which seeds each displaced defect supercell.

    The ``dielec`` INCAR used in ``ra_pos_*/`` has ``LCHARG = .FALSE.`` and
    ``LWAVE = .FALSE.``, so VASP reads from but never writes back through these
    symlinks.  See module-level docstring for the safety invariant.
    """
    _LINKS = [
        ("WAVECAR", "resonant VASP runs will start without wavefunction seeding"),
        ("CHGCAR",  "resonant VASP runs will start without charge-density seeding"),
    ]
    # ra_pos_*/ sits two levels below work_dir:  work_dir/raman/ra_pos_XYZ/
    # so ../../scf/<file> correctly resolves to work_dir/scf/<file>.

    for filename, warn_msg in _LINKS:
        rel_target = f"../../scf/{filename}"
        src_path = os.path.join(work_dir, "scf", filename)

        if not os.path.exists(src_path):
            print(f"  WARNING: {src_path} not found — {warn_msg}")
            continue

        dirs = sorted(
            d for d in os.listdir(raman_dir)
            if d.startswith("ra_pos_") and os.path.isdir(os.path.join(raman_dir, d))
        )
        for d in dirs:
            link = os.path.join(raman_dir, d, filename)
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            os.symlink(rel_target, link)

        print(f"  {filename} symlinks → {rel_target} in {len(dirs)} ra_pos_* dirs")
