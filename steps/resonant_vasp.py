"""Step 14 — Resonant VASP runs in all ra_pos_* directories."""
import os, time, glob
from util.io import run_command
from util.vasp import check_vasp_convergence, check_dielectric_complete, check_no_selective_dynamics
from util.status import print_step_header, print_step_result

def run(ctx):
    ws=ctx["write_status"]; R=ctx["raman_dir"]; SD=ctx["script_dir"]; SR=ctx["srun_args"]
    print_step_header(7); ws(7,"running","Resonant VASP runs"); _t0=time.time()
    s=os.path.join(SD,"scripts","run_all_vasp_folders_fixed.sh")
    if not os.path.exists(s): raise FileNotFoundError(f"run_all_vasp_folders_fixed.sh not found at {s}")
    ra=sorted(glob.glob(os.path.join(R,"ra_pos_*")))
    if ra: check_no_selective_dynamics(os.path.join(ra[0],"POSCAR"),"ra_pos_* POSCAR")
    run_command(f"export SRUN_ARGS='{SR}' && bash {s}")
    ra2=sorted(glob.glob(os.path.join(R,"ra_pos_*")))
    if not ra2: raise RuntimeError("No ra_pos_* directories produced")
    for d in ra2: check_vasp_convergence(d,"step-7"); check_dielectric_complete(d,"step-7")
    ws(7,"completed",f"Resonant VASP — {len(ra2)} dirs validated")
    print_step_result(7,ok=True,duration_s=time.time()-_t0,message=f"{len(ra2)} directories")
