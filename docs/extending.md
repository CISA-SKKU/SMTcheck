# Extending SMTcheck

This guide explains how to add support for new microarchitectural resources to the SMTcheck framework.

## Overview

SMTcheck is designed to be extensible. You can add:

1. **New diagnostic generators** - Measure new resource behaviors
2. **New injector generators** - Profile workloads against new resources
3. **New ISA support** - Port to different architectures

## Adding New Diagnostics

### Step 1: Create Generator Script

Create a new file at `diag/diag_generator/x86/{resource}.py`:

```python
"""
{Resource Name} Diagnostic Generator

Generates diagnostic programs that stress the {resource description}.
"""

import sys
import subprocess
from multiprocessing import Pool, cpu_count

# Assembly template for the main loop
base = """\
    asm volatile(
    "movq %[RandomArray0], %%r13"
    :
    : [RandomArray0] "m" (arr0)
    : "%r13" 
    );
    asm volatile("xorq %r12, %r12");
MainLoop:
    asm volatile("movq (%r13), %r13");
//filling instructions
    asm volatile("lfence":::"memory");
goto MainLoop;
"""


def filler(num_ops):
    """
    Generate filling instructions to stress the target resource.
    
    Args:
        num_ops: Number of operations to insert
    
    Returns:
        Complete assembly code with filling instructions
    """
    # Replace with your resource-specific assembly
    block = f'''
    asm volatile(".rept({num_ops})");
    asm volatile("YOUR_INSTRUCTION_HERE");
    asm volatile(".endr");
'''
    return base.replace("//filling instructions", block)


def generator(args):
    """
    Generate and compile a diagnostic binary.
    
    Args:
        args: Tuple of (code_gen_dir, bin_dir, num_ops)
    
    Returns:
        Tuple of (return_code, stderr, num_ops)
    """
    code_gen_dir, bin_dir, num_ops = args
    
    # Choose appropriate template
    template_file = "templates/queue_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()
    
    # Generate code
    code_name = f"{code_gen_dir}/{resource}.{num_ops}.cpp"
    bin_name = f"{bin_dir}/{resource}.{num_ops}.diag"
    code = template.replace("//Insert point", filler(num_ops))
    
    with open(code_name, "w") as f:
        f.write(code)
    
    # Compile
    result = subprocess.run(
        ["g++", "-o", bin_name, code_name, "-lpfm"],
        capture_output=True,
        text=True
    )
    
    return result.returncode, result.stderr, num_ops


if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    
    # Define operation counts to test
    num_ops_list = list(range(1, 101))
    
    # Parallel compilation
    with Pool(processes=cpu_count()) as pool:
        args = [(code_gen_dir, bin_dir, n) for n in num_ops_list]
        for returncode, stderr, num_ops in pool.imap(generator, args):
            if returncode != 0:
                print(f"[ERROR] op={num_ops}: {stderr}")
```

### Step 2: Create Runner Script

Create `diag/run/scripts/{resource}.py`:

```python
"""
{Resource Name} Diagnostic Runner

Runs diagnostics and measures IPC with and without SMT contention.
"""

import subprocess
import glob
import sys

if __name__ == "__main__":
    bin_dir = sys.argv[1]
    output_dir = sys.argv[2]
    coreids = sys.argv[3].split(",")

    # Sort diagnostics by operation count
    diag_list = sorted(
        glob.glob(f"{bin_dir}/*.diag"),
        key=lambda x: int(x.split(".")[-2])
    )
    
    dummy_process = None
    smt_mode = "wo_smt"
    
    # Start dummy process on sibling for SMT mode
    if len(coreids) == 2:
        smt_mode = "w_smt"
        dummy_process = subprocess.Popen(
            ["taskset", "-c", coreids[1], diag_list[0]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Run each diagnostic
    for diag in diag_list:
        print(f"Running [{smt_mode}]: {diag}")
        
        result = subprocess.run(
            ["timeout", "-s", "SIGINT", "1s", 
             "taskset", "-c", coreids[0], diag],
            capture_output=True,
            text=True,
        )

        if result.returncode == 124:  # Timeout (expected)
            output_file = diag.split('/')[-1].replace('.diag', '.out')
            with open(f"{output_dir}/{output_file}", "w") as f:
                f.write(result.stdout)
        else:
            print(f"Failed: {result.returncode}")

    # Cleanup
    if dummy_process:
        dummy_process.terminate()
        dummy_process.wait()
```

### Step 3: Test the New Diagnostic

```bash
cd diag
python3 main.py --target_resource {resource} --isa x86
```

## Adding New Profiling Resources

### Step 1: Create Injector Generator

Create `profiling/profiling_server/injector_generator/x86/{resource}.py`:

The injector structure differs by resource type. Below is a **sequential-type** example (e.g., `load_isq`).
For parallel-type and port-type, see [Resource Type Guidelines](#resource-type-guidelines).

```python
"""
{Resource Name} Injector Generator (Sequential-Type)

Creates injector programs that stress {resource} for profiling.
"""

import sys
import subprocess
from multiprocessing import Pool, cpu_count

# Number of operations already in the base loop (counted toward ROB occupancy)
base_op_nums = 1  # e.g., the pointer-chasing movq

# Assembly template — pointer chasing with filling + NOP padding
base = """\
    asm volatile(
    "movq %[RandomArray0], %%r13"
    :
    : [RandomArray0] "m" (arr0)
    : "%r13" 
    );
    asm volatile("xorq %r12, %r12");
MainLoop:
    asm volatile("movq (%r13), %r13");
//filling instructions
    asm volatile("lfence":::"memory");
goto MainLoop;
"""


def filler(num_ops, num_nops):
    """
    Generate filling instructions + NOP padding.
    
    Args:
        num_ops: Number of stress operations (occupies queue entries)
        num_nops: Number of NOP-equivalent ops to fill remaining ROB capacity
    
    Returns:
        Assembly code string
    """
    block = f'''
    asm volatile(".rept({num_ops})");
    asm volatile("YOUR_STRESS_INSTRUCTION");
    asm volatile(".endr");
    asm volatile(".rept({num_nops})");
    asm volatile("xorq %r8, %r8");
    asm volatile(".endr");
'''
    return base.replace("//filling instructions", block)


def generator(args):
    """Generate and compile injector binary."""
    code_gen_dir, bin_dir, num_ops, num_nops = args
    
    template_file = "injector_templates/queue_type.cpp"
    with open(template_file, "r") as f:
        template = f.read()
    
    code_name = f"{code_gen_dir}/{resource}.{num_ops}.cpp"
    bin_name = f"{bin_dir}/{resource}.{num_ops}.injector"
    code = template.replace("//Insert point", filler(num_ops, num_nops))
    
    with open(code_name, "w") as f:
        f.write(code)
    
    result = subprocess.run(
        ["g++", "-o", bin_name, code_name, "-lpfm"],
        capture_output=True,
        text=True
    )
    
    return result.returncode, result.stderr, num_ops


if __name__ == "__main__":
    code_gen_dir = sys.argv[1]
    bin_dir = sys.argv[2]
    sample_points = list(map(int, sys.argv[3].split(",")))
    rob_size = int(sys.argv[4])  # extra_data = effective ROB size for sequential-type

    # NOP count = ROB size - (stress ops + base ops) to keep total ROB occupancy constant
    with Pool(processes=cpu_count()) as pool:
        args = [(code_gen_dir, bin_dir, n, rob_size - (n + base_op_nums)) for n in sample_points]
        for returncode, stderr, num_ops in pool.imap(generator, args):
            if returncode != 0:
                print(f"[ERROR] op={num_ops}: {stderr}")
```

> **Key difference from diagnostics**: Injector generators receive 4 command-line arguments
> (`code_dir`, `injector_dir`, `sample_points`, `extra_data`) from `injector_generator.py`.
> For sequential-type, `extra_data` is the effective ROB size; for parallel-type, it is the cache unit size.
> The NOP padding ensures constant ROB occupancy across different stress levels.

### Step 2: Update Machine Data

Edit `profiling/profiling_server/tools/machine_data.py`:

```python
# Add to appropriate category
SEQUENTIAL_TYPE = ["int_isq", "fp_isq", "load_isq", "uop_cache", "{resource}"]
# OR
PARALLEL_TYPE = ["l1_dcache", "l2_cache", "l1_dtlb", "l3_cache", "{resource}"]
# OR
PORT_TYPE = ["int_port", "fp_port", "{resource}"]

# Add to target features (order matters for indexing)
TARGET_FEATURE = ['int_port', 'int_isq', 'fp_port', 'load_isq', 'l1_dcache', 'l2_cache', 'l1_dtlb', '{resource}']

# Add specifications
WATERMARK = {
    ...,
    "{resource}": 10,  # Minimum reserved entries (0 for cache/port types)
}

SIZE = {
    ...,
    "{resource}": 100,  # Total capacity
}
```

> **Note:** `l3_cache` is automatically included alongside `TARGET_FEATURE` during injector generation
> (used for IPC scaling factor calculation), so you do not need to add it to `TARGET_FEATURE`.

### Step 3: Update Global Variable Generator

Edit `profiling/profiling_server/tools/global_variable_generator.py`:

```python
# 1. Add to FEATURE_TO_ID — append to the list inside enumerate()
FEATURE_TO_ID = {
    feature: idx for idx, feature in enumerate([
        'uop_cache', 'int_port', 'int_isq', 'fp_port', 'fp_isq',
        'load_isq', 'l1_dcache', 'l2_cache', 'l1_dtlb',
        '{resource}',  # <-- add here
    ])
}

# 2. Add to FEATURE_TYPE_TABLE — append to the hardcoded list
FEATURE_TYPE_TABLE = {
    feature: (...)
    for feature in ['uop_cache', 'int_port', 'int_isq', 'fp_port', 'fp_isq',
                    'load_isq', 'l1_dcache', 'l2_cache', 'l1_dtlb',
                    '{resource}']  # <-- add here
}

# 3. If sequential-type: add to WATERMARK_SIZE and RESOURCE_SIZE filter sets
WATERMARK_SIZE = [
    WATERMARK[feature] if feature in {"int_isq", "fp_isq", "load_isq", "uop_cache", "{resource}"} else 0
    for feature in TARGET_FEATURE
]
# (same for RESOURCE_SIZE)
```

> **Note:** `PRESSURE_POINTS` (aliased as `target_points`) is auto-computed from
> `TARGET_FEATURE`, `RESOURCE_SIZE`, and `WATERMARK_SIZE`. You do **not** need to manually
> add entries — just ensure the resource is listed in the correct category in `machine_data.py`
> and in the hardcoded sets above.

### Step 4: Register Injector Binaries

Add entries to `profiling/profiling_server/tools/injector_exec_dir.txt`.
The format is `feature,pressure_level,path`. The number of entries depends on resource type:

```
# Sequential-type: 3 levels (0=LOW, 1=MEDIUM, 2=HIGH)
{resource},0,injector/{resource}/{resource}.1.injector
{resource},1,injector/{resource}/{resource}.{medium}.injector
{resource},2,injector/{resource}/{resource}.{max}.injector

# Parallel-type: 2 levels (0=LOW, 1=HIGH)
{resource},0,injector/{resource}/{resource}.1.injector
{resource},1,injector/{resource}/{resource}.4.injector

# Port-type: 1 level (0=HIGH)
{resource},0,injector/{resource}/{resource}.0.injector
```

### Step 5: Update Score Calculation

The scoring module at `scheduling/userlevel/python/smtcheck/score_updater.py` automatically selects the correct characteristic calculator based on resource type (queue/cache/port).

> **Important:** The scheduling module has its own copies of `machine_data.py` and `global_variable_generator.py`
> at `scheduling/userlevel/python/smtcheck/`. These must be kept in sync with the profiling server copies
> when adding new resources.

## Adding New ISA Support

### Step 1: Create ISA Directory

```bash
mkdir -p diag/diag_generator/{new_isa}
mkdir -p profiling/profiling_server/injector_generator/{new_isa}
```

### Step 2: Port Generator Scripts

Copy and modify x86 scripts for the new ISA:

```python
# diag/diag_generator/{new_isa}/load_isq.py

# Replace x86 assembly with new ISA assembly
base = """\
    // New ISA specific setup
    //filling instructions
"""

def filler(num_ops):
    block = f'''
    // New ISA stress instructions
    '''
    return base.replace("//filling instructions", block)
```

### Step 3: Update Templates

Create or modify templates in `diag/templates/` for the new ISA if needed.

### Custom Templates

The provided generic templates (`queue_type.cpp`, `cache_type.cpp`) handle common patterns like pointer-chasing loops, performance counter setup, and signal-based measurement. However, you can create your own template if a resource requires specialized measurement logic.

#### When to create a custom template

- The resource needs a different memory access pattern (e.g., `uop_cache.cpp` uses a specific loop structure instead of pointer chasing)
- Vendor-specific behavior requires different perf events or setup (e.g., `l1_itlb-intel.cpp` vs `l1_itlb-amd.cpp`)
- The `//Insert point` placeholder in generic templates doesn't fit your code structure

#### How to create a custom template

1. **For diagnostics**: Add your template to `diag/templates/`:
   ```
   diag/templates/
   ├── queue_type.cpp          # Generic queue-type template
   ├── cache_type.cpp          # Generic cache-type template
   ├── uop_cache.cpp           # Custom: uop cache specific
   ├── l1_itlb-intel.cpp       # Custom: Intel-specific ITLB
   └── {your_resource}.cpp     # Your custom template
   ```

2. **For injectors**: Add your template to `profiling/profiling_server/injector_templates/`:
   ```
   profiling/profiling_server/injector_templates/
   ├── queue_type.cpp           # Generic queue-type
   ├── cache_type.cpp           # Generic cache-type
   ├── port_type.cpp            # Generic port-type
   └── {your_resource}.cpp      # Your custom template
   ```

3. **Template requirements**:
   - Include `//Insert point` as a placeholder where the generator script inserts stress instructions
   - Set up performance counters via libpfm4 (at minimum: `cycles` and `instructions`)
   - Handle `SIGINT` to report measurement results (the runner sends `timeout -s SIGINT`)
   - Initialize any memory arrays needed for the access pattern

4. **Reference your template** in the generator script:
   ```python
   # In your generator's generator() function:
   template_file = "templates/{your_resource}.cpp"  # for diagnostics
   # or
   template_file = "injector_templates/{your_resource}.cpp"  # for injectors
   ```

> **Tip**: Start by copying an existing template that is closest to your use case (`queue_type.cpp` for entry-based resources, `cache_type.cpp` for capacity-based resources) and modify it to suit your needs.

### Step 4: Test

```bash
python3 main.py --target_resource load_isq --isa {new_isa}
```

## Resource Type Guidelines

### Sequential-Type Resources

For resources with discrete entries (issue queues, buffers):

- Use `queue_type.cpp` template
- Stress by filling entries with long-latency operations
- Measure at multiple fill levels e.g., (1, 80%, 100%)
- Key assembly: Create dependencies that prevent early retirement

```asm
; Example: Fill issue queue with dependent loads
.rept N
movq (%r15), %r8
.endr
```

### Parallel-Type Resources

For cache/TLB resources:

- Use `cache_type.cpp` template
- Stress by accessing conflicting cache lines/pages
- Measure at low and high contention levels
- Key: Ensure evictions and misses

```asm
; Example: Access different cache sets
.rept N
movq offset*64(%r15), %r8
.endr
```

### Port-Type Resources

For execution port resources:

- Use `port_type.cpp` injector template (for injectors) or `queue_type.cpp` (for diagnostics)
- Create high utilization of specific ports
- Usually only a single HIGH pressure level (no `filler(num_ops)` pattern)
- Key: Saturate specific execution units with a fixed unrolled loop

```asm
; Example: Saturate integer ALU ports (fixed block, not parameterized)
MainLoop:
    addq  %r8,  %r8
    addq  %r9,  %r9
    addq %r10, %r10
    addq %r11, %r11
    addq %r12, %r12
    addq %r13, %r13
jmp MainLoop
```

## Testing New Resources

### Verify Diagnostic Generation

```bash
cd diag
python3 main.py --target_resource {resource}

# Check generated files
ls code/{resource}/
ls bin/{resource}/
```

### Verify Measurements

```bash
# Run single diagnostic manually
./bin/{resource}/{resource}.50.diag

# Check output format
cat outputs/{resource}/wo_smt/{resource}.50.out
```

### Verify Injector Integration

```bash
cd profiling/profiling_server
python3 injector_generator/injector_generator.py

# Check generated injectors
ls injector/{resource}/
```

### Verify Score Calculation

```python
from smtcheck import score_updater

score_updater.initialize()
# Check that new resource is included
print(score_updater.TARGET_FEATURE)
```
