#!/usr/bin/env python3
"""Lightweight Raman-pipeline generator.

Reads the layered YAML config for one material and emits self-contained bash
scripts (a runner + one script per step) into the material's work directory.
Everything config-derived is baked in as literals; the generated bash has no
dependency on this repo except that it sources new/common.sh and calls
new/check_*.py by absolute path.

    python3 generate.py <material_dir> [--no-scratch] [--cpu] [--debug]

Runs on $SCRATCH by default (output copied back to the material dir); pass
--no-scratch to run directly in the material dir.

This file is standalone: no imports from the old src/ or util/ packages. The
config-merge and INCAR-build logic below are ported (not imported) from
util/config.py and util/incar.py.
"""
import argparse
import os
import sys

import yaml

REPO_NEW = os.path.dirname(os.path.abspath(__file__))          # .../raman_workflow/new
COMMON_SH = os.path.join(REPO_NEW, "common.sh")
CHECK_CONV = os.path.join(REPO_NEW, "check_convergence.py")
CHECK_DIEL = os.path.join(REPO_NEW, "check_dielectric.py")


# =============================================================================
#  Config layer  (ported from util/config.py)
# =============================================================================
def merge_config(target, incoming):
    """Deep-merge *incoming* into *target*; dicts recurse, scalars/lists replace,
    keys starting with '_' skipped."""
    if incoming is None:
        return
    for k, v in incoming.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            merge_config(target[k], v)
        else:
            target[k] = v


def load_config(paths):
    cfg = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            merge_config(cfg, yaml.safe_load(f))
        print(f"Loaded config: {path}")
    return cfg


# Only the two surviving compute modes.
COMPUTE_MODE_REQUIRED = {
    "interactive": ["srun_relax", "srun_per_dir"],
    "interactive_manual": ["srun_relax", "srun_per_dir"],   # alias
    "sbatch_mix": ["srun_relax", "srun_per_dir", "sbatch"],
}
_DELETED_MODES = {"sbatch_parallel", "sbatch_serial", "interactive_serial", "sbatch"}


def validate_config(cfg, step_names):
    missing = []
    mode = cfg.get("compute_mode")
    if mode is None:
        sys.exit(
            "ERROR: compute_mode is not set in config.\n"
            "       Set compute_mode: \"interactive\" or \"sbatch_mix\" in "
            "shared_workflow_settings.yaml or the per-material config."
        )
    if mode in _DELETED_MODES:
        sys.exit(
            f"ERROR: compute_mode '{mode}' is not supported by the new pipeline.\n"
            f"       Use 'interactive' or 'sbatch_mix'."
        )
    if mode not in COMPUTE_MODE_REQUIRED:
        sys.exit(f"ERROR: unknown compute_mode '{mode}'. Use 'interactive' or 'sbatch_mix'.")

    mode_cfg = cfg.get("compute_modes", {}).get(mode, {})
    if not mode_cfg:
        missing.append(f"compute_modes.{mode} section missing")
    else:
        for key in COMPUTE_MODE_REQUIRED[mode]:
            if key not in mode_cfg:
                missing.append(f"compute_modes.{mode}.{key} missing")

    for sect in ("phonopy", "system_paths", "steps"):
        if sect not in cfg:
            missing.append(f"[{sect}] section missing")

    known = set(STEP_ORDER) | {"defect_relax_1", "defect_relax_2", "defect_relax_2_cpu"}
    for name in step_names:
        if name not in known:
            missing.append(f"unknown step '{name}'")

    if missing:
        print("ERROR: config problems:")
        for m in missing:
            print("  " + m)
        sys.exit(1)


# =============================================================================
#  INCAR layer  (ported from util/incar.py)
# =============================================================================
def _parse_incar(text):
    tags = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            tag, val = line.split("=", 1)
            tags[tag.strip()] = val.strip()
    return tags


def _format_incar(tags):
    return "\n".join(f"{t} = {v}" for t, v in tags.items())


def build_incar_content(cfg, step_name, debug_overrides=None):
    steps = cfg.get("steps", {})
    template_text = steps.get(step_name, {}).get("incar", "")
    if not template_text:
        raise KeyError(f"Missing steps.{step_name}.incar (have: {list(steps.keys())})")
    override_text = steps.get(step_name, {}).get("incar_overrides", "")

    template_tags = _parse_incar(template_text)
    override_tags = _parse_incar(override_text) if override_text else {}
    if debug_overrides:
        override_tags.update(debug_overrides)
    if not override_tags:
        return template_text.strip() + "\n"
    for tag in override_tags:
        template_tags.pop(tag, None)
    parts = [_format_incar(override_tags)]
    if template_tags:
        parts.append(_format_incar(template_tags))
    return "\n".join(parts) + "\n"


def kpoints_content(comment, mesh, shift):
    return f"{comment}\n0\nGamma\n{mesh}\n{shift}\n"


# =============================================================================
#  Step ordering
# =============================================================================
STEP_ORDER = [
    "scf_relax", "supercell", "hf_setup", "force_consts",
    "phonon_post", "raman_prep", "resonant_vasp", "post_process",
]
# steps that need a GPU allocation
COMPUTE_STEPS = {"scf_relax", "supercell", "defect_relax_1", "defect_relax_2",
                 "defect_relax_2_cpu", "force_consts", "resonant_vasp"}


# =============================================================================
#  Bake dict  (replaces PipelineContext.__post_init__)
# =============================================================================
def resolve_binary(cfg, cpu_flag):
    sp = cfg.get("system_paths", {})
    gam = cfg.get("use_gam", False)
    key = {
        (False, False): "vasp_binary",
        (False, True): "vasp_binary_gam",
        (True, False): "vasp_binary_cpu",
        (True, True): "vasp_binary_gam_cpu",
    }[(cpu_flag, gam)]
    env = {
        (False, False): "VASP_BINARY",
        (False, True): "VASP_BINARY_GAM",
        (True, False): "VASP_BINARY_CPU",
        (True, True): "VASP_BINARY_GAM_CPU",
    }[(cpu_flag, gam)]
    return os.environ.get(env) or sp.get(key, "")


def _kp(cfg, step, key, default=""):
    return cfg.get("steps", {}).get(step, {}).get("kpoints", {}).get(key, default)


def build_bake(cfg, cpu_flag, home_output_dir):
    sp = cfg.get("system_paths", {})
    mode = cfg.get("compute_mode", "interactive")
    mode_cfg = cfg.get("compute_modes", {}).get(mode, {})
    post = cfg.get("steps", {}).get("post_process", {})
    rt = post.get("raman_tensor", {})
    brd = post.get("broadening", {})
    symf = post.get("symmetry_filter", {})

    b = {
        "COMPUTE_MODE": mode,
        "BIN": os.environ.get("BINARY_UTILITIES_DIR") or sp.get("binary_utilities_dir", ""),
        "SPECTROPY": sp.get("spectroPy_dir", ""),
        "VASP_BINARY": resolve_binary(cfg, cpu_flag),
        "PHONOPY_DIM": cfg["phonopy"]["dim"],
        "PHONOPY_AMP": cfg["phonopy"]["amplitude"],
        "SRUN_PER_DIR": mode_cfg.get("srun_per_dir", ""),
        "SRUN_RELAX": mode_cfg.get("srun_relax", ""),
        "SBATCH_RELAX": mode_cfg.get("sbatch_relax", "") or mode_cfg.get("sbatch", ""),
        "SBATCH_MAIN": mode_cfg.get("sbatch", ""),
        "SBATCH_POST": mode_cfg.get("sbatch_post", "") or mode_cfg.get("sbatch", ""),
        "MAX_RESTARTS": cfg.get("vasp_loop", {}).get("max_restarts", 3),
        "START_FROM_SUPERCELL": cfg.get("start_from_supercell", False),
        "HOME_OUTPUT_DIR": home_output_dir or "",
        # kpoints per step
        "SCF_MESH": _kp(cfg, "scf_relax", "mesh"),
        "SCF_SHIFT": _kp(cfg, "scf_relax", "shift", "0 0 0"),
        "SUP_MESH": _kp(cfg, "supercell", "mesh"),
        "SUP_SHIFT": _kp(cfg, "supercell", "shift", "0 0 0"),
        "HF_MESH": _kp(cfg, "force_consts", "mesh"),
        "HF_SHIFT": _kp(cfg, "force_consts", "shift", "0 0 0"),
        "RAMAN_MESH": _kp(cfg, "resonant_vasp", "mesh"),
        "RAMAN_SHIFT": _kp(cfg, "resonant_vasp", "shift", "0 0 0"),
        "DEFECT_MESH": _kp(cfg, "defect_relax_1", "mesh") or _kp(cfg, "scf_relax", "mesh"),
        "DEFECT_SHIFT": _kp(cfg, "defect_relax_1", "shift", "") or _kp(cfg, "scf_relax", "shift", "0 0 0"),
        # post_process
        "ENERGIES": " ".join(str(e) for e in post.get("desired_energies", [])),
        "INCIDENT_POL": rt.get("incident_polarization", "1.0 0.0 0.0"),
        "SCATTERED_POL": rt.get("scattered_polarization", "1.0 0.0 0.0"),
        "SURFACE_NORMAL": rt.get("surface_normal", "z"),
        "BRD_MODE": brd.get("mode", 2),
        "BRD_HWHM": brd.get("hwhm", 1),
        "BRD_INTERP": brd.get("interpolation", 200),
        "BRD_NORM": brd.get("normalization", 2),
        "SYM_ENABLED": symf.get("enabled", False),
        "SYM_IRREPS": " ".join(symf.get("allowed_irreps", ["A1'", "E'"])),
        # eigenvectors / viz
        "EIG_PATH": cfg.get("steps", {}).get("phonon_post", {}).get("eigenvectors_band", {}).get("path", "0.0 0.0 0.0  0.0 0.0 0.0"),
        "EIG_LABELS": cfg.get("steps", {}).get("phonon_post", {}).get("eigenvectors_band", {}).get("labels", "GAMMA GAMMA"),
        "EIG_POINTS": cfg.get("steps", {}).get("phonon_post", {}).get("eigenvectors_band", {}).get("points", 1),
        "VIZ_ENABLED": cfg.get("steps", {}).get("phonon_post", {}).get("visualization", {}).get("enabled", False),
        "VIZ_SCALE": cfg.get("steps", {}).get("phonon_post", {}).get("visualization", {}).get("scale_factor", 0.5),
        # seed files
        "SEED_CHGCAR": cfg.get("seed_files", {}).get("chgcar", ""),
        "SEED_WAVECAR": cfg.get("seed_files", {}).get("wavecar", ""),
    }
    return b


# =============================================================================
#  Emitters — each returns a finished bash script string
# =============================================================================
HEADER = "#!/bin/bash\nset -euo pipefail\nsource {common}\ncd \"$(dirname \"$0\")\"\n"


def _header():
    return HEADER.format(common=COMMON_SH)


def symmetry_conf(dim):
    return f"DIM = {dim}\nIRREPS = 0 0 0\n"


def eigenvectors_conf(b):
    lines = [f"DIM = {b['PHONOPY_DIM']}", f"BAND = {b['EIG_PATH']}"]
    if b["EIG_LABELS"]:
        lines.append(f"BAND_LABELS = {b['EIG_LABELS']}")
    lines.append(f"BAND_POINTS = {b['EIG_POINTS']}")
    lines.append("EIGENVECTORS = .TRUE.")
    return "\n".join(lines) + "\n"


def emit_relax(cfg, b, step, dst_dir, debug):
    """scf_relax / defect_relax_1 / defect_relax_2. dst_dir: 'scf' or 'scf2'."""
    incar = build_incar_content(cfg, step)
    if step == "defect_relax_2" or step == "defect_relax_2_cpu":
        mesh, shift = b["DEFECT_MESH"], b["DEFECT_SHIFT"]
    elif step == "defect_relax_1":
        mesh, shift = b["DEFECT_MESH"], b["DEFECT_SHIFT"]
    else:
        mesh, shift = b["SCF_MESH"], b["SCF_SHIFT"]
    kpts = kpoints_content("K-points", mesh, shift)
    dryrun = " --dry-run" if debug else ""

    is_defect1 = step == "defect_relax_1"
    done_file = "CONTCAR_ISIF2" if is_defect1 else "CONTCAR"
    seed_lines = ""
    if b["SEED_CHGCAR"]:
        seed_lines += f'[ -f CHGCAR ] || cp "{b["SEED_CHGCAR"]}" CHGCAR 2>/dev/null || true\n'
    if b["SEED_WAVECAR"]:
        seed_lines += f'[ -f WAVECAR ] || cp "{b["SEED_WAVECAR"]}" WAVECAR 2>/dev/null || true\n'

    post_success = ""
    if is_defect1:
        post_success = (
            "        cp CONTCAR CONTCAR_ISIF2; cp OUTCAR OUTCAR_ISIF2; cp OSZICAR OSZICAR_ISIF2\n"
        )

    s = _header()
    s += f"""
case "${{1:-}}" in
  --check)   python3 {CHECK_CONV} --relax . >/dev/null 2>&1 && [ -s {done_file} ] && exit 0 || exit 1 ;;
  --restart) rm -f OUTCAR CONTCAR OSZICAR vasprun.xml vaspout.h5 relaxation.stdout"""
    if is_defect1:
        s += " CONTCAR_ISIF2 OUTCAR_ISIF2 OSZICAR_ISIF2"
    s += """; exit 0 ;;
esac
"""
    s += f"""python3 {CHECK_CONV} --relax . >/dev/null 2>&1 && [ -s {done_file} ] && {{ echo "[{step}] already complete"; exit 0; }}

[ -f POSCAR ] || cp ../input/POSCAR POSCAR 2>/dev/null || true
cp ../input/POTCAR POTCAR 2>/dev/null || true
{seed_lines}cat > INCAR <<'INCAR_EOF'
{incar}INCAR_EOF
cat > KPOINTS <<'KPT_EOF'
{kpts}KPT_EOF
resume_contcar

for attempt in $(seq 1 {b['MAX_RESTARTS']}); do
    rm -f OUTCAR
    srun {b['SRUN_PER_DIR']} {b['VASP_BINARY']}{dryrun} > relaxation.stdout 2>&1 || true
    if python3 {CHECK_CONV} --relax . >/dev/null 2>&1; then
{post_success}        echo "[{step}] converged on attempt $attempt"
        exit 0
    fi
    resume_contcar
done
echo "[{step}] FATAL: not converged after {b['MAX_RESTARTS']} attempts" >&2
exit 1
"""
    return s


def emit_supercell(cfg, b, debug):
    incar = build_incar_content(cfg, "supercell")
    kpts = kpoints_content("K-points", b["SUP_MESH"], b["SUP_SHIFT"])
    dryrun = " --dry-run" if debug else ""
    sfs = b["START_FROM_SUPERCELL"]
    check = '[ -s SPOSCAR ]' if sfs else '[ -s groundstate/CONTCAR ]'
    s = _header()
    s += f"""
case "${{1:-}}" in
  --check)   {check} && exit 0 || exit 1 ;;
  --restart) rm -rf groundstate SPOSCAR POSCAR-* phonopy_disp.yaml; exit 0 ;;
esac
{check} && {{ echo "[supercell] already complete"; exit 0; }}

cp ../scf/CONTCAR POSCAR_unitcell
[ -s SPOSCAR ] || phonopy -d --dim="{b['PHONOPY_DIM']}" --amplitude={b['PHONOPY_AMP']} -c POSCAR_unitcell
mkdir -p groundstate
cp SPOSCAR groundstate/POSCAR
"""
    if not sfs:
        s += f"""cp ../input/POTCAR groundstate/POTCAR 2>/dev/null || true
cat > groundstate/INCAR <<'INCAR_EOF'
{incar}INCAR_EOF
cat > groundstate/KPOINTS <<'KPT_EOF'
{kpts}KPT_EOF
( cd groundstate && srun {b['SRUN_PER_DIR']} {b['VASP_BINARY']}{dryrun} > supercell_relax.stdout 2>&1 ) || true
python3 {CHECK_CONV} --relax groundstate >/dev/null 2>&1 || {{ echo "[supercell] FATAL: groundstate relax not converged" >&2; exit 1; }}
n=$(grep -c 'reached required accuracy' groundstate/OUTCAR 2>/dev/null || echo 0)
cp groundstate/CONTCAR CONTCAR_supercell_relaxed
cp groundstate/CONTCAR CONTCAR
"""
    else:
        s += "cp SPOSCAR CONTCAR\n"
    s += 'echo "[supercell] done"\n'
    return s


def emit_hf_setup(cfg, b, debug):
    incar = build_incar_content(cfg, "force_consts")
    kpts = kpoints_content("K-points", b["HF_MESH"], b["HF_SHIFT"])
    dryrun = " --dry-run" if debug else ""
    symlink_src = "../../scf" if b["START_FROM_SUPERCELL"] else "../groundstate"
    s = _header()
    s += f"""
case "${{1:-}}" in
  --check)   compgen -G "hf_POSCAR-*" >/dev/null && exit 0 || exit 1 ;;
  --restart) rm -rf hf_POSCAR-* SPOSCAR POSCAR-* phonopy_disp.yaml groundstate; exit 0 ;;
esac
compgen -G "hf_POSCAR-*" >/dev/null && {{ echo "[hf_setup] already complete"; exit 0; }}

relax_dir=../scf; [ -s ../scf2/CONTCAR ] && relax_dir=../scf2
cp "$relax_dir/CONTCAR" POSCAR_unitcell
cp ../input/POTCAR POTCAR 2>/dev/null || true
cat > INCAR <<'INCAR_EOF'
{incar}INCAR_EOF
cat > KPOINTS <<'KPT_EOF'
{kpts}KPT_EOF
cat > symmetry.conf <<'CONF_EOF'
{symmetry_conf(b['PHONOPY_DIM'])}CONF_EOF

[ -s SPOSCAR ] || phonopy -d --dim="{b['PHONOPY_DIM']}" --amplitude={b['PHONOPY_AMP']} -c POSCAR_unitcell
{b['BIN']}/runHF

for d in hf_POSCAR-*; do
    cat > "$d/run_vasp.sh" <<VASP_EOF
#!/bin/bash
set -euo pipefail
source {COMMON_SH}
cd "\\$(dirname "\\$0")"
python3 {CHECK_CONV} --static . >/dev/null 2>&1 && exit 0
srun {b['SRUN_PER_DIR']} {b['VASP_BINARY']}{dryrun} > relaxation.stdout 2>&1
VASP_EOF
    chmod +x "$d/run_vasp.sh"
done

mkdir -p groundstate
for f in CHGCAR WAVECAR; do
    src="$relax_dir/$f"; [ -s "$src" ] || continue
    [ "$relax_dir" != groundstate ] && ln -sf "$src" "groundstate/$f"
    for d in hf_POSCAR-*; do ln -sf "{symlink_src}/$f" "$d/$f"; done
done
echo "[hf_setup] $(ls -d hf_POSCAR-* | wc -l) dirs created"
"""
    return s


def emit_dir_loop(b, glob, log, dielectric):
    """force_consts / resonant_vasp — batch run_vasp.sh over displacement dirs."""
    diel = ""
    if dielectric:
        diel = f'    python3 {CHECK_DIEL} "$d" || {{ echo "[FATAL] $d no Im(ε)" >&2; exit 1; }}\n'
    s = _header()
    s += f"""
CHECK="python3 {CHECK_CONV} --static"
check_all() {{ local d; for d in {glob}; do $CHECK "$d" >/dev/null 2>&1 || return 1; done; }}

case "${{1:-}}" in
  --check)   check_all && exit 0 || exit 1 ;;
  --restart) for d in {glob}; do rm -f "$d"/{{OUTCAR,CONTCAR,OSZICAR,vasprun.xml,vaspout.h5,{log}}}; done; exit 0 ;;
esac
check_all && {{ echo "[dispatch] already complete"; exit 0; }}

dirs=( {glob} )
concurrent=${{SLURM_JOB_NUM_NODES:-1}}
echo "[dispatch] ${{#dirs[@]}} dirs, ${{concurrent}}/batch"
for (( s=0; s<${{#dirs[@]}}; s+=concurrent )); do
    for (( i=s; i<s+concurrent && i<${{#dirs[@]}}; i++ )); do
        bash "${{dirs[i]}}/run_vasp.sh" &
    done
    wait
done

for d in {glob}; do
    $CHECK "$d" >/dev/null 2>&1 || {{ echo "[FATAL] $d did not converge" >&2; exit 1; }}
{diel}done
echo "[dispatch] all ${{#dirs[@]}} dirs converged"
"""
    return s


def emit_phonon_post(cfg, b):
    s = _header()
    s += f"""
case "${{1:-}}" in
  --check)   [ -s band.yaml ] && [ -s FORCE_SETS ] && exit 0 || exit 1 ;;
  --restart) rm -f FORCE_SETS band.yaml irreps.yaml eigenvectors.yaml mesh.yaml; rm -rf VESTA_MODES; exit 0 ;;
esac
[ -s band.yaml ] && [ -s FORCE_SETS ] && {{ echo "[phonon_post] already complete"; exit 0; }}

mapfile -t vaspruns < <(ls hf_POSCAR-*/vasprun.xml 2>/dev/null | sort)
(( ${{#vaspruns[@]}} == 0 )) && {{ echo "[phonon_post] FATAL: no vasprun.xml" >&2; exit 1; }}
ndirs=$(ls -d hf_POSCAR-* | wc -l)
(( ${{#vaspruns[@]}} < ndirs )) && echo "[phonon_post] WARNING: ${{#vaspruns[@]}}/${{ndirs}} vasprun.xml present"

phonopy -f "${{vaspruns[@]}}"
cat > eigenvectors.conf <<'CONF_EOF'
{eigenvectors_conf(b)}CONF_EOF
cat > symmetry.conf <<'SYM_EOF'
{symmetry_conf(b['PHONOPY_DIM'])}SYM_EOF
phonopy -c POSCAR_unitcell eigenvectors.conf
phonopy -c POSCAR_unitcell symmetry.conf
"""
    if b["VIZ_ENABLED"] and b["SPECTROPY"]:
        s += f"""python3 {b['SPECTROPY']}/visualize_modes.py --poscar POSCAR_unitcell --band band.yaml --outdir VESTA_MODES --format vesta --scale {b['VIZ_SCALE']} || echo "[viz] WARNING: skipped"
"""
    s += 'echo "[phonon_post] done"\n'
    return s


def emit_raman_prep(cfg, b, debug):
    incar = build_incar_content(cfg, "resonant_vasp")
    kpts = kpoints_content("K-points", b["RAMAN_MESH"], b["RAMAN_SHIFT"])
    dryrun = " --dry-run" if debug else ""
    s = _header()
    s += f"""
case "${{1:-}}" in
  --check)   compgen -G "ra_pos_*" >/dev/null && exit 0 || exit 1 ;;
  --restart) rm -rf ra_pos_* AXML; exit 0 ;;
esac
compgen -G "ra_pos_*" >/dev/null && {{ echo "[raman_prep] already complete"; exit 0; }}

cp ../scf/CONTCAR CONTCAR
for f in CHGCAR WAVECAR; do [ -s "../scf/$f" ] && ln -sf "../scf/$f" "$f"; done
cp ../input/POTCAR POTCAR 2>/dev/null || true
cat > INCAR <<'INCAR_EOF'
{incar}INCAR_EOF
cat > KPOINTS <<'KPT_EOF'
{kpts}KPT_EOF

{b['BIN']}/ramdiscar
echo "go" | {b['BIN']}/genRApos610
{b['BIN']}/runRA

for d in ra_pos_*; do
    cat > "$d/run_vasp.sh" <<VASP_EOF
#!/bin/bash
set -euo pipefail
source {COMMON_SH}
cd "\\$(dirname "\\$0")"
python3 {CHECK_CONV} --static . >/dev/null 2>&1 && exit 0
srun {b['SRUN_PER_DIR']} {b['VASP_BINARY']}{dryrun} > stdout 2>&1
VASP_EOF
    chmod +x "$d/run_vasp.sh"
    for f in CHGCAR WAVECAR; do [ -s "../scf/$f" ] && ln -sf "../../scf/$f" "$d/$f"; done
done
echo "[raman_prep] $(ls -d ra_pos_* | wc -l) dirs created"
"""
    return s


def emit_post_process(cfg, b):
    energies = b["ENERGIES"] or "0.00"
    first_e = energies.split()[0]
    copyback = ""
    if b["HOME_OUTPUT_DIR"]:
        copyback = f'mkdir -p "{b["HOME_OUTPUT_DIR"]}"; cp -r ../output/. "{b["HOME_OUTPUT_DIR"]}/"\n'
    sym_filter = ""
    if b["SYM_ENABLED"]:
        sym_filter = (
            f'    awk \'BEGIN{{split("{b["SYM_IRREPS"]}",a," ");for(i in a)ok[a[i]]=1}} ok[$NF]\' '
            f'"Raman_intensity_complex_${{eV}}eV" > "${{eV}}eV/filtered.dat" 2>/dev/null || true\n'
        )
    plot = ""
    if b["SPECTROPY"]:
        plot = (
            f"printf '5.0\\nl\\n' | python3 {b['SPECTROPY']}/generate_raman_plots.py "
            f"&& cp Raman_plot_styled.png ../output/raman_spectra/ 2>/dev/null "
            f'|| echo "[post] WARNING: plotting skipped"\n'
        )
    s = _header()
    s += f"""BIN="{b['BIN']}"
DONE="../output/raman_data/Raman_intensity_complex_{first_e}eV"

case "${{1:-}}" in
  --check)   [ -s "$DONE" ] && exit 0 || exit 1 ;;
  --restart) rm -rf store_ramfile store_epsilon AXML ./*eV ../output; exit 0 ;;
esac
[ -s "$DONE" ] && {{ echo "[post_process] already complete"; exit 0; }}

# Kopia: cp vasprun.xml -> AXML/
mkdir -p AXML
for d in ra_pos_*; do
    dst="AXML/${{d#ra_pos_}}.xml"
    {{ [ -f "$dst" ] && [ ! -L "$dst" ]; }} || cp --remove-destination "$d/vasprun.xml" "$dst"
done
n=$(ls AXML/*.xml 2>/dev/null | wc -l); e=$(find AXML -name '*.xml' -empty | wc -l)
(( n == 0 )) && {{ echo "[post] FATAL: no XML in AXML" >&2; exit 1; }}
(( e > 0 )) && {{ echo "[post] FATAL: $e empty XML" >&2; exit 1; }}

# RAMFILE per energy
export PATH="$BIN:$PATH"; mkdir -p store_ramfile store_epsilon
for eV in {energies}; do
    [ -s "store_ramfile/RAMFILE_${{eV}}" ] && continue
    echo "$eV" | genRAram610_dynamic
    mv RAMFILE_* store_ramfile/ 2>/dev/null || true
    mv EPSILON_* store_epsilon/ 2>/dev/null || true
done

mkdir -p ../output/raman_data ../output/raman_spectra
for eV in {energies}; do
    [ -s "store_ramfile/RAMFILE_${{eV}}" ] || {{ echo "[post] RAMFILE_${{eV}} missing — skipping"; continue; }}
    cp "store_ramfile/RAMFILE_${{eV}}" RAMFILE
    "$BIN"/raman_tensor <<POL >/dev/null
{b['INCIDENT_POL']}
{b['SCATTERED_POL']}
{b['SURFACE_NORMAL']}
POL
    cat > broadening_input <<BRD
Raman_intensity_complex
{b['BRD_MODE']}
{b['BRD_HWHM']}
{b['BRD_INTERP']}
{b['BRD_NORM']}
BRD
    "$BIN"/broadening
    mv Raman_intensity_complex "Raman_intensity_complex_${{eV}}eV" 2>/dev/null || true
    mv Raman_intensity_complex_broadening "Raman_intensity_complex_broadening_${{eV}}eV" 2>/dev/null || true
    mkdir -p "${{eV}}eV"
{sym_filter}    cp "Raman_intensity_complex_${{eV}}eV" ../output/raman_data/ 2>/dev/null || true
    cp "Raman_intensity_complex_broadening_${{eV}}eV" ../output/raman_data/ 2>/dev/null || true
done

{plot}for f in band.yaml irreps.yaml; do [ -s "../hf/$f" ] && cp "../hf/$f" ../output/; done
{copyback}echo "[post_process] done"
"""
    return s


def emit_run_all(cfg, b, active_steps, work_dir):
    """The runner. sbatch_mix groups compute steps under sbatch --wait; interactive
    runs everything inline."""
    mode = b["COMPUTE_MODE"]
    name = os.path.basename(work_dir)
    lines = ['#!/bin/bash', 'set -euo pipefail', 'cd "$(dirname "$0")"',
             f'source {COMMON_SH}', '',
             f'# Runner for {name}  |  mode: {mode}  |  generated by new/generate.py',
             '# Edit the sbatch lines to regroup allocations.', '']

    # map step name -> script path
    def path(step):
        if step in ("scf_relax", "defect_relax_1"):
            return f"scf/run_{step.replace('scf_relax','relax')}.sh" if step == "scf_relax" else "scf/run_defect_relax_1.sh"
        if step == "defect_relax_2":
            return "scf2/run_defect_relax_2.sh"
        if step in ("supercell", "hf_setup", "force_consts", "phonon_post"):
            return f"hf/run_{step}.sh"
        return f"raman/run_{step}.sh"

    if mode == "sbatch_mix":
        # relax phase
        relax_steps = [s for s in active_steps if s in ("scf_relax", "supercell", "defect_relax_1", "defect_relax_2")]
        main_steps = [s for s in active_steps if s in ("force_consts", "resonant_vasp")]
        login_mid = [s for s in active_steps if s in ("hf_setup", "phonon_post", "raman_prep")]
        post = [s for s in active_steps if s == "post_process"]

        def phase(steps, sbatch_args, jobname):
            out = [f"sbatch --wait {sbatch_args} --requeue -J {jobname} <<PHASE",
                   "#!/bin/bash",
                   f'cd {work_dir} && source {COMMON_SH}']
            for st in steps:
                out.append(f"run_until_complete {path(st)}")
            out.append("PHASE")
            return "\n".join(out)

        if relax_steps:
            lines.append("# ── relax (own allocation; hf_setup needs its output) ──")
            lines.append(phase(relax_steps, b["SBATCH_RELAX"], f"relax_{name}"))
            lines.append("")
        # login steps interleave by dependency: hf_setup after relax, before force_consts
        for st in active_steps:
            if st in login_mid and st == "hf_setup":
                lines.append(f"run_until_complete {path(st)}   # login")
        if main_steps:
            lines.append("")
            lines.append("# ── main compute (one allocation for all GPU work) ──")
            lines.append(phase(main_steps, b["SBATCH_MAIN"], f"main_{name}"))
            lines.append("")
        for st in active_steps:
            if st in ("phonon_post", "raman_prep"):
                lines.append(f"run_until_complete {path(st)}   # login")
        if post:
            lines.append("")
            lines.append("# ── post-process (small allocation) ──")
            lines.append(phase(post, b["SBATCH_POST"], f"post_{name}"))
    else:  # interactive
        lines.append("# interactive: already inside an allocation, no sbatch")
        for st in active_steps:
            lines.append(f"run_until_complete {path(st)}")

    lines.append("")
    lines.append('echo "Pipeline complete."')
    return "\n".join(lines) + "\n"


# =============================================================================
#  Driver
# =============================================================================
def write(path, content, executable=True):
    with open(path, "w") as f:
        f.write(content)
    if executable:
        os.chmod(path, 0o755)
    print(f"  wrote {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("material_dir")
    ap.add_argument("--no-scratch", dest="scratch", action="store_false",
                    help="run in the material dir instead of $SCRATCH (scratch is the default)")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--debug", action="store_true", help="append VASP --dry-run to every VASP call")
    ap.add_argument("--shared", default=os.path.join(
        os.environ.get("RAMAN_PROJECT_DIR", os.path.dirname(os.path.dirname(REPO_NEW))),
        "shared_workflow_settings.yaml"))
    ap.set_defaults(scratch=True)
    args = ap.parse_args()

    material_dir = os.path.abspath(args.material_dir)
    per_material = os.path.join(material_dir, "input", "workflow_settings.yaml")

    # capture ordered step list from the per-material file before merge
    step_order_from_file = []
    if os.path.exists(per_material):
        with open(per_material) as f:
            raw = yaml.safe_load(f) or {}
        step_order_from_file = list((raw.get("steps") or {}).keys())

    cfg = load_config([args.shared, per_material])
    active_steps = step_order_from_file or STEP_ORDER
    validate_config(cfg, active_steps)

    name = os.path.basename(material_dir)
    # Base work dir: $SCRATCH by default, material dir with --no-scratch.
    if args.scratch:
        scratch = os.environ.get("SCRATCH", "")
        if not scratch:
            sys.exit("ERROR: default is scratch mode but $SCRATCH is unset; "
                     "pass --no-scratch to run in the material dir.")
        base_dir = os.path.join(scratch, "vasp_calculations", name)
    else:
        base_dir = material_dir
    # --debug nests a throwaway tree under the base so it never touches real data.
    work_dir = os.path.join(base_dir, "debug") if args.debug else base_dir

    # Whenever the work dir isn't the material dir itself, create an `input`
    # symlink so the scripts find POSCAR/POTCAR, and (non-debug) bake the
    # HOME_OUTPUT_DIR so post_process copies results back to $HOME.
    home_output = ""
    if work_dir != material_dir:
        os.makedirs(work_dir, exist_ok=True)
        link = os.path.join(work_dir, "input")
        if os.path.islink(link):
            os.unlink(link)
        if not os.path.exists(link):
            os.symlink(os.path.join(material_dir, "input"), link)
        if not args.debug:
            home_output = os.path.join(material_dir, "output")

    b = build_bake(cfg, args.cpu, home_output)

    # validate binaries exist
    if not b["VASP_BINARY"] or not os.path.isfile(b["VASP_BINARY"]):
        sys.exit(f"ERROR: VASP binary not found: {b['VASP_BINARY']!r}")
    if not os.path.isdir(b["BIN"]):
        sys.exit(f"ERROR: binary_utilities_dir not found: {b['BIN']!r}")

    print(f"\nGenerating pipeline for {name}")
    print(f"  work_dir: {work_dir}")
    print(f"  mode: {b['COMPUTE_MODE']}  steps: {active_steps}\n")

    for sub in ("scf", "scf2", "hf", "raman"):
        os.makedirs(os.path.join(work_dir, sub), exist_ok=True)

    # emit step scripts
    if "scf_relax" in active_steps:
        write(os.path.join(work_dir, "scf", "run_relax.sh"),
              emit_relax(cfg, b, "scf_relax", "scf", args.debug))
    if "defect_relax_1" in active_steps:
        write(os.path.join(work_dir, "scf", "run_defect_relax_1.sh"),
              emit_relax(cfg, b, "defect_relax_1", "scf", args.debug))
    if "defect_relax_2" in active_steps:
        write(os.path.join(work_dir, "scf2", "run_defect_relax_2.sh"),
              emit_relax(cfg, b, "defect_relax_2", "scf2", args.debug))
    if "supercell" in active_steps:
        write(os.path.join(work_dir, "hf", "run_supercell.sh"),
              emit_supercell(cfg, b, args.debug))
    if "hf_setup" in active_steps:
        write(os.path.join(work_dir, "hf", "run_hf_setup.sh"),
              emit_hf_setup(cfg, b, args.debug))
    if "force_consts" in active_steps:
        write(os.path.join(work_dir, "hf", "run_force_consts.sh"),
              emit_dir_loop(b, "hf_POSCAR-*", "relaxation.stdout", dielectric=False))
    if "phonon_post" in active_steps:
        write(os.path.join(work_dir, "hf", "run_phonon_post.sh"),
              emit_phonon_post(cfg, b))
    if "raman_prep" in active_steps:
        write(os.path.join(work_dir, "raman", "run_raman_prep.sh"),
              emit_raman_prep(cfg, b, args.debug))
    if "resonant_vasp" in active_steps:
        write(os.path.join(work_dir, "raman", "run_resonant_vasp.sh"),
              emit_dir_loop(b, "ra_pos_*", "stdout", dielectric=True))
    if "post_process" in active_steps:
        write(os.path.join(work_dir, "raman", "run_post_process.sh"),
              emit_post_process(cfg, b))

    write(os.path.join(work_dir, "run_all.sh"),
          emit_run_all(cfg, b, active_steps, work_dir))

    print(f"\nDone. Run:  bash {os.path.join(work_dir, 'run_all.sh')}")


if __name__ == "__main__":
    main()
