# Raman Pipeline — Refactoring Notes

Tracks design decisions and remaining cleanup ideas.

---

## Completed refactors

### OOP: PipelineContext + Step class (June 2026)

- `src/__init__.py` — `Step` frozen dataclass with `number`, `name`, `description`, `run(ctx)`. `PIPELINE` list replaces the old `STEP_FUNCTIONS` dict.
- `PipelineContext` dataclass replaces the 30-key untyped `ctx` dict. All YAML-derived fields are extracted in `__post_init__`; step modules use `ctx.field` throughout.
- Auto-provision logic moved out of `scf_relax.run()` into the dispatch prelude in `automation_raman_analysis.py`. Step modules are now pure step runners.
- `util/compute.py` gained `build_bash_setup`, `build_serial_vasp_wrapper` (shared between `force_constants` and `resonant_vasp`, was duplicated), and `run_pipeline_in_salloc`.

### Symlinks merged (June 2026)

`update_wavecar_symlinks` and `update_chgcar_symlinks` in `util/symlinks.py` were near-identical. Merged into `update_hf_symlinks(hffiles_dir, source_subdir)` which handles both files in one loop. Old names kept as deprecated aliases for any external scripts.

### `hf_POSCAR-*` pattern centralised (June 2026)

The string `"hf_POSCAR-"` was repeated in `vasp_loop.py`, `force_constants.py`, and `symlinks.py`. Added `HF_DIR_PREFIX` constant and `list_hf_dirs(hffiles_dir, include_groundstate=False)` to `util/vasp_loop.py`. `force_constants.py` now calls `list_hf_dirs` and its local `_all_hf_dirs` was removed.

### `_run_serial_dirs` extracted (June 2026)

`_run_cpu` and the retry branch of `_run_gpu_serial` in `vasp_loop.py` were identical loops. Extracted as `_run_serial_dirs(dirs, hffiles_dir, srun_args, vasp_binary)`.

### ZBRENT inline removed (June 2026)

`check_vasp_convergence` in `util/vasp.py` re-implemented the ZBRENT error check inline instead of calling the existing `_has_zbrent_error` + `_extract_max_force` helpers. Fixed to use those helpers.

### `run_command` noise reduced (June 2026)

Added `verbose=False` parameter to `run_command` in `util/io.py`. File-copy operations in `post_process.py` replaced with `shutil.copy2` / `os.makedirs` / `shutil.copytree` — no subprocess needed, no banners printed.

### `STEP_DESCRIPTIONS` single source of truth (June 2026)

`util/status.py` previously had a static `STEP_DESCRIPTIONS` dict duplicating the `Step.description` values in `PIPELINE`. Now `STEP_DESCRIPTIONS` starts empty and is populated at startup by `populate_step_descriptions()` called from `automation_raman_analysis.py`, deriving descriptions directly from `PIPELINE`.

### Resume symlink guard (June 2026)

`raman_prep.py` was calling `os.symlink` on CHGCAR/WAVECAR without checking if the symlink already existed, causing `FileExistsError` on resume runs. Fixed to guard with `not os.path.exists(dst) and not os.path.islink(dst)`. Same fix was previously applied to `scf_relax.py`.

---

## Potential future improvements

### `run_command` exception handling

Currently catches all `Exception` and re-raises if `check_success=True`. This means an `OSError` (e.g. binary not found) prints `--- ERROR ---` even when `check_success=False`. Better: only catch `RuntimeError` from the return-code check, let `OSError`/`FileNotFoundError` propagate naturally.

### `print_step_header` / `print_step_result` description arg

Both functions look up `STEP_DESCRIPTIONS[step_num]` internally. Since the caller (the dispatch loop) already has `step.description`, passing it as a kwarg would avoid the global lookup and make the table description and the banner description guaranteed to match. Change signature to `print_step_header(step_num, description="")` (already exists) and always pass `description=step.description` from the dispatch loop.

### `STEP_HISTORY` global mutable state

`STEP_HISTORY` in `util/status.py` is a module-level dict accumulated across the pipeline run. This makes it impossible to run two pipeline instances in one process and makes unit testing fragile. Long-term: move it into `PipelineContext` and thread it through `write_status`.

### `write_eigenvectors_conf` simplification

`util/phonopy.py:write_eigenvectors_conf` opens the file twice (once to check, once to write) and has a multi-branch "check-and-recreate" structure. It could be simplified: always write the correct content, and print a warning only if the existing content differs.

### Auto-sync `#SBATCH` args with config

`run_raman_pipeline.sbatch` hardcodes `--nodes`, `--gpus-per-node`, `--time` etc. These are also in `shared_workflow_settings.yaml` under `compute_modes`. They can drift. Consider generating the sbatch header from the YAML at submission time or adding a validation check.
