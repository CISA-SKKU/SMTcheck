"""
Load Issue Queue (Load ISQ) Injector Generator

This module generates injector programs that create contention on the Load Issue Queue.
The load issue queue (scheduler) holds pending memory load operations waiting for
execution resources or memory operands.

Contention Model:
    - Load ISQ has limited entries (e.g., ~72 entries on Skylake)
    - Long-latency loads (cache misses) occupy entries longer
    - Filling the queue with dependent loads blocks new load scheduling
    - Uses pointer chasing to create serialized memory dependencies
    
Generated Assembly Pattern:
    - Uses r13 as pointer-chasing chain (serializes loads)
    - Uses r14 for additional memory access
    - Fills remaining ROB with XOR operations to control timing
    - LFENCE ensures memory ordering and prevents speculation
    - .rept directive replicates load operations to occupy queue entries

The number of load operations controls pressure level:
    - More loads = higher pressure (more entries consumed)
    - NOPs fill remaining space to maintain consistent ROB usage

Command Line Arguments:
    code_gen_dir: Directory to write generated C++ source files
    bin_dir: Directory to write compiled injector binaries
    sample_points: Comma-separated list of operation counts to generate
    rob_size: Reorder Buffer size for calculating NOP padding
"""

import sys
import subprocess
from multiprocessing import Pool, cpu_count

# Number of operations in the base code (just the pointer-chasing movq)
base_op_nums = 1 # movq (%r13), %r13

# Base assembly template for load queue contention
# Uses pointer chasing to create serialized memory load dependencies
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


def filler(num_ops, num_nops):
    """
    Generate filling instructions for the load queue.
    
    Creates a block of memory loads that occupy load issue queue entries,
    with NOP operations filling the remaining ROB capacity.
    
    Args:
        num_ops: Number of memory load operations to generate
        num_nops: Number of XOR operations for padding to fill ROB
        
    Returns:
        Assembly block with load operations and NOP padding
    """
    block = f"""\
    asm volatile("xorq %r13, %r15");
    asm volatile("xorq %r13, %r15");
    asm volatile(".rept({num_ops})");
    asm volatile("movq (%r15), %r8");
    asm volatile(".endr");
    asm volatile(".rept({num_nops})");
    asm volatile("xorq %r8, %r8");
    asm volatile(".endr");
"""
    return base.replace("//filling instructions", block)


def generator(args):
    """
    Generate and compile a single load ISQ injector binary.
    
    Args:
        args: Tuple of (code_gen_dir, bin_dir, num_ops, num_nops)
        
    Returns:
        Tuple of (return_code, stderr_output, num_ops) from compilation
    """
    template_file = f"injector_templates/queue_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_gen_dir, bin_dir, num_ops, num_nops = args    
    code_name = f"{code_gen_dir}/load_isq.{num_ops}.cpp"
    bin_name = f"{bin_dir}/load_isq.{num_ops}.injector"
    code = template.replace("//Insert point", filler(num_ops, num_nops))

    with open(code_name, "w") as code_file:
        code_file.write(code)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr, num_ops

    
if __name__ == "__main__":
    # Parse command line arguments
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    rob_size = int(sys.argv[4])

    # Generate injectors in parallel for each sample point
    # NOP count = ROB size - (load ops + base ops) to fill remaining capacity
    with Pool(processes=cpu_count()) as pool:
        for returncode, stderr, num_ops in pool.imap(generator, [(code_gen_dir, bin_dir, num_ops, rob_size-(num_ops+base_op_nums)) for num_ops in sample_points]):
            if returncode != 0:
                print(f"[ERROR] op={num_ops}, returncode={returncode}")
                print(stderr)