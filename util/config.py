"""YAML config loading, merging, and srun-arg construction."""

import os
import re

import yaml


def merge_config(target_config, file_config, label=""):
    """Deep-merge a YAML config dict into a target config dict."""
    if file_config is None:
        return
    for section, values in file_config.items():
        if section.startswith("_"):
            continue
        if isinstance(target_config.get(section), dict) and isinstance(values, dict):
            target_config[section].update(values)
        else:
            target_config[section] = values


def load_config(paths):
    """Load and deep-merge YAML config files in order; later files override earlier."""
    config = {}
    for path, label in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            merge_config(config, data, label)
            print(f"Loaded {label} config from {path}")
        except Exception as e:
            print(f"Warning: Could not load {label} config {path}: {e}")
    if not config:
        print("Warning: No config files loaded — all settings will be empty.")
    return config


def build_srun_args(config, cpu_flag=False):
    """Build an srun argument string from the pipeline config.

    If the config contains a ``raw`` key under ``vasp_srun`` or
    ``vasp_srun_cpu``, it is returned verbatim.  Without ``raw``, the
    string is assembled from individual keys with Python-level defaults.
    """
    key = "vasp_srun_cpu" if cpu_flag else "vasp_srun"
    cfg = config.get(key, {}) if isinstance(config, dict) else {}

    if "raw" in cfg:
        return cfg["raw"]

    if cpu_flag:
        ntasks = cfg.get("ntasks", 32)
        cpus_per_task = cfg.get("cpus_per_task", 4)
        return (f"--cpu_bind=cores --ntasks {ntasks} "
                f"--cpus-per-task {cpus_per_task}")
    else:
        gpus = cfg.get("gpus", 4)
        ntasks = cfg.get("ntasks", 4)
        cpus_per_task = cfg.get("cpus_per_task", 32)
        constraint = cfg.get("constraint", "gpu")
        return (f"--cpu_bind=cores --gpus {gpus} "
                f"--ntasks {ntasks} --cpus-per-task {cpus_per_task} "
                f"-C {constraint}")


def split_srun_args(srun_args, num_dirs):
    """Split an srun arg string into *num_dirs* proportional copies.

    Parses ``--gpus N`` and ``--ntasks M``, divides by *num_dirs* (floor).
    GPU count is split as a bare ``--gpus N`` (NOT ``--gpus-per-task=1``,
    which breaks NCCL).  With ``--overlap``, Slurm shares the GPU pool.
    """
    if num_dirs < 1:
        return []

    gpus_match = re.search(r'--gpus\s+(\d+)', srun_args)
    ntasks_match = re.search(r'--ntasks\s+(\d+)', srun_args)
    if not gpus_match or not ntasks_match:
        return []

    total_gpus = int(gpus_match.group(1))
    total_ntasks = int(ntasks_match.group(1))
    gpus_per = total_gpus // num_dirs
    ntasks_per = total_ntasks // num_dirs

    if gpus_per < 1 or ntasks_per < 1:
        return []

    result = []
    for _ in range(num_dirs):
        args = re.sub(r'--gpus\s+\d+', f'--gpus {gpus_per}', srun_args)
        args = re.sub(r'--ntasks\s+\d+', f'--ntasks {ntasks_per}', args)
        result.append(args)

    idle_gpus = total_gpus - (gpus_per * num_dirs)
    if idle_gpus:
        print(f"  [hf_parallel] {idle_gpus} GPU(s) idle "
              f"({total_gpus} not divisible by {num_dirs})")

    return result
