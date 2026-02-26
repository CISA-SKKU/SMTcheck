"""
Reorder Buffer (ROB) Diagnostic Generator

Generates diagnostic programs that stress the reorder buffer by
creating varying numbers of in-flight instructions.

The generated code uses pointer chasing with additional zero-idiom XOR
instructions to occupy ROB entries to different levels. Each instruction
consumes a ROB entry until the load completes and instructions can retire.
"""

import sys
import subprocess
from multiprocessing import Pool, cpu_count

# Base assembly template for ROB stress
# Uses pointer chasing with zero-idiom XORs to fill the reorder buffer
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
    block = f"""\
    asm volatile(".rept({num_ops})");
    asm volatile("xorq %r15, %r15"); // zero idiom
    asm volatile(".endr");
"""
    return base.replace("//filling instructions", block)

def generator(args):
    template_file = f"templates/queue_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_gen_dir, bin_dir, num_ops = args    
    code_name = f"{code_gen_dir}/rob.{num_ops}.cpp"
    bin_name = f"{bin_dir}/rob.{num_ops}.diag"
    code = template.replace("//Insert point", filler(num_ops))

    with open(code_name, "w") as code_file:
        code_file.write(code)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr, num_ops
    
if __name__ == "__main__":
    start_num_ops = 100
    end_num_ops = 300
    
    num_ops_list = list(range(start_num_ops, end_num_ops + 1))
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]

    with Pool(processes=cpu_count()) as pool:
        for returncode, stderr, num_ops in pool.imap(generator, [(code_gen_dir, bin_dir, num_ops) for num_ops in num_ops_list]):
            if returncode != 0:
                print(f"[ERROR] op={num_ops}, returncode={returncode}")
                print(stderr)