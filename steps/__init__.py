"""Pipeline step dispatch."""

from . import scf_relax, supercell, hf_setup, force_constants, phonon_post, raman_prep, resonant_vasp, post_process

STEP_FUNCTIONS = {
    1: scf_relax,
    2: supercell,
    3: hf_setup,
    4: force_constants,
    5: phonon_post,
    6: raman_prep,
    7: resonant_vasp,
    8: post_process,
}


def build_context(write_status, config, material_dir, material_name,
                  work_dir, srun_args, vasp_binary, hffiles_dir, raman_dir,
                  script_dir, binary_utilities_dir, cpu_flag, scratch_flag,
                  run_relaxation, vasp_loop_fn):
    return {
        "write_status": write_status, "config": config,
        "material_dir": material_dir, "material_name": material_name,
        "work_dir": work_dir, "srun_args": srun_args,
        "vasp_binary": vasp_binary, "hffiles_dir": hffiles_dir,
        "raman_dir": raman_dir, "script_dir": script_dir,
        "binary_utilities_dir": binary_utilities_dir,
        "cpu_flag": cpu_flag, "scratch_flag": scratch_flag,
        "run_relaxation": run_relaxation, "vasp_loop_fn": vasp_loop_fn,
        "phonopy_dim": config["phonopy"]["dim"],
        "phonopy_amplitude": config["phonopy"]["amplitude"],
        "phonopy_band_points": config["phonopy"].get("band_points", 101),
        "scf_kpoints_mesh": config["scf_kpoints"]["mesh"],
        "scf_kpoints_shift": config["scf_kpoints"]["shift"],
        "sup_relax_kpoints_mesh": config["sup_relax_kpoints"]["mesh"],
        "sup_relax_kpoints_shift": config["sup_relax_kpoints"]["shift"],
        "hf_kpoints_mesh": config["hf_kpoints"]["mesh"],
        "hf_kpoints_shift": config["hf_kpoints"]["shift"],
        "raman_kpoints_mesh": config["raman_kpoints"]["mesh"],
        "raman_kpoints_shift": config["raman_kpoints"]["shift"],
        "desired_energies": config["desired_energies"],
        "raman_incident_pol": config["raman_tensor"]["incident_polarization"],
        "raman_scattered_pol": config["raman_tensor"]["scattered_polarization"],
        "raman_surface_normal": config["raman_tensor"]["surface_normal"],
        "vasp_max_restarts": config["vasp_loop"]["max_restarts"],
        "hf_parallel": config.get("hf_parallel", False),
        "start_from_supercell": config.get("start_from_supercell", False),
        "eigvec_band_path": config["eigenvectors_band"]["path"],
        "eigvec_band_labels": config["eigenvectors_band"]["labels"],
        "eigvec_band_points": config["eigenvectors_band"]["points"],
    }
