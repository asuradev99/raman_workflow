"""Step 1 — Initial VASP relaxation (unit cell or defect supercell)."""

import os, time, shutil
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.status import print_step_header, print_step_result


def _relax_stage(ctx, stage, scf_dir, label):
    """Run a single VASP relaxation stage and return (ok, duration)."""
    t0 = time.time()
    write_incar(os.path.join(scf_dir, "INCAR"), ctx.config, stage)
    ok = ctx.run_relaxation(
        scf_dir, ctx.srun_args, ctx.vasp_binary, stage_label=label
    )
    return ok, time.time() - t0


def run(ctx):
    write_status = ctx.write_status
    step = ctx.current_step
    config = ctx.config

    scf_dir = os.path.join(ctx.work_dir, "scf")
    run_command(f"mkdir -p {scf_dir}", cwd=ctx.work_dir)

    input_dir = os.path.join(ctx.material_dir, "input")
    for vasp_input in ("POSCAR", "POTCAR"):
        src = os.path.join(input_dir, vasp_input)
        if not os.path.exists(src):
            raise FileNotFoundError(f"input/{vasp_input} not found at {src}.")
        run_command(f"cp input/{vasp_input} scf/{vasp_input}", cwd=ctx.work_dir)

    # Optionally seed CHGCAR/WAVECAR from a previous calculation (YAML-configured)
    seed = config.get("seed_files", {})
    for seed_file in ("CHGCAR", "WAVECAR"):
        src = seed.get(seed_file.lower(), "")
        if not src:
            continue
        if not os.path.exists(src):
            print(f"  [seed] WARNING: {seed_file} path does not exist: {src}")
            continue
        if os.path.getsize(src) == 0:
            print(f"  [seed] WARNING: {seed_file} is 0 bytes, skipping: {src}")
            continue
        dst = os.path.join(scf_dir, seed_file)
        if os.path.islink(dst) or os.path.exists(dst):
            os.unlink(dst)
        os.symlink(src, dst)
        print(f"  [seed] Symlinked {seed_file} → scf/ ({os.path.getsize(src)} bytes)")

    # ── Detect two-stage defect relaxation ───────────────────────────────────
    templates = config.get("incar_templates", {})
    has_defect_pair = (
        ctx.start_from_supercell
        and "defect_relax_fixed" in templates
        and "defect_relax_full" in templates
    )

    if has_defect_pair:
        # ── Resume-aware: check if ISIF2 already saved from previous run ────
        contcar_isif2 = os.path.join(scf_dir, "CONTCAR_ISIF2")
        skip_isif2 = os.path.exists(contcar_isif2)

        if skip_isif2:
            print(f"  [defect] Found existing CONTCAR_ISIF2 — skipping ISIF2, resuming at ISIF3")

        # ── Stage 1: ISIF=2 (lattice fixed, atoms only) ──────────────────────
        if not skip_isif2:
            print_step_header(step, description=f"Step {step}a — Defect ISIF=2 (atoms only)")
            write_status(step, "running", "Defect ISIF=2 relaxation")
            t_total_start = time.time()

            write_kpoints(
                os.path.join(scf_dir, "KPOINTS"),
                "K-points for defect supercell relaxation",
                ctx.scf_kpoints_mesh,
                ctx.scf_kpoints_shift,
            )
            print(f"  [setup] Wrote KPOINTS to scf/")

            ok1, dt1 = _relax_stage(ctx, "defect_relax_fixed", scf_dir, "defect-ISIF2")
            if not ok1:
                msg = (
                    f"Defect ISIF=2 relaxation failed in {scf_dir}. "
                    f"Check {scf_dir}/relaxation.stdout."
                )
                print_step_result(step, ok=False, duration_s=dt1, message="ISIF2 failed")
                raise RuntimeError(msg)

            # Save the ISIF2-relaxed CONTCAR for reference
            shutil.copy2(os.path.join(scf_dir, "CONTCAR"), contcar_isif2)
            print(f"  [defect] Saved ISIF2 CONTCAR → CONTCAR_ISIF2 ({os.path.getsize(contcar_isif2)} bytes)")

            # Save ISIF2 output files before ISIF3 overwrites them
            for fname in ("OUTCAR", "OSZICAR"):
                src = os.path.join(scf_dir, fname)
                dst = os.path.join(scf_dir, f"{fname}_ISIF2")
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    print(f"  [defect] Saved ISIF2 {fname} → {fname}_ISIF2 ({os.path.getsize(dst)} bytes)")

            print(f"  [defect] ISIF=2 converged — starting ISIF=3")
        else:
            t_total_start = time.time()
            # Still need KPOINTS for ISIF3 stage
            write_kpoints(
                os.path.join(scf_dir, "KPOINTS"),
                "K-points for defect supercell relaxation",
                ctx.scf_kpoints_mesh,
                ctx.scf_kpoints_shift,
            )
            print(f"  [defect] Resuming at ISIF3 — ISIF2 already converged in prior run")

        # ── Stage 2: ISIF=3 (full relaxation from ISIF2 positions) ────────────
        step2 = step + 0.5  # sub-step key for log table
        print_step_header(step, description=f"Step {step}b — Defect ISIF=3 (atoms + lattice)")
        write_status(step2, "running", "Defect ISIF=3 relaxation")

        # ISIF2 CONTCAR → POSCAR for the ISIF3 run
        poscar_src = contcar_isif2 if os.path.exists(contcar_isif2) else os.path.join(scf_dir, "CONTCAR")
        shutil.copy2(poscar_src, os.path.join(scf_dir, "POSCAR"))
        print(f"  [defect] CONTCAR → POSCAR for ISIF3 stage")

        ok2, dt2 = _relax_stage(ctx, "defect_relax_full", scf_dir, "defect-ISIF3")
        if not ok2:
            msg = (
                f"Defect ISIF=3 relaxation failed in {scf_dir}. "
                f"Check {scf_dir}/relaxation.stdout."
            )
            write_status(step2, "failed", "Defect ISIF=3 failed")
            print_step_result(step, ok=False, duration_s=dt2, message="ISIF3 failed")
            raise RuntimeError(msg)

        write_status(step2, "completed", "Defect ISIF=3 converged")
        write_status(step, "completed", "Defect relaxation complete (ISIF2+ISIF3)")
        total_dt = time.time() - t_total_start
        print_step_result(
            step, ok=True, duration_s=total_dt,
            message="ISIF2+ISIF3 converged"
        )

    else:
        # ── Standard unit-cell relaxation ────────────────────────────────────
        print_step_header(step)
        write_status(step, "running", "Initial VASP relaxation")
        t_start = time.time()

        write_kpoints(
            os.path.join(scf_dir, "KPOINTS"),
            "K-points for unit cell SCF",
            ctx.scf_kpoints_mesh,
            ctx.scf_kpoints_shift,
        )
        print(f"  [setup] Wrote unit cell KPOINTS ({ctx.scf_kpoints_mesh}) to scf/")

        ok, dt = _relax_stage(ctx, "relax", scf_dir, "step-1")
        if not ok:
            msg = (
                f"VASP relaxation failed after max retries in {scf_dir}. "
                f"Check {scf_dir}/relaxation.stdout."
            )
            print_step_result(step, ok=False, duration_s=dt, message="Relaxation failed")
            raise RuntimeError(msg)

        write_status(step, "completed", "Initial VASP relaxation finished")
        print_step_result(step, ok=True, duration_s=dt)
