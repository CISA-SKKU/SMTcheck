"""
L1 Data TLB (DTLB) Diagnostic Runner

Runs the L1 data TLB diagnostics to measure TLB contention behavior.
This script tests different stride sizes and associativity (ways) to
characterize how the TLB responds to different access patterns.

Unlike cache diagnostics, TLB diagnostics do NOT use hugepages since
we're specifically testing page table entry contention.

Usage:
    python l1_dtlb.py <bin_dir> <output_dir> <core_id>[,<sibling_core_id>]
    
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

    # Find diagnostic binary
    diag_list = sorted(glob.glob(f"{bin_dir}/*.diag"))
    if len(diag_list) != 1:
        print(f"Expected exactly one diagnostic file, found {len(diag_list)}")
        sys.exit(1)
    
    diag = diag_list[0]
    
    # Configuration parameters for L1 DTLB testing
    max_ways = 16          # Maximum associativity to test
    use_hugepage = 0       # Disable hugepages (testing TLB, not cache)
    num_sets = 1           # Number of TLB sets to target
    start, end = 12, 20    # Stride range: 2^12 (4KB pages) to 2^20 (1MB)

    # If SMT mode (two cores specified), start a dummy process on sibling thread
    dummy_process = None
    if len(coreids) == 2:
        pinned_cpu_core = coreids[1]
        dummy_process = mp.Process(target=dummy_worker)
        dummy_process.start()

    # Test all combinations of stride and associativity
    for stride_log2 in range(start, end+1):
        stride = 1 << stride_log2  # Convert log2 to actual stride value
        
        for num_ways in range(1, max_ways + 1):
            print(f"Running diagnostic: {diag} with stride {stride} and ways {num_ways}")

            # Run diagnostic for 1 second, then interrupt to collect counters
            result = subprocess.run(
                ["timeout", "-s", "SIGINT", "1s", "taskset", "-c", coreids[0], diag, 
                 str(use_hugepage), str(stride), str(num_sets), str(num_ways)],
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
                # Save output with stride and ways in filename
                output_filename = diag.split('/')[-1].replace('.diag', f'.stride{stride}.ways{num_ways}.out')
                with open(f"{output_dir}/{output_filename}", "w") as out_file:
                    out_file.write(result.stdout)

    # Cleanup dummy process if running
    if dummy_process:
        os.kill(dummy_process.pid, signal.SIGTERM)