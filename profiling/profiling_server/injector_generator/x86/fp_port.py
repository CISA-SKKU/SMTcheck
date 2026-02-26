import sys
import subprocess

block = """\
MainLoop:
    asm volatile ("addps %xmm0, %xmm1");
    asm volatile ("addps %xmm0, %xmm2");
    asm volatile ("addps %xmm0, %xmm3");
    asm volatile ("addps %xmm0, %xmm4");
    asm volatile ("addps %xmm0, %xmm5");
    asm volatile ("addps %xmm0, %xmm6");
goto MainLoop;
"""

def generator(code_gen_dir, bin_dir):
    template_file = f"injector_templates/port_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_name = f"{code_gen_dir}/fp_port.0.cpp"
    bin_name = f"{bin_dir}/fp_port.0.injector"
    code = template.replace("//Insert point", block)

    with open(code_name, "w") as code_file:
        code_file.write(code)
    
    result = subprocess.run(["g++", "-o", bin_name, code_name, "-lpfm"], capture_output=True, text=True)
    return result.returncode, result.stderr
    
if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    rob_size = int(sys.argv[4])

    returncode, stderr = generator(code_gen_dir, bin_dir)

    if returncode != 0:
        print(f"[ERROR] returncode={returncode}")
        print(stderr)
