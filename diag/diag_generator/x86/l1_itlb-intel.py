"""
L1 Instruction TLB Diagnostic Generator (Intel)

Generates diagnostic programs that stress the L1 instruction TLB by
creating code that spans different memory pages.

The generated assembly uses jump chains across page-aligned code blocks
to force ITLB lookups from different pages.

Parameters varied:
    - window_size: Code block alignment (page size for TLB testing)
    - num_entries: Number of pages to access

This Intel-specific version uses Intel PMU events for accurate ITLB
miss measurement.
"""

import sys
import random
import subprocess
from multiprocessing import Pool, cpu_count

def get_base_code(entry_count):
    base = f"""\
    .file	"colocate.{entry_count}.s"
    .text
.globl diag_start
    .type	diag_start, @function
    .align	1024, 0x90
"""
    return base

def generator(args):
    code_gen_dir, bin_dir, window_size, num_entries = args
    block = f"""\
    TARGET_FROM:
    xorq %rax, %rax
    jmp TARGET_TO
    .align {window_size}, 0x90
"""

    random_numbers = list(range(num_entries))
    random.shuffle(random_numbers)
    chasing_order = [0 for _ in range(num_entries)]

    for i in range(num_entries):
        chasing_order[random_numbers[i]] = random_numbers[(i+1)%num_entries]
    base = get_base_code(num_entries)
    base += f"""\
    diag_start:
    jmp TARGET_{random_numbers[0]}
    .align {window_size}, 0x90
"""

    for i in range(num_entries):
        base += block.replace("TARGET_FROM", f'TARGET_{i}').replace("TARGET_TO", f'TARGET_{chasing_order[i]}')
    base += f"""\
    jmp diag_start
    .align {window_size}, 0x90
"""

    code_name = f"{code_gen_dir}/l1_itlb-intel.{window_size}.{num_entries}.s"
    bin_name = f"{bin_dir}/l1_itlb-intel.{window_size}.{num_entries}.diag"
    template_file = f"templates/l1_itlb-intel.cpp"
    with open(code_name, "w") as file:
        file.write(base)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, template_file, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr, (window_size, num_entries)

if __name__ == "__main__":
    entire_working_set_size = (1<<19) # 512KB
    min_num_entries = 8

    parameter_list = []

    sample_point_per_window_size = 16
    for i in range(6):
        window_size = entire_working_set_size // (1<<i)
        max_num_entries = min_num_entries << i
        stride = max(1, max_num_entries // sample_point_per_window_size)
        for i in range(1, sample_point_per_window_size+1):
            num_entries = stride * i
            parameter_list.append((window_size, num_entries))
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]

    with Pool(processes=cpu_count()) as pool:
        for returncode, stderr, (window_size, num_entries) in pool.imap(generator, [(code_gen_dir, bin_dir, window_size, num_entries) for (window_size, num_entries) in parameter_list]):
            if returncode != 0:
                print(f"[ERROR] window_size={window_size}, num_entries={num_entries}, returncode={returncode}")
                print(stderr)
