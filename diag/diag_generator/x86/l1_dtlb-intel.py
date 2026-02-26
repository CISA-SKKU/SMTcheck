"""
L1 Data TLB Diagnostic Generator (Intel)

Generates a diagnostic program that stresses the L1 data TLB by
performing pointer chasing across different memory pages.

The l1_dtlb-intel.cpp template is self-contained with embedded assembly.
This generator simply compiles the template - the TLB geometry
(num_sets, num_ways, stride) is configured at runtime via command-line
arguments to the compiled diagnostic binary.
"""

import sys
import subprocess
    
if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]

    # Template is self-contained - just compile it directly
    template_file = "templates/l1_dtlb-intel.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_name = f"{code_gen_dir}/l1_dtlb-intel.cpp"
    bin_name = f"{bin_dir}/l1_dtlb-intel.diag"

    with open(code_name, "w") as code_file:
        code_file.write(template)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, "-lpfm"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] returncode={result.returncode}")
        print(result.stderr)