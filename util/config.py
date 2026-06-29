"""YAML config loading, merging, validation, and srun-arg construction."""

import os
import re
import sys

import yaml


def merge_config(target_config, file_config, label=""):
    """Deep-merge a YAML config dict into a target config dict.

    Dict values are merged recursively so a per-material override like
    ``steps.scf_relax.kpoints.mesh`` only replaces ``mesh``, not the
    entire ``kpoints`` block.  Scalar and list values are replaced outright.
    Keys beginning with ``_`` (metadata) are skipped.
    """
    if file_config is None:
        return
    for k, v in file_config.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(target_config.get(k), dict):
            merge_config(target_config[k], v)
        else:
            target_config[k] = v


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

    When ``cpu_flag`` is True, reads ``compute_modes.<mode>.srun_cpu_relax``
    so args match the node count for the active compute mode.
    Raises KeyError if the mode/key combination is missing.
    """
    modes = config.get("compute_modes", {})
    mode_cfg = modes.get(mode, {})
    if cpu_flag:
        args = mode_cfg.get("srun_cpu_relax", "")
        return args or "--cpu_bind=cores --ntasks 32 --cpus-per-task 4"
    args = mode_cfg.get(key, "")
    if args:
        return args
    raise KeyError(
        f"Missing compute_modes.{mode}.{key} in config. "
        f"Available modes: {list(modes.keys())}"
    )


# ── Step-level required sub-keys (validated per active step) ──────────────
STEP_REQUIRED_KEYS = {
    "scf_relax":          ["kpoints", "incar"],
    "defect_relax_1":     ["kpoints", "incar"],
    "defect_relax_2":     ["kpoints", "incar"],
    "defect_relax_2_cpu": ["kpoints", "incar", "cpu_relax"],
    "supercell":          ["kpoints", "incar"],
    "hf_setup":           [],            # writes INCAR/KPOINTS from force_consts config
    "force_consts":       ["kpoints", "incar"],
    "phonon_post":        ["eigenvectors_band", "visualization"],
    "raman_prep":         [],            # writes INCAR/KPOINTS from resonant_vasp config
    "resonant_vasp":      ["kpoints", "incar"],
    "post_process":       ["broadening", "raman_tensor", "desired_energies"],
}

# ── Global required sections (always checked, regardless of step selection) ─
GLOBAL_REQUIRED = {
    "phonopy": ["dim", "amplitude"],
    "vasp_loop": ["max_restarts"],
    "system_paths": None,
    "steps": None,
}

# ── Required keys per compute mode (only the active mode is validated) ────────
COMPUTE_MODE_REQUIRED_KEYS = {
    "interactive_manual": ["srun_relax", "srun_per_dir"],
    "interactive_serial": ["srun_relax", "salloc"],
    "sbatch_parallel":    ["srun_relax", "srun_per_dir", "salloc_relax", "sbatch_per_dir"],
    "sbatch":             ["srun_relax", "srun_per_dir", "salloc_relax", "sbatch_per_dir"],
    "sbatch_serial":      ["srun_relax", "sbatch"],
    "sbatch_mix":         ["srun_relax", "srun_per_dir", "sbatch"],
}


def validate_config(config, step_names):
    """Check that all required config keys are present for the given steps.

    Only validates step-level keys for the steps that are actually being run,
    and only validates compute_mode keys for the mode this simulation will use.
    """
    missing = []
    for section, keys in GLOBAL_REQUIRED.items():
        if section not in config:
            missing.append(f"  [{section}] section missing entirely")
            continue
        if keys is None:
            continue
        for key in keys:
            if key not in config[section]:
                missing.append(f"  [{section}] -> '{key}' missing")

    compute_mode = config.get("compute_mode")
    if not compute_mode:
        missing.append("  [compute_mode] top-level key missing")
    else:
        mode_cfg = config.get("compute_modes", {}).get(compute_mode, {})
        if not mode_cfg:
            missing.append(f"  [compute_modes.{compute_mode}] section missing")
        else:
            for key in COMPUTE_MODE_REQUIRED_KEYS.get(compute_mode, []):
                if key not in mode_cfg:
                    missing.append(f"  [compute_modes.{compute_mode}] -> '{key}' missing")

    steps_cfg = config.get("steps", {})
    for name in step_names:
        required = STEP_REQUIRED_KEYS.get(name, [])
        step = steps_cfg.get(name, {})
        for key in required:
            if key not in step:
                missing.append(f"  [steps.{name}] -> '{key}' missing")

    if missing:
        print("ERROR: Required config key(s) missing:")
        for m in missing:
            print(m)
        print()
        print("Make sure both shared_workflow_settings.yaml and")
        print("your per-material input/workflow_settings.yaml define all required keys.")
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


