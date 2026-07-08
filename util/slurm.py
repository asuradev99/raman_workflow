"""Low-level Slurm primitives: job submission, polling, salloc piping.

Nothing in this module knows about the pipeline or VASP — it only talks to the
Slurm scheduler (sbatch, salloc, squeue).  Higher-level orchestration lives in
provision.py (resource allocation) and util/compute.py (per-step VASP dispatch).
"""

import os
import re
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


def _log_launch(kind, resource_args, script_or_cmd, log_path=None):
    """Print the full script/command about to be launched.

    If *log_path* is given, also append it directly to that file --
    needed only when the caller runs before automation_raman_analysis.py's
    Tee is installed (i.e. provision.py itself), since Tee would otherwise
    be the only thing capturing this print(). Callers running *inside* the
    already-Tee'd pipeline process must leave log_path=None or the banner
    gets written twice.
    """
    sep = "─" * 78
    header = f"LAUNCHING {kind}" + (f"  ({resource_args})" if resource_args else "")
    banner = f"\n{sep}\n  {header}\n{sep}\n{script_or_cmd}\n{sep}\n"
    print(banner)
    if log_path:
        try:
            with open(log_path, "a") as f:
                f.write(banner)
        except Exception as e:
            print(f"  [launch-log] Warning: could not append to {log_path}: {e}")


def build_bash_setup(system_paths: dict) -> str:
    """Return a bash snippet that activates the conda env and loads modules.

    Stderr is left unsuppressed on purpose -- a typo'd module name or a
    missing conda env should be visible right where it happens, not
    silently swallowed until something downstream fails for a confusing,
    unrelated-looking reason. `|| true` on the bashrc source just keeps a
    non-interactive-shell quirk from aborting the script; it doesn't hide
    the error text itself.
    """
    sp = system_paths
    lines = ["source ~/.bashrc || true"]
    if sp.get("conda_init"):
        lines.append(f"source {sp['conda_init']}")
    if sp.get("conda_env"):
        lines.append(f"conda activate {sp['conda_env']}")
    if sp.get("vasp_modules"):
        lines.append(f"module load {sp['vasp_modules']}")
    return "\n".join(lines)


_SRUN_TOKEN_RE = re.compile(r'(^|&&)\s*srun\b')


def build_omp_prefix(omp_cfg):
    """Build 'export OMP_NUM_THREADS=N && export OMP_PLACES=P && export
    OMP_PROC_BIND=B' from omp_cfg = {"num_threads":.., "places":..,
    "proc_bind":..} (config["omp"], shared_workflow_settings.yaml). Empty
    string if omp_cfg is empty or has no usable values.
    """
    if not omp_cfg:
        return ""
    mapping = [
        ("OMP_NUM_THREADS", omp_cfg.get("num_threads")),
        ("OMP_PLACES", omp_cfg.get("places")),
        ("OMP_PROC_BIND", omp_cfg.get("proc_bind")),
    ]
    parts = [f"export {k}={v}" for k, v in mapping if v not in (None, "")]
    return " && ".join(parts)


def build_srun_cmd(srun_args, vasp_binary, redirect):
    """Build the full VASP launch command from a config srun_args string.

    srun_args is normally just flags (e.g. "--nodes=4 --ntasks-per-node=32")
    and this prepends "srun ". But for CPU runs, PipelineContext bakes an
    OMP export chain + "srun" directly into ctx.srun_args/vasp_srun_per_dir
    once, at the source (see build_omp_prefix) -- e.g.:
        export OMP_NUM_THREADS=4 && export OMP_PLACES=threads &&
        export OMP_PROC_BIND=spread && srun --nodes=4 --ntasks-per-node=32 ...
    Detected here by the presence of "srun" as its own command (via
    _SRUN_TOKEN_RE) -- in that case srun_args is used verbatim, with no
    second "srun " prepended.

    The export-ahead-of-srun approach exists because Slurm's
    `--export=ALL,VAR=value` is documented to override an ALL-inherited
    value of VAR, but on Perlmutter's Cray MPICH stack that override isn't
    reliably honored -- confirmed empirically: with OMP_NUM_THREADS=4 in
    --export=, VASP still reported "1 threads/rank", because the vasp
    module sets OMP_NUM_THREADS=1 via Lmod setenv before the srun call runs.
    """
    if _SRUN_TOKEN_RE.search(srun_args):
        return f"{srun_args} {vasp_binary} {redirect}"
    return f"srun {srun_args} {vasp_binary} {redirect}"


def build_standalone_script(cwd, cmd, system_paths=None):
    """Return a self-contained bash script that reproduces *cmd* run in *cwd*.

    Includes the same module-load/conda-activate preamble as the pipeline's
    own launch (via build_bash_setup) so it's copy-pasteable into a plain
    shell and actually works standalone -- unlike the bare srun line alone,
    which silently assumes the surrounding shell already has everything
    loaded (true only because the pipeline's own wrapper script set that up
    first). Written out as a file by write_standalone_script() alongside
    each VASP launch, in the same directory as the calculation itself.
    """
    lines = ["#!/bin/bash -l"]
    if system_paths:
        lines.append(build_bash_setup(system_paths))
    lines.append(f"cd {cwd}")
    lines.append(cmd)
    return "\n".join(lines)


def write_standalone_script(cwd, cmd, system_paths=None, filename="rerun_vasp.sh"):
    """Write an executable standalone reproduction script into *cwd*.

    Reference copy, not something the pipeline itself runs: cd into *cwd*
    later and run it manually to reproduce this exact VASP launch outside
    the pipeline. No salloc/allocation request is included -- same as the
    pipeline itself, it assumes it's being run from inside an existing
    allocation. Overwritten on every call, so it always reflects the most
    recent launch for that directory.
    """
    script = build_standalone_script(cwd, cmd, system_paths)
    path = os.path.join(cwd, filename)
    try:
        with open(path, "w") as f:
            f.write(script + "\n")
        os.chmod(path, 0o755)
        print(f"  [rerun] Wrote standalone script: {path}")
    except Exception as e:
        print(f"  [rerun] Warning: could not write {path}: {e}")
    return path


def run_via_salloc_pipe(wrapper_script, job_name="raman_pipe",
                        salloc_args="", work_dir="", log_path=None):
    """Write *wrapper_script* to work_dir, pipe into salloc, block until done.

    log_path: pass only when calling from outside the pipeline's own Tee'd
    process (i.e. provision.py) -- see _log_launch for why.
    """
    if not salloc_args:
        raise ValueError("salloc_args is required")
    if not work_dir:
        raise ValueError("work_dir is required (must be on shared filesystem)")

    _log_launch("salloc", salloc_args, wrapper_script, log_path)

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
                srun_args="", sbatch_args="", log_path=None):
    """Submit one sbatch job per directory, poll until all finish.

    srun_args  — passed as $SRUN_ARGS env var inside the job (from srun_per_dir config).
    sbatch_args — sbatch resource flags (nodes, gpus, time, qos, constraint)
                  sourced from sbatch_per_dir config; overrides any #SBATCH headers.
    log_path — see _log_launch; normally left None here since this always runs
    inside the already-Tee'd pipeline process (print() alone reaches workflow.log).
    """
    n_total = len(directories)
    if n_total == 0:
        return True

    with open(script_path) as f:
        _template = f.read()
    _log_launch(f"sbatch (template, x{n_total} dirs)", sbatch_args, _template, log_path)

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
                          sbatch_args="", log_path=None):
    """Write *wrapper_script* to a temp file, submit via sbatch, poll until done.

    sbatch_args — raw resource flags string passed verbatim to sbatch
                  (e.g. "--nodes=4 --time=04:00:00 --qos=preempt -A m526 -C gpu").
                  When empty, sbatch uses whatever #SBATCH headers are in the script.
    log_path — pass only when calling from outside the pipeline's own Tee'd
    process (i.e. provision.py) -- see _log_launch.

    Returns True if the job completed successfully.
    """
    _log_launch("sbatch", sbatch_args, wrapper_script, log_path)

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


