import os
import sys

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
pause_block = '''\
    asm volatile("pause");
'''

def get_boundary_condition(num_entries, need_pause):
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
    code = template.replace("// Insert point", 
                             "\n".join([base, "".join([cache_access_line[i] for i in range(num_registers)]), get_boundary_condition(num_entries, need_pause)]))
    return code


if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    num_entries = int(sys.argv[4])
    use_hugepage = 1

    template_file = f"injector_templates/cache_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    for num_registers in sample_points:
        code_name = f"{code_gen_dir}/l3_cache.{num_registers}.cpp"
        bin_name = f"{bin_dir}/l3_cache.{num_registers}.injector"
        code = gen_code(template, num_registers, num_entries, False)
        with open(code_name, "w") as f:
            f.write(code)
        
        os.system(f"g++ -D USE_HUGEPAGE={use_hugepage} -D NUM_ENTRIES={num_entries} -D NUM_REGISTERS={num_registers} -D SHIFT_BITS=6 -o {bin_name} {code_name} -lpfm")
        
    
    for special_type in ["low", "high"]:
        code_name = f"{code_gen_dir}/l3_cache.{special_type}.cpp"
        bin_name = f"{bin_dir}/l3_cache.{special_type}.injector"
        if special_type == "low":
            code = gen_code(template, 1, num_entries, True)
        elif special_type == "high":
            code = gen_code(template, max(sample_points), num_entries, False)
        with open(code_name, "w") as f:
            f.write(code)
        
        os.system(f"g++ -D USE_HUGEPAGE={use_hugepage} -D NUM_ENTRIES={num_entries} -D NUM_REGISTERS={num_registers} -D SHIFT_BITS=6 -o {bin_name} {code_name} -lpfm")