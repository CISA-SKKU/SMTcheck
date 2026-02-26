"""
L1 Data Cache Injector Generator

Generates injector programs that stress the L1 data cache by
accessing multiple cache ways within the same set, causing evictions.

The generated code uses pointer-based cache line accesses through
multiple registers to control the number of conflicting cache ways.
"""

import sys
import os

# Assembly template for L1 dcache stress test
# Loads 4 different pointers into registers for multi-way access
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
    asm volatile ("shlq $6, %rsi");
"""

# Cache access instruction for each register (up to 4 ways)
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

# Pause instruction for low-contention mode
pause_block = '''\
    asm volatile("pause");
'''


def get_boundary_condition(num_entries, need_pause):
    """
    Generate loop boundary condition code.
    
    Args:
        num_entries: Number of cache lines to iterate over
        need_pause: Whether to insert pause instruction (for low contention)
    
    Returns:
        Assembly code for loop continuation logic
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
    Generate complete injector code from template.
    
    Args:
        template: C++ template content
        num_registers: Number of cache ways to access (1-4)
        num_entries: Total cache lines to iterate
        need_pause: Whether to use pause instruction
    
    Returns:
        Complete C++ code as string
    """
    code = template.replace("// Insert point", 
                             "\n".join([base, "".join([cache_access_line[i] for i in range(num_registers)]), get_boundary_condition(num_entries, need_pause)]))
    return code


if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    num_entries = int(sys.argv[4])
    use_hugepage = 1  # Use hugepages for consistent TLB behavior

    template_file = f"injector_templates/cache_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()
    
    # Generate injectors for each pressure level (number of cache ways)
    for num_registers in sample_points:
        code_name = f"{code_gen_dir}/l1_dcache.{num_registers}.cpp"
        bin_name = f"{bin_dir}/l1_dcache.{num_registers}.injector"
        code = gen_code(template, num_registers, num_entries, False)
        with open(code_name, "w") as f:
            f.write(code)

        # Compile with cache configuration macros
        os.system(f"g++ -D USE_HUGEPAGE={use_hugepage} -D NUM_ENTRIES={num_entries} -D NUM_REGISTERS={num_registers} -D SHIFT_BITS=6 -o {bin_name} {code_name} -lpfm")
        
    # Generate special low/high contention injectors for baseline measurements
    for special_type in ["low", "high"]:
        code_name = f"{code_gen_dir}/l1_dcache.{special_type}.cpp"
        bin_name = f"{bin_dir}/l1_dcache.{special_type}.injector"
        if special_type == "low":
            # Low contention: single way access with pause
            code = gen_code(template, 1, num_entries, True)
        elif special_type == "high":
            # High contention: maximum way access
            code = gen_code(template, max(sample_points), num_entries, False)
        with open(code_name, "w") as f:
            f.write(code)
        os.system(f"g++ -D USE_HUGEPAGE={use_hugepage} -D NUM_ENTRIES={num_entries} -D NUM_REGISTERS={num_registers} -D SHIFT_BITS=6 -o {bin_name} {code_name} -lpfm")