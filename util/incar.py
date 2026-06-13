"""INCAR generation from YAML config — with tag-level override merging."""


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


def build_incar_content(config, stage):
    """Assemble an INCAR file content string from YAML config sources.

    Assembly logic:
      1. Parse the base template and per-material overrides into tag→value dicts.
      2. Remove any tags from the template that are also in the override.
      3. Prepend the override block, then append the (stripped) template.

    This guarantees that overridden tags (e.g., ``IBRION``, ``KPAR``) appear
    **first** in the INCAR — VASP uses the first occurrence for most tags —
    and the duplicate is removed entirely so there is no ambiguity.

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
    template_text = templates.get(stage, "")
    if not template_text:
        raise KeyError(
            f"Missing incar_templates.{stage} in pipeline config. "
            f"Available stages: {list(templates.keys())}"
        )

    override_text = config.get("incar_settings", {}).get(stage, "")

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


def write_incar(path, config, stage):
    """Assemble and write an INCAR file from YAML config."""
    content = build_incar_content(config, stage)
    with open(path, "w") as f:
        f.write(content)
