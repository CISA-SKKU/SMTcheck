"""
Diagnostic Generator Module

This module locates and executes ISA-specific generator scripts
to create diagnostic binaries for each target resource.

To add a new resource:
1. Create a generator script at: diag_generator/{isa}/{resource}.py
2. The script receives (code_gen_dir, bin_dir) as command-line arguments
3. Generate assembly code and compile to binary
"""

import os
import sys
import subprocess


def run_script(generator_script, code_gen_dir, bin_dir):
    """Execute a generator script using the current Python interpreter."""
    result = subprocess.run(
        [sys.executable, generator_script, code_gen_dir, bin_dir],
        capture_output=True,
        text=True,
        check=False,  # Don't raise exception on failure
    )

    print("stdout:")
    print(result.stdout)
    print("stderr:")
    print(result.stderr)
    print("returncode:", result.returncode)

def run_generator(target_resource, isa, code_gen_dir, bin_dir):
    generator_script = f"diag_generator/{isa}/{target_resource}.py"
    if not os.path.isfile(generator_script):
        raise ValueError(f"Generator script not found for operation type '{target_resource}' and ISA '{isa}'")
    else:
        run_script(generator_script, code_gen_dir, bin_dir)