#!/usr/bin/env python3
"""Standalone: regenerate VESTA phonon-mode files for one material.

Re-derives hf/VESTA_MODES/*.vesta from the hf/band.yaml + hf/POSCAR_unitcell
already on disk from a prior phonon_post run -- no VASP, no phonopy re-run,
just the visualization step. Useful after changing
steps.phonon_post.visualization.scale_factor (or vesta_template) in the
config without wanting to redo the rest of phonon_post.

Usage:
    python raman_workflow/scripts/rerun_vesta.py <material_name>

Reads config the same way the pipeline does (shared + per-material YAML),
and looks for hf/ on $SCRATCH first (if this material was run with
--scratch), falling back to the material directory on HOME.
"""
import os
import sys
from types import SimpleNamespace

_RAMAN_WORKFLOW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAMAN_WORKFLOW_DIR not in sys.path:
    sys.path.insert(0, _RAMAN_WORKFLOW_DIR)

from util.config import load_config, validate_config
from util.visualize import generate_phonon_visuals

if len(sys.argv) != 2:
    print("Usage: python rerun_vesta.py <material_name>")
    sys.exit(1)

MATERIAL_NAME = sys.argv[1]
BASE_PROJECT_DIR = os.environ.get("RAMAN_PROJECT_DIR", "")
if not os.path.isdir(BASE_PROJECT_DIR):
    print("Error: RAMAN_PROJECT_DIR not set or does not exist.")
    sys.exit(1)

MATERIAL_DIR = os.path.join(BASE_PROJECT_DIR, MATERIAL_NAME)
SHARED_CONFIG = os.path.join(BASE_PROJECT_DIR, "shared_workflow_settings.yaml")
PER_MAT_CONFIG = os.path.join(MATERIAL_DIR, "input", "workflow_settings.yaml")
if not os.path.isfile(PER_MAT_CONFIG):
    print(f"Error: no input/workflow_settings.yaml in {MATERIAL_DIR}.")
    sys.exit(1)

cfg = load_config([(SHARED_CONFIG, "shared"), (PER_MAT_CONFIG, "material")])
validate_config(cfg, ["phonon_post"])

# hf/ lives on $SCRATCH if this material was run with --scratch, else on HOME.
scratch_base = os.environ.get("SCRATCH", "")
scratch_hf = os.path.join(scratch_base, "vasp_calculations", MATERIAL_NAME, "hf") if scratch_base else ""
if scratch_base and os.path.isdir(scratch_hf):
    hf_dir = scratch_hf
else:
    hf_dir = os.path.join(MATERIAL_DIR, "hf")

band_yaml = os.path.join(hf_dir, "band.yaml")
if not os.path.isfile(band_yaml):
    print(f"Error: {band_yaml} not found -- run phonon_post at least once first.")
    sys.exit(1)

viz = cfg["steps"]["phonon_post"].get("visualization", {})
ctx = SimpleNamespace(
    material_dir=MATERIAL_DIR,
    system_paths=cfg.get("system_paths", {}),
    viz_scale_factor=float(viz.get("scale_factor", 0.5)),
    viz_output_format=viz.get("output_format", "vesta").lower(),
    viz_vesta_template=viz.get("vesta_template", "template.vesta"),
)

print(f"Material:     {MATERIAL_NAME}")
print(f"hf_dir:       {hf_dir}")
print(f"scale_factor: {ctx.viz_scale_factor}")
print(f"template:     {ctx.viz_vesta_template}")
print()

generate_phonon_visuals(hf_dir, ctx)
