"""
L1 Data TLB (DTLB) Injector Generator

This module generates injector programs that create contention on the L1 Data TLB.
The generated code accesses memory locations spaced 4KB apart to force TLB misses
when the number of unique page accesses exceeds the TLB capacity.

Contention Model:
    - L1 DTLB typically has limited entries (e.g., 64 entries on Intel)
    - Accessing more unique 4KB pages than TLB entries causes TLB thrashing
    - Stride of 4KB (2^12 bytes) ensures each access hits a different page
    
Generated Assembly Pattern:
    - Uses r8-r11 registers to hold base pointers (up to 4 concurrent pointers)
    - Left-shifts index by 12 bits (4KB stride) to get page offset
    - Accesses unique pages in a round-robin pattern
    - Optional pause instruction for reduced pressure

Command Line Arguments:
    code_gen_dir: Directory to write generated C++ source files
    bin_dir: Directory to write compiled injector binaries
    sample_points: Comma-separated list of register counts to generate
    num_entries: Number of unique TLB entries to touch
"""

import os
import sys

# Base assembly template: loads 4 pointers into registers and initializes loop
base = """\
    asm volatile(
    "movq %[ptr0], %%r8"
    :
    : [ptr0] "m" (ptr_arr[0])
    : "%r8" 
    );
    asm volatile(
    "movq %[ptr1], %%r9"
    :
    : [ptr1] "m" (ptr_arr[1])
    : "%r9" 
    );
    asm volatile(
    "movq %[ptr2], %%r10"
    :
    : [ptr2] "m" (ptr_arr[2])
    : "%r10" 
    );
    asm volatile(
    "movq %[ptr3], %%r11"
    :
    : [ptr3] "m" (ptr_arr[3])
    : "%r11" 
    );

    asm volatile ("xorq %rdi, %rdi");
    asm volatile ("xorq %rax, %rax");
    
    asm volatile ("loop:");
    asm volatile ("movq %rdi, %rsi");
    asm volatile ("shlq $12, %rsi");
"""

# Memory access instructions using different base registers
# Each register points to a different region of the buffer
cache_access_line = [
f'''\
    asm volatile ("movq (%r8, %rsi, 1), %rdx");
''',
'''\
    asm volatile ("movq (%r9, %rsi, 1), %rdx");
''',
'''\
    asm volatile ("movq (%r10, %rsi, 1), %rdx");
''',
'''\
    asm volatile ("movq (%r11, %rsi, 1), %rdx");
''',
]

# Pause instruction block for reducing contention pressure
pause_block = '''\
    asm volatile("pause");
'''


def get_boundary_condition(num_entries, need_pause):
    """
    Generate the loop boundary condition and jump assembly.
    
    Args:
        num_entries: Number of unique TLB entries to cycle through
        need_pause: If True, insert pause instruction for reduced pressure
        
    Returns:
        Assembly string for incrementing index, checking bounds, and looping
    """
    boundary_condition = f'''\
    asm volatile("addq $1, %rdi");
    asm volatile("cmp ${(num_entries)}, %rdi");
    asm volatile("cmovz %rax, %rdi");
'''
    if need_pause:
        return boundary_condition + pause_block  + '\n\tasm volatile("jmp loop");\n'
    else:
        return boundary_condition + '\n\tasm volatile("jmp loop");\n'


def gen_code(template, num_registers, num_entries, need_pause):
    """
    Generate complete C++ source code for an L1 DTLB injector.
    
    Args:
        template: Base C++ template with "// Insert point" marker
        num_registers: Number of base pointer registers to use (1-4)
        num_entries: Number of unique pages to access
        need_pause: If True, insert pause for reduced pressure
        
    Returns:
        Complete C++ source code string
    """
    code = template.replace("// Insert point", 
                             "\n".join([base, "".join([cache_access_line[i] for i in range(num_registers)]), get_boundary_condition(num_entries, need_pause)]))
    return code


if __name__ == "__main__":
    # Parse command line arguments
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    num_entries = int(sys.argv[4])
    use_hugepage = 0

    # Load the cache-type template
    template_file = f"injector_templates/cache_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    # Generate injectors for each sample point (number of registers)
    for num_registers in sample_points:
        code_name = f"{code_gen_dir}/l1_dtlb.{num_registers}.cpp"
        bin_name = f"{bin_dir}/l1_dtlb.{num_registers}.injector"
        code = gen_code(template, num_registers, num_entries, False)
        with open(code_name, "w") as f:
            f.write(code)
        
        os.system(f"g++ -D USE_HUGEPAGE={use_hugepage} -D NUM_ENTRIES={num_entries} -D NUM_REGISTERS={num_registers} -D SHIFT_BITS=12 -o {bin_name} {code_name} -lpfm")
    
    # Generate special low/high pressure injectors for profiling
    for special_type in ["low", "high"]:
        code_name = f"{code_gen_dir}/l1_dtlb.{special_type}.cpp"
        bin_name = f"{bin_dir}/l1_dtlb.{special_type}.injector"
        if special_type == "low":
            # Low pressure: minimal registers with pause
            code = gen_code(template, 1, num_entries, True)
        elif special_type == "high":
            # High pressure: maximum registers without pause
            code = gen_code(template, max(sample_points), num_entries, False)
        with open(code_name, "w") as f:
            f.write(code)
        
        os.system(f"g++ -D USE_HUGEPAGE={use_hugepage} -D NUM_ENTRIES={num_entries} -D NUM_REGISTERS={num_registers} -D SHIFT_BITS=12 -o {bin_name} {code_name} -lpfm")