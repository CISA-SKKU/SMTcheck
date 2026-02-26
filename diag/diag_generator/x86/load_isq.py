"""
Load Issue Queue Diagnostic Generator

Generates diagnostic programs that stress the load issue queue by
creating varying numbers of outstanding load operations.

The generated code uses pointer chasing with additional load operations
to fill the issue queue to different levels.
"""

import sys
import subprocess
from multiprocessing import Pool, cpu_count

# Base assembly template for load issue queue stress
# Uses pointer chasing (movq through r13) with additional loads to r8
base = """\
    asm volatile(
    "movq %[RandomArray0], %%r13"
    :
    : [RandomArray0] "m" (arr0)
    : "%r13" 
    );
    asm volatile(
    "movq %[RandomArray1], %%r14"
    :
    : [RandomArray1] "m" (arr1) 
    : "%r14" 
    );
    asm volatile("xorq %r12, %r12");
    asm volatile("xorpd %xmm3, %xmm3");
MainLoop:
    asm volatile("movq (%r13), %r13");
//filling instructions
    asm volatile("movq (%r12, %r14, 1), %r14");
    asm volatile("lfence":::"memory");
goto MainLoop;
"""

def filler(num_ops):
    """
    Generate filling instructions to occupy issue queue slots.
    
    Args:
        num_ops: Number of load operations to insert
    
    Returns:
        Complete assembly code with filling instructions inserted
    """
    block = f"""\
    asm volatile("xorq %r13, %r15");
    asm volatile("xorq %r13, %r15");
    asm volatile(".rept({num_ops})");
    asm volatile("movq (%r15), %r8");
    asm volatile(".endr");
"""
    return base.replace("//filling instructions", block)


def generator(args):
    """
    Generate and compile a diagnostic binary for specific operation count.
    
    Args:
        args: Tuple of (code_gen_dir, bin_dir, num_ops)
    
    Returns:
        Tuple of (return_code, stderr, num_ops)
    """
    template_file = f"templates/queue_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_gen_dir, bin_dir, num_ops = args    
    code_name = f"{code_gen_dir}/load_isq.{num_ops}.cpp"
    bin_name = f"{bin_dir}/load_isq.{num_ops}.diag"
    code = template.replace("//Insert point", filler(num_ops))

    with open(code_name, "w") as code_file:
        code_file.write(code)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr, num_ops
    
if __name__ == "__main__":
    start_num_ops = 1
    end_num_ops = 70
    
    num_ops_list = list(range(start_num_ops, end_num_ops + 1))
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]

    with Pool(processes=cpu_count()) as pool:
        for returncode, stderr, num_ops in pool.imap(generator, [(code_gen_dir, bin_dir, num_ops) for num_ops in num_ops_list]):
            if returncode != 0:
                print(f"[ERROR] op={num_ops}, returncode={returncode}")
                print(stderr)