"""
Diagnostic Runner Module

This module locates and executes runner scripts for each target resource.
Diagnostics are run both with and without SMT to measure interference.

To add a new resource:
1. Create a runner script at: run/scripts/{resource}.py
2. The script receives (bin_dir, output_dir, comma_separated_core_ids) as command-line arguments
3. Execute diagnostics and save results
"""

import os
import sys
import glob
import subprocess


def get_core_id():
    """
    Get SMT sibling core IDs from CPU topology.
    
    Returns:
        tuple[str, str]: (core0, core1) - Two logical CPUs on the same physical core
    
    Raises:
        RuntimeError: If SMT is not enabled or topology cannot be determined
    """
    output = subprocess.run(
        ["cat", "/sys/devices/system/cpu/cpu0/topology/thread_siblings_list"],
        capture_output=True,
        text=True,
        check=True
    )
    try:
        core_ids = sorted(output.stdout.strip().split(','), key=int)
    except:
        core_ids = sorted(output.stdout.strip().split('-'), key=int)
    if len(core_ids) < 2:
        raise RuntimeError("SMT not enabled or unable to determine core IDs.")
    return core_ids[0], core_ids[1]


def run_script(runner_script, bin_dir, output_dir, coreids):
    """Execute a runner script using the current Python interpreter."""
    result = subprocess.run(
        [sys.executable, runner_script, bin_dir, output_dir, ",".join(coreids)],
        text=True,
        check=False,  # Don't raise exception on failure
    )

    print("returncode:", result.returncode)

def run_runner(target_resource, bin_dir, output_dir):
    """
    Run diagnostics for a target resource with and without SMT.
    
    Args:
        target_resource: Resource type (e.g., 'load_isq', 'int_isq')
        bin_dir: Directory containing diagnostic binaries
        output_dir: Directory for output files
    
    Raises:
        ValueError: If runner script is not found
    """
    runner_script = f"run/scripts/{target_resource}.py"
    if not os.path.isfile(runner_script):
        raise ValueError(f"Runner script not found for resource type '{target_resource}'")
    else:
        # Get SMT sibling cores
        core0, core1 = get_core_id()
        
        # Run without SMT (single thread only)
        os.makedirs(f"{output_dir}/wo_smt", exist_ok=True)
        run_script(runner_script, bin_dir, f"{output_dir}/wo_smt", (core0,))

        # Run with SMT (both sibling threads active)
        os.makedirs(f"{output_dir}/w_smt", exist_ok=True)
        run_script(runner_script, bin_dir, f"{output_dir}/w_smt", (core0, core1))