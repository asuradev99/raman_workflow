"""Step 4 — Supercell generation + ionic relaxation."""

import os, time
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.vasp import (
    check_vasp_convergence,
    check_no_selective_dynamics,
    count_ionic_steps,
)
from util.status import print_step_header, print_step_result


def run(ctx):
    ws = ctx["write_status"]
    H = ctx["hffiles_dir"]
    W = ctx["work_dir"]
    print_step_header(2)
    ws(2, "running", "Supercell generation + ionic relaxation")
    _t0 = time.time()
    gs = os.path.join(H, "groundstate")
    run_command(f"mkdir -p {gs}", cwd=W)
    print("  [setup] Creating supercell in hf/ via phonopy...")
    run_command(f"cp scf/CONTCAR {H}/POSCAR_unitcell", cwd=W)
    check_no_selective_dynamics(os.path.join(H, "POSCAR_unitcell"), "POSCAR_unitcell")
    run_command(
        f'phonopy -d --dim="{ctx["phonopy_dim"]}" --amplitude={ctx["phonopy_amplitude"]} -c POSCAR_unitcell',
        cwd=H,
    )
    sp = os.path.join(H, "SPOSCAR")
    if not os.path.exists(sp):
        raise FileNotFoundError(f"SPOSCAR not found in {H}")
    run_command(f"cp SPOSCAR {gs}/POSCAR", cwd=H)
    print("  [setup] POSCAR-* + SPOSCAR in hf/")
    if not ctx["start_from_supercell"]:
        print("  [vasp] Supercell ionic relaxation...")
        write_incar(os.path.join(gs, "INCAR"), ctx["config"], "supercell_relax")
        write_kpoints(
            os.path.join(gs, "KPOINTS"),
            "K-points for supercell",
            ctx["sup_relax_kpoints_mesh"],
            ctx["sup_relax_kpoints_shift"],
        )
        run_command(f"cp input/POTCAR {gs}/", cwd=W)
        run_command(
            f"srun {ctx['srun_args']} {ctx['vasp_binary']} > supercell_relax.stdout",
            cwd=gs,
            check_success=False,
        )
        check_vasp_convergence(gs, "step-2")
        n = count_ionic_steps(gs)
        if n and n >= 100:
            print("  WARNING: Supercell relaxation reached NSW=100 limit")
    run_command(f"cp SPOSCAR {H}/CONTCAR_supercell_relaxed", cwd=H)
    run_command(f"cp SPOSCAR {H}/CONTCAR", cwd=H)
    if not ctx["start_from_supercell"]:
        run_command(f"cp CONTCAR {gs}/CONTCAR_relaxed", cwd=gs, check_success=False)
    if ctx["start_from_supercell"]:
        ws(2, "completed", "Supercell displacements (relaxation skipped)")
    else:
        ws(2, "completed", "Supercell relaxed — CHGCAR/WAVECAR in hf/groundstate/")
    print_step_result(2, ok=True, duration_s=time.time() - _t0)
