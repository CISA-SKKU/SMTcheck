"""
Micro-op Cache (µop Cache) Injector Generator

This module generates injector programs that create contention on the micro-op cache
(also known as Decoded ICache or DSB - Decoded Stream Buffer on Intel processors).

Contention Model:
    - The µop cache stores decoded x86 instructions to bypass the decoder
    - Organized as sets x ways (e.g., 32 sets x 8 ways on Skylake)
    - Accessing more ways than available per set causes evictions
    - Uses 32-byte aligned code blocks matching the cache window size
    
Generated Assembly Pattern:
    - Creates a grid of jump targets aligned to window size (32 bytes)
    - Jumps traverse sets and ways in a pattern to fill cache
    - Each target: xorq + jmp (minimal micro-ops per block)
    - NOP padding (.align) ensures each block fills one cache window
    
Architecture:
    - Window size (32 bytes) determines instruction block granularity
    - num_sets controls how many cache sets are touched
    - num_ways controls pressure level (more ways = more eviction pressure)

This differs from l1_dcache/l2_cache as it targets the instruction pipeline
rather than data caches.

Command Line Arguments:
    code_gen_dir: Directory to write generated assembly source files
    bin_dir: Directory to write compiled injector binaries
    sample_points: Comma-separated list of way counts to generate
    window_size,num_sets: Cache window size and number of sets (comma-separated)
"""

import sys
import random
import subprocess
from multiprocessing import Pool, cpu_count


def get_base_code(num_ways):
    """
    Generate the assembly file header with function declaration.
    
    Args:
        num_ways: Number of cache ways (used in filename metadata)
        
    Returns:
        Assembly header string with file declaration and function start
    """
    base = f"""\
    .file	"colocate.{num_ways}.s"
    .text
.globl diag_start
    .type	diag_start, @function
    .align	1024, 0x90
"""
    return base


def generator(code_gen_dir, bin_dir, window_size, num_sets, num_ways):
    """
    Generate and compile a single µop cache injector binary.
    
    Creates an assembly file with a grid of jump targets that traverse
    the specified number of cache sets and ways, then compiles it.
    
    Args:
        code_gen_dir: Directory for generated source files
        bin_dir: Directory for compiled binaries
        window_size: Byte alignment for each code block (typically 32)
        num_sets: Number of cache sets to traverse
        num_ways: Number of cache ways to fill per set
        
    Returns:
        Tuple of (return_code, stderr_output, (window_size, num_ways))
    """
    # Template for each jump target block
    # FROM_SET_WAY and TO_SET_WAY are replaced with actual coordinates
    block = f"""\
    TARGET_FROM_SET_WAY:
    xorq %rax, %rax
    jmp TARGET_TO_SET_WAY
    .align {window_size}, 0x90
"""
    # Build assembly starting with header and entry point
    base = get_base_code(num_ways)
    base += f"""\
    diag_start:
    jmp TARGET_0_0
    .align {window_size}, 0x90
"""

    # Generate jump targets for each (set, way) combination
    # Traversal order: all sets at way 0, then all sets at way 1, etc.
    # Final target jumps back to start (circular execution)
    for w in range(num_ways):
        for s in range(num_sets):
            if(w == num_ways-1 and s == num_sets-1):
                # Last target: jump back to beginning
                base += block.replace("FROM_SET_WAY", f'{s}_{w}').replace("TO_SET_WAY", f'{0}_{0}')
            elif(w+1 < num_ways):
                # Move to next way in same set
                base += block.replace("FROM_SET_WAY", f'{s}_{w}').replace("TO_SET_WAY", f'{s}_{w+1}')
            else:
                # Move to first way of next set
                base += block.replace("FROM_SET_WAY", f'{s}_{w}').replace("TO_SET_WAY", f'{(s+1)%num_sets}_{0}')

    code_name = f"{code_gen_dir}/uop_cache.{num_ways}.s"
    bin_name = f"{bin_dir}/uop_cache.{num_ways}.injector"
    template_file = f"injector_templates/uop_cache.cpp"
    
    with open(code_name, "w") as file:
        file.write(base)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, template_file, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr, (window_size, num_ways)


if __name__ == "__main__":
    # Parse command line arguments
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    window_size, num_sets = map(int, sys.argv[4].split(","))

    # Generate injectors for each way count
    for num_ways in sample_points:
        retcode, stderr, params = generator(code_gen_dir, bin_dir, window_size, num_sets, num_ways)
        if retcode != 0:
            print(f"Error generating uop_cache diag for window_size={window_size}, num_ways={num_ways}")
            print(stderr)
