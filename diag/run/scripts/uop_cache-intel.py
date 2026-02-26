"""
Micro-op Cache (µop Cache) Diagnostic Runner

Runs the micro-op cache diagnostics and measures IPC.
The diagnostics fill the µop cache (Decoded ICache / DSB) with
varying numbers of entries to characterize cache contention behavior.

With SMT enabled, a dummy process runs on the sibling thread
to create contention for shared µop cache resources.

Usage:
    python uop_cache-intel.py <bin_dir> <output_dir> <core_id>[,<sibling_core_id>]
    
Arguments:
    bin_dir: Directory containing compiled diagnostic binaries
    output_dir: Directory to store output results
    coreids: Comma-separated core IDs (single for solo, pair for SMT mode)
"""

import subprocess
import multiprocessing as mp
import glob
import sys
import signal
import os


def dummy_worker():
    global pinned_cpu_core
    """Worker function that runs in a separate process group and spins forever."""
    os.setsid()   # Create new session to separate from parent process group
    os.sched_setaffinity(0, {int(pinned_cpu_core)})  # Pin to specified CPU core (0 = current process)
    while True:
        pass

if __name__ == "__main__":
    # Parse command line arguments
    bin_dir = sys.argv[1]
    output_dir = sys.argv[2]
    coreids = sys.argv[3].split(",")

    # Sort diagnostics by way count (extracted from filename: diag.N.diag)
    diag_list = sorted(
        glob.glob(f"{bin_dir}/*.diag"), 
        key=lambda x: int(x.split(".")[-2])
    )
    
    dummy_process = None
    smt_mode = "wo_smt"  # Without SMT contention
    
    # If SMT mode, run a dummy diagnostic on the sibling thread to create contention
    if len(coreids) == 2:
        smt_mode = "w_smt"  # With SMT contention
        pinned_cpu_core = coreids[1]
        dummy_process = mp.Process(target=dummy_worker)
        dummy_process.start()

    # Run each diagnostic and collect results
    for diag in diag_list:
        print(f"Running diagnostic [{smt_mode}]: {diag}")

        # Run diagnostic for 1 second, then interrupt to collect counters
        result = subprocess.run(
            ["timeout", "-s", "SIGINT", "1s", "taskset", "-c", coreids[0], diag],
            capture_output=True,
            text=True,
            check=False,
        )

        # Exit code 124 means timeout occurred (expected behavior)
        if result.returncode != 124:
            print(f"Diagnostic {diag} failed with return code {result.returncode}")
            print("stdout:")
            print(result.stdout)
            print("stderr:")
            print(result.stderr)
        else:
            # Save output to file
            output_filename = diag.split('/')[-1].replace('.diag', '.out')
            with open(f"{output_dir}/{output_filename}", "w") as out_file:
                out_file.write(result.stdout)
                
    # Cleanup dummy process if running
    if dummy_process:
        os.kill(dummy_process.pid, signal.SIGTERM)