"""Step 1 — Initial VASP relaxation (unit cell or defect supercell)."""

import os, time, shutil
from util.io import run_command
from util.incar import write_incar
from util.kpoints import write_kpoints
from util.status import print_step_header, print_step_result


def _resume_contcar(scf_dir, context=""):
    """Copy CONTCAR → POSCAR if CONTCAR exists from a prior crash, return True if done."""
    contcar = os.path.join(scf_dir, "CONTCAR")
    if os.path.exists(contcar) and os.path.getsize(contcar) > 0:
        shutil.copy2(contcar, os.path.join(scf_dir, "POSCAR"))
        suffix = f" ({context})" if context else ""
        print(f"  [resume] CONTCAR → POSCAR{suffix} — resuming prior crash")
        return True
    return False


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
        # ── Resume-aware: check if relax 1 CONTCAR already saved from prior run ──
        contcar_relax1 = os.path.join(scf_dir, "CONTCAR_ISIF2")
        skip_relax1 = os.path.exists(contcar_relax1)

        if skip_relax1:
            print(f"  [defect] Found existing relax 1 CONTCAR — skipping relax 1, resuming at relax 2")

        # ── Stage 1: lattice-fixed atomic relaxation ──────────────────────────
        if not skip_relax1:
            print_step_header(step, description=f"Step {step}a — Defect relax 1 (lattice fixed)")
            write_status(step, "running", "Defect relax 1 (lattice fixed)")
            t_total_start = time.time()

            write_kpoints(
                os.path.join(scf_dir, "KPOINTS"),
                "K-points for defect supercell relaxation",
                ctx.scf_kpoints_mesh,
                ctx.scf_kpoints_shift,
            )
            print(f"  [setup] Wrote KPOINTS to scf/")
            _resume_contcar(scf_dir, "relax1")

            ok1, dt1 = _relax_stage(ctx, "defect_relax_fixed", scf_dir, "defect-relax1")
            if not ok1:
                msg = (
                    f"Defect relax 1 failed in {scf_dir}. "
                    f"Check {scf_dir}/relaxation.stdout."
                )
                print_step_result(step, ok=False, duration_s=dt1, message="Relax 1 failed")
                raise RuntimeError(msg)

            # Save relax 1 CONTCAR before relax 2 overwrites it
            shutil.copy2(os.path.join(scf_dir, "CONTCAR"), contcar_relax1)
            print(f"  [defect] Saved relax 1 CONTCAR → CONTCAR_ISIF2 ({os.path.getsize(contcar_relax1)} bytes)")

            # Save relax 1 output files before relax 2 overwrites them
            for fname in ("OUTCAR", "OSZICAR"):
                src = os.path.join(scf_dir, fname)
                dst = os.path.join(scf_dir, f"{fname}_ISIF2")
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    print(f"  [defect] Saved relax 1 {fname} → {fname}_ISIF2 ({os.path.getsize(dst)} bytes)")

            print(f"  [defect] Relax 1 converged — starting relax 2")
        else:
            t_total_start = time.time()
            # Still need KPOINTS for relax 2
            write_kpoints(
                os.path.join(scf_dir, "KPOINTS"),
                "K-points for defect supercell relaxation",
                ctx.scf_kpoints_mesh,
                ctx.scf_kpoints_shift,
            )
            print(f"  [defect] Resuming at relax 2 — relax 1 already converged in prior run")

        # ── Stage 2: full relaxation from stage 1 positions ──────────────────
        step2 = step + 0.5  # sub-step key for log table
        print_step_header(step, description=f"Step {step}b — Defect relax 2 (full)")
        write_status(step2, "running", "Defect relax 2 (full)")

        # Set POSCAR for relax 2: prefer CONTCAR if it has relax 2 progress (crash resume)
        contcar_path = os.path.join(scf_dir, "CONTCAR")
        contcar_has_relax2 = (
            os.path.exists(contcar_path)
            and os.path.getsize(contcar_path) > 0
            and os.path.getmtime(contcar_path) > os.path.getmtime(contcar_relax1)
        )
        if contcar_has_relax2:
            shutil.copy2(contcar_path, os.path.join(scf_dir, "POSCAR"))
            print(f"  [resume] CONTCAR → POSCAR (relax 2) — resuming prior crash")
        else:
            shutil.copy2(contcar_relax1, os.path.join(scf_dir, "POSCAR"))
            print(f"  [defect] CONTCAR_ISIF2 → POSCAR for relax 2")

        ok2, dt2 = _relax_stage(ctx, "defect_relax_full", scf_dir, "defect-relax2")
        if not ok2:
            msg = (
                f"Defect relax 2 failed in {scf_dir}. "
                f"Check {scf_dir}/relaxation.stdout."
            )
            write_status(step2, "failed", "Defect relax 2 failed")
            print_step_result(step, ok=False, duration_s=dt2, message="Relax 2 failed")
            raise RuntimeError(msg)

        write_status(step2, "completed", "Defect relax 2 converged")
        write_status(step, "completed", "Defect relaxation complete (relax1+relax2)")
        total_dt = time.time() - t_total_start
        print_step_result(
            step, ok=True, duration_s=total_dt,
            message="Relax 1+2 converged"
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
        _resume_contcar(scf_dir)

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
