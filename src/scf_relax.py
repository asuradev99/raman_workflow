"""Step 1 — Initial VASP relaxation (unit cell or defect supercell)."""

import os, time, shutil
from util.io import run_command, require_file
from util.incar import write_incar, write_kpoints
from util.vasp import is_calculation_complete
from util.status import (
    begin_step, print_step_header, print_step_result,
    RELAX_LABEL_SINGLE, RELAX_LABEL_DEFECT_1, RELAX_LABEL_DEFECT_2,
    RELAX_LABEL_DEFECT_2_CPU,
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


def _relax_stage(ctx, stage, scf_dir, label,
                 srun_args=None, vasp_binary=None, setup_cmd=""):
    """Run a single VASP relaxation stage and return (ok, duration)."""
    t0 = time.time()
    write_incar(os.path.join(scf_dir, "INCAR"), ctx.config, stage)
    ok = ctx.run_relaxation(
        scf_dir,
        srun_args if srun_args is not None else ctx.srun_args,
        vasp_binary if vasp_binary is not None else ctx.vasp_binary,
        stage_label=label,
        setup_cmd=setup_cmd,
    )
    return ok, time.time() - t0


def _setup_scf(ctx):
    """Idempotent scf/ directory setup — mkdir, POSCAR/POTCAR, seed files, KPOINTS.

    POSCAR is only copied from input/ if scf/POSCAR does not already exist,
    so crash-resume state (CONTCAR copied over POSCAR) is preserved across calls.
    Returns the absolute path to scf/.
    """
    scf_dir = os.path.join(ctx.work_dir, "scf")
    os.makedirs(scf_dir, exist_ok=True)
    input_dir = os.path.join(ctx.material_dir, "input")

    poscar_dst = os.path.join(scf_dir, "POSCAR")
    if not os.path.exists(poscar_dst):
        src = os.path.join(input_dir, "POSCAR")
        require_file(src, "input/POSCAR")
        shutil.copy2(src, poscar_dst)

    potcar_src = os.path.join(input_dir, "POTCAR")
    require_file(potcar_src, "input/POTCAR")
    shutil.copy2(potcar_src, os.path.join(scf_dir, "POTCAR"))

    seed = ctx.config.get("seed_files", {})
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

    write_kpoints(
        os.path.join(scf_dir, "KPOINTS"),
        "K-points for SCF",
        ctx.scf_kpoints_mesh,
        ctx.scf_kpoints_shift,
    )
    return scf_dir


def _setup_scf2(ctx):
    """Set up scf2/ for stage-2 defect relaxation — isolated from scf/.

    POSCAR comes from scf/CONTCAR_ISIF2 (stage-1 output), with crash-resume
    fallback to scf2/CONTCAR if a previous stage-2 run was interrupted.
    CHGCAR/WAVECAR are seeded from scf/ to speed convergence.
    """
    scf_dir  = os.path.join(ctx.work_dir, "scf")
    scf2_dir = os.path.join(ctx.work_dir, "scf2")
    os.makedirs(scf2_dir, exist_ok=True)

    contcar_isif2 = os.path.join(scf_dir, "CONTCAR_ISIF2")
    if not os.path.exists(contcar_isif2):
        raise RuntimeError(
            "scf/CONTCAR_ISIF2 not found — run defect_relax_1 before defect_relax_2"
        )

    poscar   = os.path.join(scf2_dir, "POSCAR")
    contcar2 = os.path.join(scf2_dir, "CONTCAR")
    if (os.path.exists(contcar2) and os.path.getsize(contcar2) > 0
            and os.path.getmtime(contcar2) > os.path.getmtime(contcar_isif2)):
        shutil.copy2(contcar2, poscar)
        print("  [resume] scf2/CONTCAR → POSCAR — resuming prior crash")
    elif not (os.path.exists(poscar) and os.path.getsize(poscar) > 0):
        shutil.copy2(contcar_isif2, poscar)
        print("  [defect] CONTCAR_ISIF2 → scf2/POSCAR for relax 2")

    potcar_src = os.path.join(ctx.material_dir, "input", "POTCAR")
    require_file(potcar_src, "input/POTCAR")
    shutil.copy2(potcar_src, os.path.join(scf2_dir, "POTCAR"))

    for fname in ("CHGCAR", "WAVECAR"):
        src = os.path.join(scf_dir, fname)
        dst = os.path.join(scf2_dir, fname)
        if (os.path.exists(src) and os.path.getsize(src) > 0
                and not (os.path.exists(dst) and os.path.getsize(dst) > 0)):
            shutil.copy2(src, dst)
            print(f"  [seed] {fname}: scf/ → scf2/")

    write_kpoints(
        os.path.join(scf2_dir, "KPOINTS"),
        "K-points for defect supercell relax 2",
        ctx.scf_kpoints_mesh,
        ctx.scf_kpoints_shift,
    )
    return scf2_dir


def run_defect_1(ctx):
    """Granular step: defect stage 1 only — lattice-fixed atomic relaxation (GPU)."""
    write_status = ctx.write_status
    label = ctx.current_label
    scf_dir = _setup_scf(ctx)

    contcar_relax1 = os.path.join(scf_dir, "CONTCAR_ISIF2")
    if os.path.exists(contcar_relax1):
        print("  [defect] Found existing CONTCAR_ISIF2 — relax 1 already done")
        write_status(label, "completed", "Defect relax 1 already converged (prior run)")
        print_step_result(label, ok=True, duration_s=0)
        return

    t_start = begin_step(ctx, "Defect relax 1 (lattice fixed)")
    _resume_contcar(scf_dir, "relax1")
    ok, dt = _relax_stage(ctx, "defect_relax_fixed", scf_dir, "defect-relax1")
    if not ok:
        _relax_fail(write_status, label, dt, "Defect relax 1 failed", scf_dir)

    shutil.copy2(os.path.join(scf_dir, "CONTCAR"), contcar_relax1)
    print("  [defect] Saved relax 1 CONTCAR → CONTCAR_ISIF2")
    for fname in ("OUTCAR", "OSZICAR"):
        src_f = os.path.join(scf_dir, fname)
        if os.path.exists(src_f):
            shutil.copy2(src_f, os.path.join(scf_dir, f"{fname}_ISIF2"))

    _relax_ok(write_status, label, time.time() - t_start, "Relax 1 converged")


def _run_defect_2_impl(ctx, srun_args=None, vasp_binary=None, setup_cmd=""):
    """Shared implementation for defect stage 2 (GPU and CPU variants)."""
    write_status = ctx.write_status
    label = ctx.current_label
    scf2_dir = _setup_scf2(ctx)
    t_start = begin_step(ctx, "Defect relax 2 (full)")
    ok, dt = _relax_stage(ctx, "defect_relax_full", scf2_dir, "defect-relax2",
                          srun_args=srun_args, vasp_binary=vasp_binary,
                          setup_cmd=setup_cmd)
    if not ok:
        _relax_fail(write_status, label, dt, "Defect relax 2 failed", scf2_dir)
    _relax_ok(write_status, label, time.time() - t_start, "Relax 2 converged")


def run_defect_2(ctx):
    """Granular step: defect stage 2 only — full relaxation (GPU)."""
    _run_defect_2_impl(ctx)


def run_defect_2_cpu(ctx):
    """Granular step: defect stage 2 only — full relaxation (CPU)."""
    _run_defect_2_impl(
        ctx,
        srun_args=ctx.cpu_relax_srun_args,
        vasp_binary=ctx.cpu_relax_vasp_binary,
        setup_cmd=ctx.cpu_relax_setup_cmd,
    )


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

        # ── Stage 2: full relaxation in scf2/ ────────────────────────────────
        print_step_header(RELAX_LABEL_DEFECT_2)
        write_status(RELAX_LABEL_DEFECT_2, "running", "Defect relax 2 (full)")
        t2_start = time.time()
        scf2_dir = _setup_scf2(ctx)
        ok2, dt2 = _relax_stage(ctx, "defect_relax_full", scf2_dir, "defect-relax2")
        if not ok2:
            _relax_fail(write_status, RELAX_LABEL_DEFECT_2, dt2,
                        "Defect relax 2 failed", scf2_dir)
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


# ── File-based completion checks ────────────────────────────────────────────

def is_complete_defect_1(work_dir, config):
    p = os.path.join(work_dir, "scf", "CONTCAR_ISIF2")
    return os.path.exists(p) and os.path.getsize(p) > 0


def is_complete_defect_2(work_dir, config):
    scf2 = os.path.join(work_dir, "scf2")
    c = os.path.join(scf2, "CONTCAR")
    return is_calculation_complete(scf2) and os.path.exists(c) and os.path.getsize(c) > 0


def is_complete(work_dir, config):
    scf = os.path.join(work_dir, "scf")
    templates = config.get("incar_templates", {})
    has_defect_pair = (
        config.get("start_from_supercell", False)
        and "defect_relax_fixed" in templates
        and "defect_relax_full" in templates
    )
    if has_defect_pair:
        return is_complete_defect_1(work_dir, config) and is_complete_defect_2(work_dir, config)
    c = os.path.join(scf, "CONTCAR")
    return is_calculation_complete(scf) and os.path.exists(c) and os.path.getsize(c) > 0
