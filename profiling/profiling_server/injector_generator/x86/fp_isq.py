"""
Floating-Point Issue Queue Injector Generator

Generates injector programs that stress the floating-point issue queue by
creating varying numbers of FP XOR operations that occupy queue slots.

The generated code uses dependent floating-point XOR operations to prevent
early retirement and fill the FP issue queue to specified pressure levels.
"""

import sys
import subprocess
from multiprocessing import Pool, cpu_count

# Number of base operations already in the template (movq instruction)
base_op_nums = 1

# Assembly template for FP issue queue stress test
# Uses a pointer chasing load followed by dependent FP operations
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
goto MainLoop;
"""


def filler(num_ops, num_nops):
    """
    Generate filling instructions to occupy floating-point issue queue slots.
    
    Args:
        num_ops: Number of FP XOR operations to insert
        num_nops: Number of NOP-equivalent integer xor operations to fill ROB
    
    Returns:
        Complete assembly code with filling instructions
    """
    block = f"""\
    asm volatile("movq %r13, %xmm0");
    asm volatile(".rept({num_ops-1})");
    asm volatile("xorpd %xmm0, %xmm1");
    asm volatile(".endr");
    asm volatile(".rept({num_nops})");
    asm volatile("xorq %r8, %r8");
    asm volatile(".endr");
"""    
    return base.replace("//filling instructions", block)


def generator(args):
    """
    Generate and compile an injector binary for specific operation count.
    
    Args:
        args: Tuple of (code_gen_dir, bin_dir, num_ops, num_nops)
    
    Returns:
        Tuple of (return_code, stderr, num_ops)
    """
    template_file = f"injector_templates/queue_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_gen_dir, bin_dir, num_ops, num_nops = args    
    code_name = f"{code_gen_dir}/fp_isq.{num_ops}.cpp"
    bin_name = f"{bin_dir}/fp_isq.{num_ops}.injector"
    code = template.replace("//Insert point", filler(num_ops, num_nops))

    with open(code_name, "w") as code_file:
        code_file.write(code)
    
    # Compile with g++ and link against libpfm4
    result = subprocess.run(["g++", "-o", bin_name, code_name, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr, num_ops
    

if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    rob_size = int(sys.argv[4])

    # Generate injectors in parallel
    # num_nops = ROB size - (num_ops + base operations) to fill remaining ROB slots
    with Pool(processes=cpu_count()) as pool:
        for returncode, stderr, num_ops in pool.imap(generator, [(code_gen_dir, bin_dir, num_ops, rob_size-(num_ops+base_op_nums)) for num_ops in sample_points]):
            if returncode != 0:
                print(f"[ERROR] op={num_ops}, returncode={returncode}")
                print(stderr)
    