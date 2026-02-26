"""
Load Issue Queue Diagnostic Runner

Runs the load issue queue diagnostics and measures IPC.
With SMT enabled, a dummy process runs on the sibling thread
to create contention for shared resources.
"""

import subprocess
import glob
import sys

if __name__ == "__main__":
    bin_dir = sys.argv[1]
    output_dir = sys.argv[2]
    coreids = sys.argv[3].split(",")

    # Sort diagnostics by operation count (extracted from filename)
    diag_list = sorted(
        glob.glob(f"{bin_dir}/*.diag"), 
        key=lambda x: int(x.split(".")[-2])
    )
    
    dummy_process = None
    smt_mode = "wo_smt"
    
    # If SMT mode, run a dummy diagnostic on the sibling thread
    if len(coreids) == 2:
        smt_mode = "w_smt"
        dummy_process = subprocess.Popen(
            ["taskset", "-c", coreids[1], diag_list[0]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

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
        dummy_process.terminate()
        dummy_process.wait()