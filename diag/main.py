"""
Diagnostic Generation and Execution Entry Point

This script orchestrates the generation and execution of diagnostic programs
for measuring microarchitectural resource contention.

Usage:
    python main.py --target_resource load_isq --isa x86
    python main.py --target_resource int_isq --skip_diag_gen 1

Steps:
    1. Generate C++ code from templates with resource-specific assembly
    2. Compile diagnostic binaries
    3. Run diagnostics with and without SMT enabled
    4. Save IPC measurements to output files
"""

import sys
import argparse
import os
from diag_generator import diag_generator
from run import runner

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate and run microarchitectural diagnostics"
    )
    parser.add_argument(
        "--target_resource", 
        type=str, 
        required=True,
        help="Target resource to stress (e.g., load_isq, uop_cache, l1_dcache)"
    )
    parser.add_argument(
        "--isa", 
        type=str, 
        default="x86",
        help="Instruction set architecture (default: x86)"
    )
    parser.add_argument(
        "--skip_diag_gen", 
        type=int, 
        default=0, 
        choices=[0, 1],
        help="Skip diagnostic generation if set to 1 (default: 0)"
    )
    args = parser.parse_args()

    code_gen_dir = f"code/{args.target_resource}"
    bin_dir = f"bin/{args.target_resource}"
    
    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(code_gen_dir, exist_ok=True)

    # Step 1: Generate code and compile diagnostic binaries
    try:
        if args.skip_diag_gen == 0:
            os.system(f"rm -rf {code_gen_dir}/*")
            os.system(f"rm -rf {bin_dir}/*")
            diag_generator.run_generator(args.target_resource, args.isa, code_gen_dir, bin_dir)
        else:
            print("[INFO] Skipping diagnostic generation as per user request.")
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
    
    # Step 2: Run diagnostics and collect measurements
    output_dir = f"outputs/{args.target_resource}"
    os.makedirs(output_dir, exist_ok=True)
    os.system(f"rm -rf {output_dir}/*")
    try:
        runner.run_runner(args.target_resource, bin_dir, output_dir)
    except ValueError as e:
        print(e)
        sys.exit(1)