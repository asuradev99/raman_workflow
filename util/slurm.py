"""Low-level Slurm primitives: job submission, polling, salloc piping.

Nothing in this module knows about the pipeline or VASP — it only talks to the
Slurm scheduler (sbatch, salloc, squeue).  Higher-level orchestration lives in
provision.py (resource allocation) and util/compute.py (per-step VASP dispatch).
"""

import os
import shlex
import subprocess
import sys
import tempfile
import time


class SallocAllocationError(RuntimeError):
    """salloc was rejected by Slurm (allocation limit / QOS policy) — not preemption."""
    pass


class SbatchCancelledError(RuntimeError):
    """sbatch job was manually cancelled by the user (scancel) — do not retry."""
    pass


_ALLOC_REJECTION_KEYWORDS = (
    "unable to allocate resources",
    "job violates accounting/qos policy",
    "qosmaxsubmitjobperuserlimit",
    "qosgrpsubmitjobslimit",
    "qosmaxjobsperuserlimit",
    "batch job submission failed",
    "insufficient resources",
)


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
        result = subprocess.run(cmd, shell=True, stderr=subprocess.PIPE, text=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            lower = result.stderr.lower()
            if any(kw in lower for kw in _ALLOC_REJECTION_KEYWORDS):
                raise SallocAllocationError(result.stderr.strip())
            raise subprocess.CalledProcessError(result.returncode, cmd)
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
    """Poll a list of Slurm job IDs until all are done.

    Raises SbatchCancelledError if any job was manually cancelled (scancel).
    Returns True otherwise.
    """
    remaining = set(job_ids)
    finished_ids = set()
    while remaining:
        time.sleep(sleep_s)
        finished = _check_done(remaining)
        remaining -= finished
        finished_ids |= finished
        if finished:
            print(f"  [compute] {len(finished)} job(s) finished, "
                  f"{len(remaining)} remaining")
    _raise_if_cancelled(finished_ids)
    return True


def _raise_if_cancelled(job_ids):
    """Check sacct for any CANCELLED jobs; raise SbatchCancelledError if found."""
    if not job_ids:
        return
    try:
        result = subprocess.run(
            ["sacct", "-j", ",".join(job_ids), "-o", "JobID,State", "-n", "-X"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "CANCELLED" in parts[1].upper():
                raise SbatchCancelledError(
                    f"Job {parts[0]} was manually cancelled — stopping retry loop."
                )
    except SbatchCancelledError:
        raise
    except Exception:
        pass  # sacct unavailable or timed out — treat as non-cancellation


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
                          extra_exports=None, output_dir=None,
                          sbatch_args=""):
    """Write *wrapper_script* to a temp file, submit via sbatch, poll until done.

    sbatch_args — raw resource flags string passed verbatim to sbatch
                  (e.g. "--nodes=4 --time=04:00:00 --qos=preempt -A m526 -C gpu").
                  When empty, sbatch uses whatever #SBATCH headers are in the script.

    Returns True if the job completed successfully.
    """
    sbatch_args_list = shlex.split(sbatch_args) if sbatch_args else []
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


