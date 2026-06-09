"""KPOINTS file generation."""


def write_kpoints(path, comment, mesh, shift):
    """Write a Gamma-centred KPOINTS file."""
    with open(path, "w") as f:
        f.write(f"{comment}\n")
        f.write("0\n")
        f.write("Gamma\n")
        f.write(f"{mesh}\n")
        f.write(f"{shift}\n")
