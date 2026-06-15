"""Compute provisioning — salloc pipe + sbatch launcher + polling."""

import os
import shlex
import subprocess
import tempfile
import time


# ── Shared bash-setup helpers ─────────────────────────────────────────────────

def build_bash_setup(system_paths: dict) -> str:
    """Return a bash snippet that activates the conda env and loads modules."""
    sp = system_paths
    lines = ["source ~/.bashrc 2>/dev/null || true"]
    if sp.get("conda_init"):
        lines.append(f"source {sp['conda_init']} 2>/dev/null")
    if sp.get("conda_env"):
        lines.append(f"conda activate {sp['conda_env']} 2>/dev/null")
    if sp.get("vasp_modules"):
        lines.append(f"module load {sp['vasp_modules']} 2>/dev/null")
    return "\n".join(lines)


def build_serial_vasp_wrapper(system_paths: dict) -> str:
    """Return a bash wrapper *template* for serial VASP runs inside an salloc.

    Placeholders: ``{DIR_LIST}`` (space-separated paths), ``{WORK_DIR}``,
    ``{SRUN_ARGS}``, ``{VASP_BINARY}``.  Completed directories are skipped.
    """
    lines = [
        "#!/bin/bash -l",
        build_bash_setup(system_paths),
        """cd {WORK_DIR}
for d in {DIR_LIST}; do
    if [ -f "$d/OUTCAR" ] && grep -q "General timing" "$d/OUTCAR" 2>/dev/null; then
        echo "[serial] $d (cached)"
        continue
    fi
    echo "[serial] Running VASP in $d"
    cd "$d" && srun {SRUN_ARGS} {VASP_BINARY} > relaxation.stdout && cd {WORK_DIR}
done""",
    ]
    return "\n".join(lines)


def run_pipeline_in_salloc(ctx, full_pipeline=True):
    """Wrap a pipeline re-invocation inside an auto-allocated salloc.

    If *full_pipeline*, passes ``--inside-salloc`` (all steps run in salloc).
    Otherwise passes ``--salloc-steps`` (only steps 1-3 in salloc, login node
    continues with steps 4+).
    """
    sp = ctx.system_paths
    material_dir = ctx.material_dir
    work_dir = ctx.work_dir
    salloc_args = ctx.salloc_relax
    if not salloc_args:
        raise KeyError("salloc_relax not set in compute_modes config")

    salloc_flag = "--inside-salloc" if full_pipeline else "--salloc-steps"
    flags = [salloc_flag]
    if ctx.scratch_flag:
        flags.append("--scratch")
    if ctx.cpu_flag:
        flags.append("--cpu")

    raman_workflow_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python_bin = os.path.join(sp.get("conda_env", ""), "bin", "python3")
    if not os.path.isfile(python_bin):
        python_bin = "python3"
    wrapper = "\n".join([
        "#!/bin/bash -l",
        build_bash_setup(sp),
        f"export PYTHONPATH={raman_workflow_dir}:$PYTHONPATH",
        f"cd {material_dir}",
        f"export RAMAN_PROJECT_DIR={os.environ.get('RAMAN_PROJECT_DIR', '')}",
        f"{python_bin} -m src.automation_raman_analysis {' '.join(flags)}",
    ])
    run_via_salloc_pipe(
        wrapper,
        salloc_args=salloc_args,
        job_name=f"raman_{ctx.material_name}",
        work_dir=work_dir,
    )


def run_via_salloc_pipe(wrapper_script, job_name="raman_pipe",
                        salloc_args="", work_dir=""):
    """Write *wrapper_script* to work_dir, pipe into salloc, block until done."""
    if not salloc_args:
        raise ValueError("salloc_args is required")
    if not work_dir:
        raise ValueError("work_dir is required (must be on shared filesystem)")

    import uuid
    script_path = os.path.join(work_dir, f".salloc_wrapper_{uuid.uuid4().hex[:8]}.sh")
    with open(script_path, "w") as f:
        f.write(wrapper_script)

    cmd = (f"echo 'bash {script_path}' | "
           f"salloc {salloc_args} "
           f"-J {job_name}")
    print(f"  [compute] Waiting for allocation ({salloc_args})…")
    try:
        subprocess.run(cmd, shell=True, check=True)
    finally:
        if os.path.exists(script_path):
            os.unlink(script_path)
    print(f"  [compute] Allocation released.")


def _sbatch_exports(system_paths, extra=None):
    """Build an --export= string from system_paths config for sbatch."""
    exports = ["ALL"]
    if extra:
        exports.extend(f"{k}={v}" for k, v in extra.items())
    if system_paths:
        sp = system_paths
        if sp.get("vasp_modules"):
            exports.append(f"VASP_MODULES={sp['vasp_modules']}")
        if sp.get("conda_init"):
            exports.append(f"CONDA_INIT={sp['conda_init']}")
        if sp.get("conda_env"):
            exports.append(f"CONDA_ENV={sp['conda_env']}")
    return ",".join(exports)


def _submit_one_job(script_path, job_name, exports_str, sbatch_args_list, output_dir=None):
    """Run sbatch for one job. Returns job ID string, or None on failure."""
    cmd = ["sbatch", f"--job-name={job_name}", f"--export={exports_str}"]
    if output_dir:
        cmd += [f"--output={output_dir}/slurm_%j.out",
                f"--error={output_dir}/slurm_%j.err"]
    cmd += sbatch_args_list
    cmd.append(script_path)
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"  [compute] ERROR submitting {job_name}: {result.stderr.strip()}")
        return None
    return result.stdout.strip().split()[-1]


def submit_many(script_path, directories, job_name_prefix="vasp",
                qos="preempt", system_paths=None,
                srun_args="", sbatch_args=""):
    """Submit one sbatch job per directory, poll until all finish.

    srun_args  — passed as $SRUN_ARGS env var inside the job (from srun_per_dir config).
    sbatch_args — sbatch resource flags (nodes, gpus, time, qos, constraint)
                  sourced from sbatch_per_dir config; overrides any #SBATCH headers.
    """
    n_total = len(directories)
    if n_total == 0:
        return True

    sbatch_args_list = shlex.split(sbatch_args) if sbatch_args else []
    base_extra = {"SRUN_ARGS": srun_args}  # always export — batch scripts use set -u

    job_ids = []
    for i, d in enumerate(directories):
        job_name = f"{job_name_prefix}_{i:03d}"
        exports_str = _sbatch_exports(system_paths or {}, extra={**base_extra, "DIR": d})
        jid = _submit_one_job(script_path, job_name, exports_str, sbatch_args_list, output_dir=d)
        if jid:
            job_ids.append(jid)
            print(f"  [compute] Submitted {job_name} ({i+1}/{n_total}): job {jid} → {d}")

    if not job_ids:
        print("  [compute] No jobs submitted successfully.")
        return False

    print(f"  [compute] Waiting for {len(job_ids)} job(s) to complete…")
    return _poll_jobs(job_ids)


def _poll_jobs(job_ids, sleep_s=15):
    """Poll a list of Slurm job IDs until all are done. Returns True if all OK."""
    remaining = set(job_ids)
    while remaining:
        time.sleep(sleep_s)
        finished = _check_done(remaining)
        remaining -= finished
        if finished:
            print(f"  [compute] {len(finished)} job(s) finished, "
                  f"{len(remaining)} remaining")
    return True


def _check_done(job_ids):
    """Return the subset of *job_ids* that have completed (no longer in squeue)."""
    try:
        result = subprocess.run(["squeue", "-h", "-o", "%A", "-j",
                                 ",".join(job_ids)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        still_running = set(result.stdout.strip().split())
    except Exception:
        return set()
    return set(job_ids) - still_running


def submit_sbatch_wrapper(wrapper_script, job_name="vasp_pipe",
                          nodes=1, walltime="48:00:00",
                          qos="preempt", account="m526",
                          ntasks_per_node=4, cpus_per_task=32,
                          extra_exports=None, output_dir=None,
                          sbatch_args=""):
    """Write *wrapper_script* to a temp file, submit via sbatch, poll until done.

    sbatch_args — resource flags as a single string (e.g. from a sbatch_relax config
                  key).  When provided, overrides the individual nodes/walltime/qos/
                  account params.  When omitted, those params are used to build the
                  resource args.

    Returns True if the job completed successfully.
    """
    if sbatch_args:
        sbatch_args_list = shlex.split(sbatch_args)
    else:
        sbatch_args_list = [
            f"--nodes={nodes}",
            f"--ntasks-per-node={ntasks_per_node}",
            f"--cpus-per-task={cpus_per_task}",
            f"--time={walltime}",
            f"--qos={qos}",
            f"--account={account}",
            "--constraint=gpu",
            "--gpus-per-node=4",
        ]

    exports_str = _sbatch_exports({}, extra=extra_exports or {})

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(wrapper_script)
        script_path = f.name

    try:
        jid = _submit_one_job(script_path, job_name, exports_str, sbatch_args_list, output_dir)
        if jid is None:
            return False
        print(f"  [compute] Submitted {job_name} (job {jid}), waiting…")
        return _poll_jobs([jid])
    finally:
        if os.path.exists(script_path):
            os.unlink(script_path)


def run_serial_in_salloc_with_retry(directories, wrapper_template,
                                    vasp_binary, work_dir,
                                    srun_args="--gpus=1 --ntasks=1",
                                    salloc_args="-N 1 -C gpu --gpus-per-node=4 -t 04:00:00 --qos=interactive -A m526",
                                    max_retries=10):
    """Run VASP serially in all *directories* inside auto-allocated salloc.

    If the salloc times out before all dirs are done, auto-requests a fresh
    salloc for the remaining dirs.  Repeats until all are complete.
    """
    remaining = list(directories)
    retry = 0

    while remaining and retry < max_retries:
        dir_list = " ".join(remaining)
        wrapper = wrapper_template.format(DIR_LIST=dir_list,
                                          VASP_BINARY=vasp_binary,
                                          SRUN_ARGS=srun_args,
                                          WORK_DIR=work_dir)
        print(f"  [serial] salloc ({salloc_args}) — "
              f"{len(remaining)} dir(s) remaining (retry {retry+1}/{max_retries})")

        run_via_salloc_pipe(
            wrapper,
            salloc_args=salloc_args,
            job_name=f"vasp_serial_{retry}",
            work_dir=work_dir,
        )

        # Update remaining
        remaining = [d for d in remaining
                     if not _is_calc_done(d)]
        retry += 1

        if remaining and retry < max_retries:
            n_done = len(directories) - len(remaining)
            print(f"\n  ═══════════════════════════════════════════════════════")
            print(f"  [compute] SALLOC RELEASED — {n_done}/{len(directories)} dirs done.")
            print(f"  [compute] {len(remaining)} dir(s) remaining — RE-REQUESTING salloc...")
            print(f"  ═══════════════════════════════════════════════════════\n")

    if remaining:
        print(f"  [serial] {len(remaining)} dir(s) still incomplete "
              f"after {max_retries} retries.")
        return False
    print(f"  [serial] All {len(directories)} dir(s) complete.")
    return True


def _is_calc_done(dirpath):
    """Check if a VASP calculation completed (OUTCAR + General timing)."""
    outcar = os.path.join(dirpath, "OUTCAR")
    if not os.path.exists(outcar):
        return False
    try:
        with open(outcar) as f:
            return "General timing and accounting" in f.read()
    except Exception:
        return False
