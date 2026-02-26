import sys
import subprocess
from multiprocessing import Pool, cpu_count

base_op_nums = 1 # movq (%r13), %r13

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
    block = f"""\
    asm volatile(".rept({num_ops-1})");
    asm volatile("movq (%r15), %r8");
    asm volatile(".endr");
    asm volatile(".rept({num_nops})");
    asm volatile("xorq %r8, %r8");
    asm volatile(".endr");
"""
    return base.replace("//filling instructions", block)

def generator(args):
    template_file = f"injector_templates/queue_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_gen_dir, bin_dir, num_ops, num_nops = args    
    code_name = f"{code_gen_dir}/load_lsq.{num_ops}.cpp"
    bin_name = f"{bin_dir}/load_lsq.{num_ops}.injector"
    code = template.replace("//Insert point", filler(num_ops, num_nops))

    with open(code_name, "w") as code_file:
        code_file.write(code)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr, num_ops
    
if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    rob_size = int(sys.argv[4])

    with Pool(processes=cpu_count()) as pool:
        for returncode, stderr, num_ops in pool.imap(generator, [(code_gen_dir, bin_dir, num_ops, rob_size-(num_ops+base_op_nums)) for num_ops in sample_points]):
            if returncode != 0:
                print(f"[ERROR] op={num_ops}, returncode={returncode}")
                print(stderr)
    