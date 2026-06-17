"""VASP step dispatch — serial, parallel-batch, and sbatch_parallel/sbatch modes.

Knows about VASP directories and compute_mode-based dispatch strategies, but
does not talk to Slurm directly — all raw Slurm calls go through util/slurm.py.
Resource allocation (salloc/sbatch wrapper) lives in the top-level provision.py.
"""

import os
import re
import subprocess

from .slurm import build_bash_setup, run_via_salloc_pipe, submit_many, submit_sbatch_wrapper
from .vasp import is_calculation_complete
from .status import parse_resume_step, STEP_HISTORY


# ── Serial VASP wrapper (bash template) ──────────────────────────────────────

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


# ── Serial in salloc with retry ───────────────────────────────────────────────

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
                     if not is_calculation_complete(d)]
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


# ── Parallel batches within an existing allocation ────────────────────────────

def run_dirs_in_parallel_batches(directories, vasp_binary, srun_args,
                                 gpus_per_dir=4, total_gpus=None,
                                 log_name="relaxation.stdout"):
    """Run VASP in *directories* concurrently within an existing allocation.

    Used by ``sbatch_mix`` mode (one big sbatch allocation, no per-directory
    sub-jobs). Launches up to ``total_gpus // gpus_per_dir`` directories at
    once as background ``srun`` job steps — each claiming its own disjoint
    ``gpus_per_dir``-GPU (1 node, by default) slice of the allocation, no
    ``--overlap`` needed since Slurm won't double-book nodes between
    concurrent steps in the same job. Waits for each batch to finish before
    starting the next, so if there aren't enough GPUs to run everything at
    once, this naturally degrades to serial batches of N-GPUs-worth at a
    time until all directories are done.

    Already-complete directories (per ``util.vasp.is_calculation_complete``)
    are skipped up front. Returns True if every directory ends up complete.
    """
    # ── Guard against srun_args/gpus_per_dir drift ─────────────────────────
    # The concurrency math below assumes every directory's srun call claims
    # exactly `gpus_per_dir` GPUs on exactly 1 node. If a config edit changes
    # one without the other, the batch sizing would be wrong — e.g. launching
    # more concurrent 1-node steps than the allocation has nodes, which just
    # queues silently inside the job and burns the whole walltime. Fail fast
    # instead, before any srun is launched.
    gpn_match = re.search(r'--gpus-per-node[=\s]+(\d+)', srun_args)
    if gpn_match and int(gpn_match.group(1)) != gpus_per_dir:
        raise ValueError(
            f"run_dirs_in_parallel_batches: srun_args requests "
            f"--gpus-per-node={gpn_match.group(1)} but gpus_per_dir="
            f"{gpus_per_dir} — these must match or the batching math below "
            f"is wrong. Fix the compute_modes.sbatch_mix config."
        )
    nodes_match = re.search(r'--nodes[=\s]+(\d+)', srun_args)
    if nodes_match and int(nodes_match.group(1)) != 1:
        raise ValueError(
            f"run_dirs_in_parallel_batches: srun_args requests "
            f"--nodes={nodes_match.group(1)}, expected --nodes=1 (exactly "
            f"one node per directory) — concurrency math assumes this. "
            f"Fix the compute_modes.sbatch_mix config."
        )

    todo = [d for d in directories if not is_calculation_complete(d)]
    if not todo:
        return True

    if total_gpus is None:
        nnodes = int(os.environ.get("SLURM_NNODES")
                     or os.environ.get("SLURM_JOB_NUM_NODES") or 1)
        total_gpus = nnodes * 4

    concurrency = max(1, total_gpus // gpus_per_dir)
    n_batches = (len(todo) + concurrency - 1) // concurrency
    print(f"  [sbatch_mix] {len(todo)} dir(s) to run, {concurrency} concurrent "
          f"({gpus_per_dir} GPUs/dir, {total_gpus} GPUs total) — "
          f"{n_batches} batch(es)")

    for batch_num, start in enumerate(range(0, len(todo), concurrency), start=1):
        batch = todo[start:start + concurrency]
        print(f"  [sbatch_mix] Batch {batch_num}/{n_batches}: "
              f"{len(batch)} dir(s) concurrently…")
        procs = []
        for d in batch:
            dirname = os.path.basename(d)
            cmd = f"srun {srun_args} {vasp_binary} > {log_name} 2>&1"
            procs.append((dirname, subprocess.Popen(cmd, shell=True, cwd=d)))
        for dirname, proc in procs:
            rc = proc.wait()
            if rc != 0:
                print(f"  [sbatch_mix] WARNING: srun in {dirname} exited {rc}")

    remaining = [d for d in todo if not is_calculation_complete(d)]
    if remaining:
        print(f"  [sbatch_mix] {len(remaining)} dir(s) still incomplete after batching.")
        return False
    print(f"  [sbatch_mix] All {len(todo)} dir(s) complete.")
    return True


# ── 5-way compute_mode dispatch ───────────────────────────────────────────────

def dispatch_vasp_runs(ctx, all_dirs, todo, *, job_prefix, dir_script_name,
                       all_script_name, env_dir_key, env_dir_value,
                       all_job_name, manual_runner, mix_log_name="relaxation.stdout"):
    """Shared 5-way compute_mode dispatch for force_constants.py / resonant_vasp.py.

    sbatch_parallel/sbatch, sbatch_serial (outside salloc), interactive_serial
    (outside salloc), and sbatch_mix behave identically for both callers —
    only script/job/env names and the directory list differ, passed in via
    the keyword args above.

    interactive_manual, and sbatch_serial/interactive_serial when *already*
    inside an salloc (no need to re-allocate), all run the same way as each
    other — but that "same way" differs per caller (force_constants runs one
    combined retry-loop script over all hf dirs; resonant_vasp runs one srun
    per directory), so it's supplied by the caller as ``manual_runner(todo)
    -> bool``.

    Returns True/False (ok).
    """
    compute_mode = ctx.compute_mode
    scripts_root = os.path.join(os.path.dirname(ctx.script_dir), "scripts")

    if compute_mode in ("sbatch_parallel", "sbatch"):
        script_path = os.path.join(scripts_root, dir_script_name)
        max_retries = getattr(ctx, "vasp_max_restarts", 3)
        ok = False
        for attempt in range(1, max_retries + 1):
            incomplete = [d for d in all_dirs if not is_calculation_complete(d)]
            if not incomplete:
                ok = True
                break
            print(f"  [sbatch_parallel] Attempt {attempt}/{max_retries}: "
                  f"submitting {len(incomplete)}/{len(all_dirs)} dirs…")
            submit_many(script_path, incomplete, job_name_prefix=job_prefix,
                        system_paths=ctx.system_paths,
                        srun_args=ctx.vasp_srun_per_dir,
                        sbatch_args=ctx.vasp_sbatch_per_dir)
            # submit_many only confirms jobs left squeue — re-check actual VASP completion above
        return ok

    if compute_mode == "sbatch_serial" and not ctx.inside_salloc:
        print(f"  [sbatch_serial] Submitting 1 job for {len(todo)} dirs…")
        script_path = os.path.join(scripts_root, all_script_name)
        with open(script_path) as f:
            script_content = f.read()
        exports = {env_dir_key: env_dir_value, "VASP_BINARY": ctx.vasp_binary}
        return submit_sbatch_wrapper(script_content, job_name=all_job_name,
                                     extra_exports=exports, output_dir=env_dir_value)

    if compute_mode == "interactive_serial" and not ctx.inside_salloc:
        print(f"  [interactive_serial] {len(todo)} dirs, auto-salloc + retry…")
        return run_serial_in_salloc_with_retry(
            todo, build_serial_vasp_wrapper(ctx.system_paths),
            vasp_binary=ctx.vasp_binary, work_dir=ctx.work_dir,
            srun_args=ctx.srun_args, salloc_args=ctx.salloc_per_dir,
        )

    if compute_mode == "sbatch_mix":
        if not ctx.inside_salloc:
            raise RuntimeError(
                "sbatch_mix reached dispatch without ctx.inside_salloc=True — "
                "this step should only run inside the single big sbatch "
                "allocation submitted by automation_raman_analysis.py."
            )
        total_gpus = (int(os.environ.get("SLURM_NNODES")
                          or os.environ.get("SLURM_JOB_NUM_NODES")
                          or 1) * 4)
        max_retries = getattr(ctx, "vasp_max_restarts", 3)
        ok = False
        for attempt in range(1, max_retries + 1):
            incomplete = [d for d in all_dirs if not is_calculation_complete(d)]
            if not incomplete:
                ok = True
                break
            print(f"  [sbatch_mix] Attempt {attempt}/{max_retries}: "
                  f"{len(incomplete)}/{len(all_dirs)} dirs remaining…")
            run_dirs_in_parallel_batches(
                incomplete, ctx.vasp_binary, ctx.vasp_srun_per_dir,
                gpus_per_dir=ctx.vasp_gpus_per_dir, total_gpus=total_gpus,
                log_name=mix_log_name,
            )
            # re-checked via is_calculation_complete at the top of the next iteration
        return ok

    # interactive_manual (default), or sbatch_serial/interactive_serial already
    # inside an existing salloc — same execution path as interactive_manual,
    # just dispatched there from a different top-level mode.
    return manual_runner(todo)
