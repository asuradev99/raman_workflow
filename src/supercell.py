"""Step 2 — Supercell generation + ionic relaxation."""

import os, time
from util.io import run_command, require_file
from util.incar import write_vasp_inputs
from util.vasp import (
    check_vasp_convergence,
    check_no_selective_dynamics,
    count_ionic_steps,
    is_calculation_complete,
)
from util.status import begin_step, print_step_result


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_label
    hf_dir = ctx.hffiles_dir
    work_dir = ctx.work_dir

    t_start = begin_step(ctx, "Supercell generation + ionic relaxation")
    gs_dir = os.path.join(hf_dir, "groundstate")
    run_command(f"mkdir -p {gs_dir}", cwd=work_dir)
    print("  [setup] Creating supercell in hf/ via phonopy...")
    run_command(f"cp scf/CONTCAR {hf_dir}/POSCAR_unitcell", cwd=work_dir)
    check_no_selective_dynamics(os.path.join(hf_dir, "POSCAR_unitcell"), "POSCAR_unitcell")
    run_command(
        f'phonopy -d --dim="{ctx.phonopy_dim}" --amplitude={ctx.phonopy_amplitude} -c POSCAR_unitcell',
        cwd=hf_dir,
    )
    require_file(os.path.join(hf_dir, "SPOSCAR"), "SPOSCAR")
    run_command(f"cp SPOSCAR {gs_dir}/POSCAR", cwd=hf_dir)
    print("  [setup] POSCAR-* + SPOSCAR in hf/")
    if not ctx.start_from_supercell:
        print("  [vasp] Supercell ionic relaxation...")
        write_vasp_inputs(gs_dir, work_dir, ctx.config, "supercell",
                          ctx.sup_relax_kpoints_mesh, ctx.sup_relax_kpoints_shift,
                          "K-points for supercell")
        run_command(
            f"srun {ctx.srun_args} {ctx.vasp_binary} > supercell_relax.stdout",
            cwd=gs_dir,
            check_success=False,
        )
        check_vasp_convergence(gs_dir, "step-2")
        n = count_ionic_steps(gs_dir)
        if n and n >= 100:
            print("  WARNING: Supercell relaxation reached NSW=100 limit")
    run_command(f"cp SPOSCAR {hf_dir}/CONTCAR_supercell_relaxed", cwd=hf_dir)
    run_command(f"cp SPOSCAR {hf_dir}/CONTCAR", cwd=hf_dir)
    if not ctx.start_from_supercell:
        run_command(f"cp CONTCAR {gs_dir}/CONTCAR_relaxed", cwd=gs_dir, check_success=False)
    msg = ("Supercell displacements (relaxation skipped)" if ctx.start_from_supercell
           else "Supercell relaxed — CHGCAR/WAVECAR in hf/groundstate/")
    write_status(step, "completed", msg)
    print_step_result(step, ok=True, duration_s=time.time() - t_start)


def is_complete(work_dir, config):
    p = os.path.join(work_dir, "hf", "groundstate", "CONTCAR")
    return os.path.exists(p) and os.path.getsize(p) > 0
