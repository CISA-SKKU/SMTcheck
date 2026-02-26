# Diagnostic System

## Overview

Diagnostics extract hidden microarchitectural features of performance-critical shared resources.
By running carefully designed code patterns and observing performance behavior, diagnostics reveal:

- **Hardware configurations**: Queue sizes, cache set/way counts, port counts
- **Sharing policies**: How resources are partitioned between SMT threads (static partitioning, competitive sharing, or dynamic partitioning)

This extracted information is then used to build resource-specific Injectors for workload profiling.

### How It Works

1. **Code Generation & Compilation**: Template-based C++ code is generated with inline assembly that stresses a target resource, then compiled with libpfm4 for performance counter access (both steps are handled by each generator script)
2. **Execution**: Diagnostics run with and without SMT to measure baseline and contention IPC
3. **Feature Extraction**: Analyze performance cliffs/saturation points to infer hardware configurations
   - For queues: IPC drop point reveals queue size
   - For caches: Set/way scan reveals cache geometry
   - For SMT: Compare w_smt vs wo_smt to detect sharing policy

4. **Visualization** (optional): Use `parse_and_plot.py` to plot results for manual analysis

## Directory Structure

```
diag/
├── main.py                 # Entry point
├── parse_and_plot.py       # Result parser and plotter
├── diag_generator/
│   ├── diag_generator.py   # Generator orchestrator
│   └── x86/                # x86-specific generators
│       ├── load_isq.py     # Load issue queue
│       ├── int_isq.py      # Integer issue queue
│       ├── fp_isq.py       # Floating-point issue queue
│       ├── load_lsq.py     # Load-store queue
│       ├── rob.py          # Reorder buffer
│       ├── l1_dcache.py    # L1 data cache
│       ├── l2_cache.py     # L2 cache
│       ├── l1_dtlb.py      # L1 data TLB
│       ├── l1_icache.py    # L1 instruction cache
│       ├── uop_cache.py    # Micro-op cache
│       └── *-intel.py, *-amd.py  # Vendor-specific variants
├── run/
│   ├── runner.py           # Execution orchestrator
│   └── scripts/            # Per-resource runners (one per resource)
├── parser/                 # Output parsers per resource type
│   ├── queue_type_parser.py
│   ├── cache_type_parser.py
│   └── *.py                # Resource-specific parsers
├── templates/              # C++ code templates
│   ├── queue_type.cpp      # For queue resources (ISQs, ROB)
│   ├── cache_type.cpp      # For cache resources (caches, TLBs)
│   └── *.cpp               # Vendor-specific templates
├── bin/                    # Compiled binaries (generated)
├── code/                   # Generated source (generated)
└── outputs/                # Measurement results (generated)
```

## Running Diagnostics

### Basic Usage

```bash
cd diag
python3 main.py --target_resource <resource> --isa x86
```

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--target_resource` | Resource to test (required) | - |
| `--isa` | Instruction set architecture | `x86` |
| `--skip_diag_gen` | Skip generation if binaries exist | `0` |

### Examples

```bash
# Generate and run load issue queue diagnostics
python3 main.py --target_resource load_isq --isa x86

# Skip regeneration (use existing binaries)
python3 main.py --target_resource load_isq --skip_diag_gen 1

# Run L1 data cache diagnostics
python3 main.py --target_resource l1_dcache --isa x86
```

> **Note:** Some resources have vendor-specific variants (e.g., `l1_itlb-intel`, `l1_itlb-amd`) because Intel and AMD CPUs use different performance counter events. Use the appropriate variant for your CPU vendor instead of the generic name.

## Output Format

Results are saved to `outputs/{resource}/`:

**Queue-type resources** (e.g., `load_isq`) produce one output file per operation count:

```
outputs/load_isq/
├── wo_smt/           # Without SMT (single thread)
│   ├── load_isq.1.out
│   ├── load_isq.2.out
│   └── ...
└── w_smt/            # With SMT (sibling thread active)
    ├── load_isq.1.out
    ├── load_isq.2.out
    └── ...
```

**Cache-type resources** (e.g., `l1_dcache`) produce one output file per stride/ways combination:

```
outputs/l1_dcache/
├── wo_smt/
│   ├── l1_dcache.stride64.ways1.out
│   ├── l1_dcache.stride64.ways2.out
│   └── ...
└── w_smt/
    ├── l1_dcache.stride64.ways1.out
    └── ...
```

### Output File Contents

```
[2] Measuring instruction count for this printf
cycles: 3000000000
instructions: 4500000000
Elapsed_time: 1.000123 seconds
IPC: 1.5000
Average_Frequency: 3.0000 GHz
```

## Understanding Results

### Interpreting IPC Degradation

Compare IPC between `wo_smt` and `w_smt` runs:

```python
# Example analysis
wo_smt_ipc = 1.5    # Without SMT
w_smt_ipc = 0.9     # With SMT
degradation = 1 - (w_smt_ipc / wo_smt_ipc)  # 40% slowdown
```

### Finding Effective Resource Size

Plot IPC vs. operation count to find the "cliff" where performance drops:

```
IPC
 │
 │────────────┐
 │            │
 │            └──────────
 │                       
 └─────────────────────── Operations
              ↑
         Effective Size
```

## Generator Architecture

### Template System

Templates define the performance counter setup and main loop structure:

```cpp
// templates/queue_type.cpp (simplified)
static void diag(uint64_t* arr0, uint64_t* arr1) {
    clock_gettime(CLOCK_MONOTONIC, &start);
    // Reset and start performance counters
    for(int i=0; i<EVENT_COUNT; i++) {
        ioctl(fd_arr[i], PERF_EVENT_IOC_RESET, 0);
        ioctl(fd_arr[i], PERF_EVENT_IOC_ENABLE, 0);
    }

//Insert point  ← Generator inserts stress code here

}
```

### Generator Scripts

Each generator creates resource-specific assembly:

```python
# diag_generator/x86/load_isq.py (simplified)
# `base` contains the full main loop assembly with a "//filling instructions" placeholder
base = """...
//filling instructions
..."""

def filler(num_ops):
    """Insert load instructions into the base assembly loop"""
    block = f'''
    asm volatile(".rept({num_ops})");
    asm volatile("movq (%r15), %r8");
    asm volatile(".endr");
    '''
    return base.replace("//filling instructions", block)

def generator(args):
    code_gen_dir, bin_dir, num_ops = args
    with open("templates/queue_type.cpp", "r") as f:
        template = f.read()
    code = template.replace("//Insert point", filler(num_ops))
    # Write code to file and compile with g++ -lpfm
```

## Adding New Diagnostics

See [Extending SMTcheck](extending.md#adding-new-diagnostics) for detailed instructions.

### Quick Steps

1. Create `diag_generator/x86/{resource}.py` with:
   - `filler(num_ops)` function returning assembly
   - `generator(args)` function for compilation
   - `__main__` block for parallel generation

2. Create `run/scripts/{resource}.py` with:
   - Binary discovery logic
   - Execution loop with performance measurement
   - Output file writing

3. Run: `python3 main.py --target_resource {resource}`

## Troubleshooting

### Compilation Errors

```bash
# Missing libpfm4
sudo apt-get install libpfm4 libpfm4-dev

# Link error
g++ -o diag.bin code.cpp -lpfm
```

### No IPC Output

```bash
# Check signal handling (SIGINT is used to read counters)
timeout -s SIGINT 1s ./diag.bin
```

### Inconsistent Results

- Ensure CPU frequency scaling is disabled
- Run multiple iterations
- Check for background processes

```bash
# Disable frequency scaling
sudo cpupower frequency-set --governor performance

# Check background load
htop
```
