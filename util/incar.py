"""VASP input file generation: INCAR (from YAML config) and KPOINTS."""

import os
import shutil


def _parse_incar(text):
    """Parse INCAR text into an ordered {tag: value} dict.
    
    Handles multi-word values (e.g., ``LATTICE_CONSTRAINTS = .TRUE. .TRUE. .FALSE.``).
    Skips blank lines and comments (``#``, ``!``).
    """
    tags = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            tag, value = line.split("=", 1)
            tags[tag.strip()] = value.strip()
    return tags


def _format_incar(tags):
    """Format an ordered {tag: value} dict back into INCAR text."""
    return "\n".join(f"{tag} = {val}" for tag, val in tags.items())


def build_incar_content(config, step_name):
    """Assemble an INCAR file content string from YAML config sources.

    Reads ``config["steps"][step_name]["incar"]`` as the base template and
    ``config["steps"][step_name]["incar_overrides"]`` for per-material tag
    replacements.

    Override tags appear first; duplicate template tags are removed so each
    tag appears exactly once in the final INCAR.

    Parameters
    ----------
    config : dict
        The merged pipeline configuration.
    step_name : str
        Step name key under ``config["steps"]`` (e.g. ``"scf_relax"``,
        ``"force_consts"``, ``"resonant_vasp"``).

    Returns
    -------
    str
        Complete INCAR file content ready to write to disk.
    """
    steps = config.get("steps", {})
    template_text = steps.get(step_name, {}).get("incar", "")
    if not template_text:
        raise KeyError(
            f"Missing steps.{step_name}.incar in pipeline config. "
            f"Available steps: {list(steps.keys())}"
        )

    override_text = steps.get(step_name, {}).get("incar_overrides", "")

    # No overrides — just return the template as-is
    if not override_text:
        return template_text.strip() + "\n"

    # Parse both into ordered {tag: value} dicts
    template_tags = _parse_incar(template_text)
    override_tags = _parse_incar(override_text)

    # Remove overridden tags from the template so the override value is
    # the only occurrence — VASP sees it first and uses it.
    for tag in override_tags:
        template_tags.pop(tag, None)

    # Assemble: overrides first, then (stripped) template
    parts = []
    parts.append(_format_incar(override_tags))
    if template_tags:
        parts.append(_format_incar(template_tags))

    return "\n".join(parts) + "\n"


def write_incar(path, config, step_name):
    """Assemble and write an INCAR file from YAML config."""
    content = build_incar_content(config, step_name)
    with open(path, "w") as f:
        f.write(content)


def write_kpoints(path, comment, mesh, shift):
    """Write a Gamma-centred KPOINTS file."""
    with open(path, "w") as f:
        f.write(f"{comment}\n0\nGamma\n{mesh}\n{shift}\n")


def write_vasp_inputs(directory, work_dir, config, stage, mesh, shift, comment="K-points"):
    """Write INCAR and KPOINTS, and copy POTCAR into *directory*.

    Combines the three-line triad that every setup step repeats before
    running VASP::

        write_incar(os.path.join(d, "INCAR"), config, stage)
        write_kpoints(os.path.join(d, "KPOINTS"), comment, mesh, shift)
        shutil.copy(os.path.join(work_dir, "input", "POTCAR"), d)
    """
    write_incar(os.path.join(directory, "INCAR"), config, stage)
    write_kpoints(os.path.join(directory, "KPOINTS"), comment, mesh, shift)
    shutil.copy(os.path.join(work_dir, "input", "POTCAR"), directory)
