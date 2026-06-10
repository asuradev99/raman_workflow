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
    write_status = ctx["write_status"]
    step = ctx["_step"]
    hf_dir = ctx["hffiles_dir"]
    work_dir = ctx["work_dir"]
    print_step_header(step)
    write_status(step, "running", "Supercell generation + ionic relaxation")
    t_start = time.time()
    gs_dir = os.path.join(hf_dir, "groundstate")
    run_command(f"mkdir -p {gs_dir}", cwd=work_dir)
    print("  [setup] Creating supercell in hf/ via phonopy...")
    run_command(f"cp scf/CONTCAR {hf_dir}/POSCAR_unitcell", cwd=work_dir)
    check_no_selective_dynamics(os.path.join(hf_dir, "POSCAR_unitcell"), "POSCAR_unitcell")
    run_command(
        f'phonopy -d --dim="{ctx["phonopy_dim"]}" --amplitude={ctx["phonopy_amplitude"]} -c POSCAR_unitcell',
        cwd=hf_dir,
    )
    spos_path = os.path.join(hf_dir, "SPOSCAR")
    if not os.path.exists(spos_path):
        raise FileNotFoundError(f"SPOSCAR not found in {hf_dir}")
    run_command(f"cp SPOSCAR {gs_dir}/POSCAR", cwd=hf_dir)
    print("  [setup] POSCAR-* + SPOSCAR in hf/")
    if not ctx["start_from_supercell"]:
        print("  [vasp] Supercell ionic relaxation...")
        write_incar(os.path.join(gs_dir, "INCAR"), ctx["config"], "supercell_relax")
        write_kpoints(
            os.path.join(gs_dir, "KPOINTS"),
            "K-points for supercell",
            ctx["sup_relax_kpoints_mesh"],
            ctx["sup_relax_kpoints_shift"],
        )
        run_command(f"cp input/POTCAR {gs_dir}/", cwd=work_dir)
        run_command(
            f"srun {ctx['srun_args']} {ctx['vasp_binary']} > supercell_relax.stdout",
            cwd=gs_dir,
            check_success=False,
        )
        check_vasp_convergence(gs_dir, "step-2")
        n = count_ionic_steps(gs_dir)
        if n and n >= 100:
            print("  WARNING: Supercell relaxation reached NSW=100 limit")
    run_command(f"cp SPOSCAR {hf_dir}/CONTCAR_supercell_relaxed", cwd=hf_dir)
    run_command(f"cp SPOSCAR {hf_dir}/CONTCAR", cwd=hf_dir)
    if not ctx["start_from_supercell"]:
        run_command(f"cp CONTCAR {gs_dir}/CONTCAR_relaxed", cwd=gs_dir, check_success=False)
    if ctx["start_from_supercell"]:
        write_status(step, "completed", "Supercell displacements (relaxation skipped)")
    else:
        write_status(step, "completed", "Supercell relaxed — CHGCAR/WAVECAR in hf/groundstate/")
    print_step_result(step, ok=True, duration_s=time.time() - t_start)
