"""YAML config loading, merging, validation, and srun-arg construction."""

import os
import re
import sys

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


def get_srun_args(config, mode, key="srun_relax", cpu_flag=False):
    """Get srun args from ``compute_modes.<mode>.<key>`` in the config.

    Falls back to ``vasp_srun_cpu.raw`` for CPU, or raises KeyError if
    the mode/key combination is missing.
    """
    if cpu_flag:
        cpu_cfg = config.get("vasp_srun_cpu", {})
        if isinstance(cpu_cfg, dict) and "raw" in cpu_cfg:
            return cpu_cfg["raw"]
        return "--cpu_bind=cores --ntasks 32 --cpus-per-task 4"

    modes = config.get("compute_modes", {})
    mode_cfg = modes.get(mode, {})
    args = mode_cfg.get(key, "")
    if args:
        return args
    raise KeyError(
        f"Missing compute_modes.{mode}.{key} in config. "
        f"Available modes: {list(modes.keys())}"
    )


# ── Required config keys (checked after loading) ──────────────────────────
REQUIRED_CONFIG = {
    "phonopy": ["dim", "amplitude", "band_path", "band_labels", "band_points"],
    "scf_kpoints": ["mesh", "shift"],
    "sup_relax_kpoints": ["mesh", "shift"],
    "hf_kpoints": ["mesh", "shift"],
    "raman_kpoints": ["mesh", "shift"],
    "desired_energies": None,          # must exist, any value
    "raman_tensor": ["incident_polarization", "scattered_polarization", "surface_normal"],
    "vasp_loop": ["max_restarts"],
    "eigenvectors_band": ["path", "labels", "points"],
    "broadening": ["mode", "hwhm", "interpolation", "normalization"],
    "compute_modes": None,
    "vasp_srun_cpu": None,
    "system_paths": None,
    "incar_templates": ["relax", "dielec", "hf", "supercell_relax"],
}


def validate_config(config):
    """Check that all required config keys are present.

    Prints a clear error for each missing key and exits with code 1
    if any are absent.  This replaces the old fallback-template approach
    where missing keys were silently empty.
    """
    missing = []
    for section, keys in REQUIRED_CONFIG.items():
        if section not in config:
            missing.append(f"  [{section}] section missing entirely")
            continue
        if keys is None:
            continue  # just check existence, done above
        for key in keys:
            if key not in config[section]:
                missing.append(f"  [{section}] -> '{key}' missing")

    if missing:
        print("ERROR: Required config key(s) missing:")
        for m in missing:
            print(m)
        print()
        print("Make sure both shared_workflow_settings.yaml and")
        print("your per-material input/workflow_settings.yaml define all required keys.")
        print("See raman_workflow/examples/ for a working template.")
        sys.exit(1)


def split_srun_args(srun_args, num_dirs):
    """Split an srun arg string into *num_dirs* proportional copies.

    Parses ``--gpus N`` and ``--ntasks M``, divides by *num_dirs* (floor).
    GPU count is split as a bare ``--gpus N`` (NOT ``--gpus-per-task=1``,
    which breaks NCCL).  With ``--overlap``, Slurm shares the GPU pool.
    """
    if num_dirs < 1:
        return []

    gpus_match = re.search(r'--gpus[=\s]+(\d+)', srun_args)
    ntasks_match = re.search(r'--ntasks[=\s]+(\d+)', srun_args)
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
        args = re.sub(r'--gpus[=\s]+\d+', f'--gpus {gpus_per}', srun_args)
        args = re.sub(r'--ntasks[=\s]+\d+', f'--ntasks {ntasks_per}', args)
        result.append(args)

    idle_gpus = total_gpus - (gpus_per * num_dirs)
    if idle_gpus:
        print(f"  [hf_parallel] {idle_gpus} GPU(s) idle "
              f"({total_gpus} not divisible by {num_dirs})")

    return result


