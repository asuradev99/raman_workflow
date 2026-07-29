#!/usr/bin/env python3
"""
Generate VESTA (.vesta) visualizations of crystal symmetry operations for any
material, given only a relaxed structure file (POSCAR/CONTCAR).

For each requested symmetry operation type (default: mirror planes), and for
each inequivalent atom, this finds the destination atom the operation maps
the source atom to (itself, for a true site-symmetry operation; a different
atom, for a general space-group operation relating two atoms) and writes one
.vesta file showing:
  - the unit cell structure
  - a displacement arrow (red) on the source atom
  - that displacement transformed by the operation, drawn on the destination
    atom (blue) -- or the same arrow re-colored if source == destination
  - the mirror/glide plane, for reflection-type operations

Requires only numpy + spglib (both present in phonopy_env).

Usage:
  visualize_site_symmetry.py POSCAR --outdir output/ --material MoS2
  visualize_site_symmetry.py CONTCAR --op-types mirror,inversion --atoms 0,2
  visualize_site_symmetry.py CONTCAR --op-types all --direction 0,0,1
"""
import argparse
import os
import numpy as np

try:
    import spglib
except ImportError:
    raise SystemExit(
        "spglib is required (source ~/phonopy_env/bin/activate, or pip install spglib)"
    )


def read_poscar(path):
    with open(path) as f:
        lines = f.readlines()
    scale = float(lines[1].split()[0])
    lattice = np.array([[float(x) for x in lines[i].split()[:3]] for i in (2, 3, 4)])
    if scale < 0:
        # negative scale = target cell volume, not a linear scale factor
        vol = abs(np.linalg.det(lattice))
        lattice *= (abs(scale) / vol) ** (1 / 3)
    else:
        lattice *= scale
    species = lines[5].split()
    counts = [int(x) for x in lines[6].split()]
    idx = 7
    if lines[idx].strip()[:1].lower() in ("s",):
        idx += 1
    direct = lines[idx].strip()[:1].lower().startswith("d")
    idx += 1
    natoms = sum(counts)
    positions = np.array([[float(x) for x in lines[idx + i].split()[:3]] for i in range(natoms)])
    if not direct:
        positions = positions @ np.linalg.inv(lattice)
    symbols = []
    for sp, c in zip(species, counts):
        symbols.extend([sp] * c)
    return lattice, positions % 1.0, symbols


def cartesian_rotation(R_frac, lattice):
    """Cartesian rotation for fractional-coordinate convention x' = R_frac @ x + t,
    with `lattice` rows = a, b, c (cart = frac @ lattice)."""
    L = lattice
    return L.T @ R_frac @ np.linalg.inv(L.T)


def classify_op(Rc):
    I = np.eye(3)
    if np.allclose(Rc, I, atol=1e-4):
        return "identity", None
    det = round(np.linalg.det(Rc))
    order2 = np.allclose(Rc @ Rc, I, atol=1e-4)
    if det > 0:
        if order2:
            return "C2", None
        angle = np.degrees(np.arccos(np.clip((np.trace(Rc) - 1) / 2, -1, 1)))
        return f"C{angle:.0f}", None
    if order2:
        if np.allclose(Rc, -I, atol=1e-4):
            return "inversion", None
        w, v = np.linalg.eigh(Rc)
        normal = v[:, np.argmin(w)].real
        return "mirror", normal / np.linalg.norm(normal)
    angle = np.degrees(np.arccos(np.clip((np.trace(Rc) + 1) / 2, -1, 1)))
    return f"S{angle:.0f}", None


OP_GROUPS = {
    "identity": {"identity"},
    "inversion": {"inversion"},
    "mirror": {"mirror"},
}


def op_group_matches(kind, wanted):
    if "all" in wanted:
        return True
    if kind in wanted:
        return True
    if kind.startswith("C") and kind != "C2" and "rotation" in wanted:
        return True
    if kind == "C2" and ("rotation" in wanted or "c2" in wanted):
        return True
    if kind.startswith("S") and "improper" in wanted:
        return True
    return False


def find_dest_atom(frac_positions, symbols, src, R_frac, t, symprec):
    x = R_frac @ frac_positions[src] + t
    for j, xj in enumerate(frac_positions):
        if symbols[j] != symbols[src]:
            continue
        d = x - xj
        d -= np.round(d)
        if np.linalg.norm(d) < max(symprec * 5, 1e-3):
            return j
    return None


def plane_reduced_miller(normal_cart, lattice):
    hkl = lattice @ normal_cart  # (n.a, n.b, n.c)
    nz = hkl[np.abs(hkl) > 1e-3]
    if len(nz):
        hkl = hkl / np.min(np.abs(nz))
    for scale in (1, 2, 3, 4, 6):
        cand = hkl * scale
        if np.allclose(cand, np.round(cand), atol=1e-2):
            return np.round(cand)
    return hkl


def plane_distance(src_frac, R_frac, t, lattice, normal_cart):
    """Distance of the mirror plane from the origin, found as the midpoint
    between the source atom and its *unwrapped* (non-periodic) image -- the
    naive fixed-point solve of R x + t = x is ambiguous modulo the lattice
    (e.g. z -> -z has fixed points at both z=0 and z=1/2), so anchoring to
    an atom actually on the plane avoids picking the wrong periodic image."""
    src_cart = src_frac @ lattice
    image_frac_raw = R_frac @ src_frac + t
    # fold to the periodic image nearest src -- the raw solution can be off
    # by a full lattice vector (e.g. z=-0.5 vs z=0.5 are the same site) which
    # would otherwise put the "plane" a whole lattice vector away from where
    # it actually sits
    image_frac = image_frac_raw - np.round(image_frac_raw - src_frac)
    image_cart = image_frac @ lattice
    midpoint = (src_cart + image_cart) / 2
    return float(normal_cart @ midpoint)


STRUC_LINE = "  {i:d}  {el:<2s}       {lab:>4s}  1.0000   {x:.6f}   {y:.6f}   {z:.6f}    1a       1\n"
STRUC_LINE2 = "                            0.000000   0.000000   0.000000  0.00\n"
THERI_LINE = "  {i:d}        {lab:>4s} -0.000000\n"
SITET_LINE = "  {i:d}        {lab:>4s}  0.9000  76 175 194  76 175 194 204  0\n"
ATOMT_LINE = "  {i:d}         {el:<2s}  0.9000  76 175 194  76 175 194 204\n"

FOOTER = """LBLAT
 -1
LBLSP
 -1
DLATM
 -1
DLBND
 -1
DLPLY
 -1
PLN2D
  0   0   0   0
ATOMT
{atomt}  0 0 0 0 0 0
SCENE
 1.000000  0.000000  0.000000  0.000000
 0.000000  1.000000  0.000000  0.000000
 0.000000  0.000000  1.000000  0.000000
 0.000000  0.000000  0.000000  1.000000
  0.000   0.000
  0.000
  1.000
HBOND 0 2

STYLE
DISPF 37753794
MODEL   0  1  0
SURFS   0  1  1
SECTS  32  1
FORMS   0  1
ATOMS   0  0  1
BONDS   1
POLYS   1
VECTS 1.000000
FORMP
  1  1.0   0   0   0
ATOMP
 24  24   0  50  2.0   0
BONDP
  1  16  0.250  2.000 127 127 127
POLYP
 204 1  1.000 180 180 180
ISURF
  0   0   0   0
TEX3P
  1  0.00000E+00  1.00000E+00
SECTP
  1  0.00000E+00  1.00000E+00  0.00000E+00  0.00000E+00  0.00000E+00  0.00000E+00
CONTR
 0.1 -1 1 1 10 -1 2 5
 2 1 2 1
   0   0   0
   0   0   0
   0   0   0
   0   0   0
HKLPP
 192 1  1.000 255   0 255
UCOLP
   0   1  1.000   0   0   0
COMPS 1
LABEL 1    12  1.000 0
PROJT 0  0.962
BKGRC
 255 255 255
DPTHQ 1 -0.5000  3.5000
LIGHT0 1
 1.000000  0.000000  0.000000  0.000000
 0.000000  1.000000  0.000000  0.000000
 0.000000  0.000000  1.000000  0.000000
 0.000000  0.000000  0.000000  1.000000
 0.000000  0.000000 20.000000  0.000000
 0.000000  0.000000 -1.000000
  26  26  26 255
 179 179 179 255
 255 255 255 255
LIGHT1
 1.000000  0.000000  0.000000  0.000000
 0.000000  1.000000  0.000000  0.000000
 0.000000  0.000000  1.000000  0.000000
 0.000000  0.000000  0.000000  1.000000
 0.000000  0.000000 20.000000  0.000000
 0.000000  0.000000 -1.000000
   0   0   0   0
   0   0   0   0
   0   0   0   0
LIGHT2
 1.000000  0.000000  0.000000  0.000000
 0.000000  1.000000  0.000000  0.000000
 0.000000  0.000000  1.000000  0.000000
 0.000000  0.000000  0.000000  1.000000
 0.000000  0.000000 20.000000  0.000000
 0.000000  0.000000 -1.000000
   0   0   0   0
   0   0   0   0
   0   0   0   0
LIGHT3
 1.000000  0.000000  0.000000  0.000000
 0.000000  1.000000  0.000000  0.000000
 0.000000  0.000000  1.000000  0.000000
 0.000000  0.000000  0.000000  1.000000
 0.000000  0.000000 20.000000  0.000000
 0.000000  0.000000 -1.000000
   0   0   0   0
   0   0   0   0
   0   0   0   0
SECCL 0

TEXCL 0

ATOMM
 204 204 204 255
  25.600
BONDM
 255 255 255 255
 128.000
POLYM
 255 255 255 255
 128.000
SURFM
   0   0   0 255
 128.000
FORMM
 255 255 255 255
 128.000
HKLPM
 255 255 255 255
 128.000
"""


def write_vesta(path, title, lattice, frac_positions, symbols, src, dst,
                 v_src_cart, v_dst_cart, plane_normal_cart, plane_dist, arrow_scale):
    n = len(symbols)
    labels = [f"{sym}{symbols[:i+1].count(sym)}" for i, sym in enumerate(symbols)]

    a, b, c = [np.linalg.norm(v) for v in lattice]
    alpha = np.degrees(np.arccos(np.dot(lattice[1], lattice[2]) / (b * c)))
    beta = np.degrees(np.arccos(np.dot(lattice[0], lattice[2]) / (a * c)))
    gamma = np.degrees(np.arccos(np.dot(lattice[0], lattice[1]) / (a * b)))

    struc = []
    theri = []
    sitet = []
    for i in range(n):
        x, y, z = frac_positions[i]
        struc.append(STRUC_LINE.format(i=i + 1, el=symbols[i], lab=labels[i], x=x, y=y, z=z))
        struc.append(STRUC_LINE2)
        theri.append(THERI_LINE.format(i=i + 1, lab=labels[i]))
        sitet.append(SITET_LINE.format(i=i + 1, lab=labels[i]))

    species_set = sorted(set(symbols))
    atomt = "".join(ATOMT_LINE.format(i=i + 1, el=sp) for i, sp in enumerate(species_set))

    sbond = ""
    if len(species_set) >= 2:
        el1, el2 = species_set[0], species_set[1]
        sbond = f"  1     {el1:<4s}{el2:<4s} 0.00000    3.00000  0  1  1  0  1  0.250  2.000 127 127 127\n"

    vectr = []
    vectt = []
    vid = 1
    for i in range(n):
        if i == src:
            v = v_src_cart
        elif i == dst and dst != src:
            v = v_dst_cart
        else:
            v = np.zeros(3)
        vectr.append(f"{vid:5d}  {v[0]:9.5f} {v[1]:9.5f} {v[2]:9.5f} 1\n")
        vectr.append(f"{vid:6d}   0    0    0    0\n")
        vectr.append(" 0 0 0 0 0\n")
        color = (220, 20, 20) if i == src else (20, 80, 220) if (i == dst and dst != src) else (128, 128, 128)
        flag = 1 if np.allclose(v, 0) else 2
        vectt.append(f"{vid:6d} 0.300 {color[0]:3d} {color[1]:3d} {color[2]:3d} {flag}\n")
        vid += 1
    vectr.append(" 0 0 0 0 0\n")
    vectt.append(" 0 0 0 0 0\n")

    if plane_normal_cart is not None:
        h, k, l = plane_normal_cart
        splan = f"  1 {h:.6E} {k:.6E} {l:.6E} {plane_dist:.5f} 255   0 255 192\n  0   0   0   0\n"
    else:
        splan = "  0   0   0   0\n"

    with open(path, "w") as f:
        f.write("#VESTA_FORMAT_VERSION 3.5.4\n\n\nCRYSTAL\n\nTITLE\n")
        f.write(title + "\n\n")
        f.write("GROUP\n1 1 P 1\nSYMOP\n0 0 0 1 0 0 0 1 0 0 0 1 1\n")
        f.write(" -1.0 -1.0 -1.0  0 0 0  0 0 0  0 0 0\nTRANM 0\n")
        f.write("0 0 0 1 0 0 0 1 0 0 0 1 1\nLTRANSL\n -1\n")
        f.write(" 0.000000  0.000000  0.000000  0.000000  0.000000  0.000000\n")
        f.write("LORIENT\n -1   0   0   0   0\n")
        f.write(f" 1.000000  0.000000  0.000000 {a*10:.6f}  0.000000  0.000000\n")
        f.write(f" 0.000000  0.000000  1.000000  0.000000 -0.000000 {c*10:.6f}\n")
        f.write("LMATRIX\n 1.000000  0.000000  0.000000  0.000000\n")
        f.write(" 0.000000  1.000000  0.000000  0.000000\n 0.000000  0.000000  1.000000  0.000000\n")
        f.write(" 0.000000  0.000000  0.000000  1.000000\n 0.000000  0.000000  0.000000\n")
        f.write("PHASON\n 1.000000  0.000000  0.000000\n 0.000000  1.000000  0.000000\n 0.000000  0.000000  1.000000\n")
        f.write(f"CELLP\n  {a:.6f}   {b:.6f}   {c:.6f}  {alpha:.6f}  {beta:.6f}  {gamma:.6f}\n")
        f.write("  0.000000   0.000000   0.000000   0.000000   0.000000   0.000000\n")
        f.write("STRUC\n" + "".join(struc) + "  0 0 0 0 0 0 0\n")
        f.write("THERI 1\n" + "".join(theri) + "  0 0 0\n")
        f.write("SHAPE\n  0       0       0       0   0.000000  0   192   192   192   192\n")
        f.write("BOUND\n0 1 0 1 0 1 \n  0   0   0   0  0\n")
        f.write("QCORIG\n        0         0         0\n")
        f.write("SBOND\n" + sbond + "  0 0 0 0\n")
        f.write("SITET\n" + "".join(sitet) + "  0 0 0 0 0 0\n")
        f.write("VECTR\n" + "".join(vectr))
        f.write("VECTT\n" + "".join(vectt))
        f.write("SPLAN\n" + splan)
        f.write(FOOTER.format(atomt=atomt))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("structure", help="POSCAR or CONTCAR (relaxed structure)")
    ap.add_argument("--outdir", default="output", help="directory to write .vesta files to")
    ap.add_argument("--material", default=None, help="name prefix for output files (default: derived from structure path)")
    ap.add_argument("--op-types", default="mirror",
                     help="comma list from: identity,mirror,inversion,rotation,c2,improper,all (default: mirror)")
    ap.add_argument("--atoms", default="inequivalent",
                     help="'inequivalent' (default, one representative per orbit) or comma list of 0-based atom indices")
    ap.add_argument("--direction", default="1,0,0", help="cartesian displacement direction, comma-separated (default: 1,0,0)")
    ap.add_argument("--arrow-length", type=float, default=0.6, help="displacement arrow length in Angstrom (default 0.6)")
    ap.add_argument("--symprec", type=float, default=1e-4)
    args = ap.parse_args()

    lattice, frac, symbols = read_poscar(args.structure)
    material = args.material or os.path.basename(os.path.dirname(os.path.abspath(args.structure))) or "material"
    os.makedirs(args.outdir, exist_ok=True)

    unique_syms = sorted(set(symbols))
    numbers = [unique_syms.index(s) + 1 for s in symbols]
    cell = (lattice, frac, numbers)
    dataset = spglib.get_symmetry_dataset(cell, symprec=args.symprec)
    rotations, translations = dataset.rotations, dataset.translations
    equiv = dataset.equivalent_atoms

    wanted = set(t.strip().lower() for t in args.op_types.split(","))

    if args.atoms == "inequivalent":
        src_atoms = sorted(set(equiv))
    else:
        src_atoms = [int(x) for x in args.atoms.split(",")]

    v_dir = np.array([float(x) for x in args.direction.split(",")])
    v_dir = v_dir / np.linalg.norm(v_dir)

    n_written = 0
    for src in src_atoms:
        for op_idx, (R_frac, t) in enumerate(zip(rotations, translations)):
            Rc = cartesian_rotation(R_frac, lattice)
            kind, normal = classify_op(Rc)
            if kind == "identity":
                continue
            if not op_group_matches(kind, wanted):
                continue

            dst = find_dest_atom(frac, symbols, src, R_frac, t, args.symprec)
            if dst is None:
                continue

            v_src_cart = v_dir * args.arrow_length
            v_dst_cart = Rc @ v_src_cart

            plane_hkl = plane_dist = None
            if kind == "mirror":
                plane_hkl = plane_reduced_miller(normal, lattice)
                plane_dist = plane_distance(frac[src], R_frac, t, lattice, normal)

            fname = f"{material}_symmetry_op{op_idx}_{kind}_atom{src}.vesta"
            path = os.path.join(args.outdir, fname)
            title = (f"{material} symmetry op{op_idx} ({kind}): "
                     f"atom{src} disp (red) -> atom{dst} transformed (blue)")
            write_vesta(path, title, lattice, frac, symbols, src, dst,
                        v_src_cart, v_dst_cart, plane_hkl, plane_dist, args.arrow_length)
            n_written += 1

    print(f"Wrote {n_written} VESTA files to {args.outdir}/")


if __name__ == "__main__":
    main()
