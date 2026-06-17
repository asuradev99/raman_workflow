"""Step 1 — Initial VASP relaxation (unit cell or defect supercell)."""

import os, time, shutil
from util.io import run_command, require_file
from util.incar import write_incar, write_kpoints
from util.status import (
    begin_step, print_step_header, print_step_result,
    RELAX_LABEL_SINGLE, RELAX_LABEL_DEFECT_1, RELAX_LABEL_DEFECT_2,
)


def _resume_contcar(scf_dir, context=""):
    """Copy CONTCAR → POSCAR if CONTCAR exists from a prior crash."""
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


def _relax_ok(write_status, label, duration_s, message):
    """Mark a relax sub-step completed and print result."""
    write_status(label, "completed", message)
    print_step_result(label, ok=True, duration_s=duration_s, message=message)


def _relax_fail(write_status, label, duration_s, short_msg, scf_dir):
    """Mark a relax sub-step failed, print result, and raise RuntimeError."""
    write_status(label, "failed", short_msg)
    print_step_result(label, ok=False, duration_s=duration_s, message=short_msg)
    raise RuntimeError(f"{short_msg} in {scf_dir}. Check {scf_dir}/relaxation.stdout.")


def run(ctx):
    write_status = ctx.write_status
    config = ctx.config

    scf_dir = os.path.join(ctx.work_dir, "scf")
    run_command(f"mkdir -p {scf_dir}", cwd=ctx.work_dir)

    input_dir = os.path.join(ctx.material_dir, "input")
    for vasp_input in ("POSCAR", "POTCAR"):
        src = os.path.join(input_dir, vasp_input)
        require_file(src, f"input/{vasp_input}")
        run_command(f"cp input/{vasp_input} scf/{vasp_input}", cwd=ctx.work_dir)

    # Optionally seed CHGCAR/WAVECAR from a previous calculation (YAML-configured).
    # COPY rather than symlink: VASP overwrites scf/WAVECAR and scf/CHGCAR at run
    # end — if those were symlinks, the write goes through to the shared seed file,
    # corrupting it for every other material pointed at the same seed path.
    # Bootstrap from seed only ONCE: if scf/ already has its own real WAVECAR/CHGCAR
    # (written by relax 1, or left from a crash), don't overwrite.
    seed = config.get("seed_files", {})
    for seed_file in ("CHGCAR", "WAVECAR"):
        src = seed.get(seed_file.lower(), "")
        if not src:
            continue
        dst = os.path.join(scf_dir, seed_file)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            continue
        if not os.path.exists(src):
            print(f"  [seed] WARNING: {seed_file} path does not exist: {src}")
            continue
        if os.path.getsize(src) == 0:
            print(f"  [seed] WARNING: {seed_file} is 0 bytes, skipping: {src}")
            continue
        if os.path.islink(dst) or os.path.exists(dst):
            os.unlink(dst)
        shutil.copy2(src, dst)
        print(f"  [seed] Copied {seed_file} → scf/ ({os.path.getsize(src)} bytes)")

    # ── Detect two-stage defect relaxation ───────────────────────────────────
    templates = config.get("incar_templates", {})
    has_defect_pair = (
        ctx.start_from_supercell
        and "defect_relax_fixed" in templates
        and "defect_relax_full" in templates
    )

    if has_defect_pair:
        contcar_relax1 = os.path.join(scf_dir, "CONTCAR_ISIF2")
        skip_relax1 = os.path.exists(contcar_relax1)

        # KPOINTS are the same for both stages — write once
        write_kpoints(
            os.path.join(scf_dir, "KPOINTS"),
            "K-points for defect supercell relaxation",
            ctx.scf_kpoints_mesh,
            ctx.scf_kpoints_shift,
        )

        # ── Stage 1: lattice-fixed atomic relaxation ──────────────────────────
        if skip_relax1:
            print(f"  [defect] Found existing relax 1 CONTCAR — skipping to relax 2")
            write_status(RELAX_LABEL_DEFECT_1, "completed",
                         "Defect relax 1 already converged (prior run)")
        else:
            print_step_header(RELAX_LABEL_DEFECT_1)
            write_status(RELAX_LABEL_DEFECT_1, "running", "Defect relax 1 (lattice fixed)")
            t1_start = time.time()
            _resume_contcar(scf_dir, "relax1")

            ok1, dt1 = _relax_stage(ctx, "defect_relax_fixed", scf_dir, "defect-relax1")
            if not ok1:
                _relax_fail(write_status, RELAX_LABEL_DEFECT_1, dt1,
                            "Defect relax 1 failed", scf_dir)

            # Save relax 1 CONTCAR and outputs before relax 2 overwrites them
            shutil.copy2(os.path.join(scf_dir, "CONTCAR"), contcar_relax1)
            print(f"  [defect] Saved relax 1 CONTCAR → CONTCAR_ISIF2")
            for fname in ("OUTCAR", "OSZICAR"):
                src = os.path.join(scf_dir, fname)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(scf_dir, f"{fname}_ISIF2"))

            _relax_ok(write_status, RELAX_LABEL_DEFECT_1,
                      time.time() - t1_start, "Relax 1 converged")
            print(f"  [defect] Relax 1 converged — starting relax 2")

        # ── Stage 2: full relaxation from stage 1 positions ──────────────────
        print_step_header(RELAX_LABEL_DEFECT_2)
        write_status(RELAX_LABEL_DEFECT_2, "running", "Defect relax 2 (full)")
        t2_start = time.time()

        # POSCAR for relax 2: prefer CONTCAR if it has relax-2 progress (crash resume)
        contcar_path = os.path.join(scf_dir, "CONTCAR")
        if (os.path.exists(contcar_path) and os.path.getsize(contcar_path) > 0
                and os.path.getmtime(contcar_path) > os.path.getmtime(contcar_relax1)):
            shutil.copy2(contcar_path, os.path.join(scf_dir, "POSCAR"))
            print(f"  [resume] CONTCAR → POSCAR (relax 2) — resuming prior crash")
        else:
            shutil.copy2(contcar_relax1, os.path.join(scf_dir, "POSCAR"))
            print(f"  [defect] CONTCAR_ISIF2 → POSCAR for relax 2")

        ok2, dt2 = _relax_stage(ctx, "defect_relax_full", scf_dir, "defect-relax2")
        if not ok2:
            _relax_fail(write_status, RELAX_LABEL_DEFECT_2, dt2,
                        "Defect relax 2 failed", scf_dir)
        _relax_ok(write_status, RELAX_LABEL_DEFECT_2,
                  time.time() - t2_start, "Relax 2 converged")

    else:
        # ── Standard unit-cell relaxation ────────────────────────────────────
        print_step_header(RELAX_LABEL_SINGLE)
        write_status(RELAX_LABEL_SINGLE, "running", "Initial VASP relaxation")
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
            _relax_fail(write_status, RELAX_LABEL_SINGLE, dt,
                        "Relaxation failed", scf_dir)
        _relax_ok(write_status, RELAX_LABEL_SINGLE,
                  time.time() - t_start, "Initial VASP relaxation finished")
