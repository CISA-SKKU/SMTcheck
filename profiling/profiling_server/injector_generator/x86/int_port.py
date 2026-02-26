import sys
import subprocess

block = """\
MainLoop:
    asm volatile ("addq  %r8,  %r8");
    asm volatile ("addq  %r9,  %r9");
    asm volatile ("addq %r10, %r10");
    asm volatile ("addq %r11, %r11");
    asm volatile ("addq %r12, %r12");
    asm volatile ("addq %r13, %r13");
goto MainLoop;
"""

def generator(code_gen_dir, bin_dir):
    template_file = f"injector_templates/port_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()

    code_name = f"{code_gen_dir}/int_port.0.cpp"
    bin_name = f"{bin_dir}/int_port.0.injector"
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
    