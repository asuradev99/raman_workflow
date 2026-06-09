"""INCAR generation from YAML config."""


def build_incar_content(config, stage):
    """Assemble an INCAR file content string from YAML config sources.

    Assembly order (VASP takes the first occurrence of a duplicate tag):

      1. Per-material overrides (``incar_settings.{stage}``), if present.
      2. Base template (``incar_templates.{stage}``).

    Parameters
    ----------
    config : dict
        The merged pipeline configuration.
    stage : str
        One of ``"relax"``, ``"dielec"``, ``"hf"``, or ``"supercell_relax"``.

    Returns
    -------
    str
        Complete INCAR file content ready to write to disk.
    """
    templates = config.get("incar_templates", {})
    base = templates.get(stage, "")
    if not base:
        raise KeyError(
            f"Missing incar_templates.{stage} in pipeline config. "
            f"Available stages: {list(templates.keys())}"
        )

    per_material = config.get("incar_settings", {}).get(stage, "")

    parts = []
    if per_material:
        parts.append(per_material)
    parts.append(base)

    return "\n".join(parts) + "\n"


def write_incar(path, config, stage):
    """Assemble and write an INCAR file from YAML config.

    Combines :func:`build_incar_content` + file write.
    """
    content = build_incar_content(config, stage)
    with open(path, "w") as f:
        f.write(content)
