# Profiling System

The profiling system measures workload characteristics by running target workloads alongside injector programs that create controlled contention.

## Overview

For each workload and resource, the system measures:

- **Sensitivity**: How much the workload slows down under contention
- **Intensity**: How much contention the workload creates for others
- **Usage**: How much of the resource the workload consumes
- **Base Slowdown**: Baseline slowdown with minimal contention

These characteristics are then used to train a prediction model.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Profiling Server                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────┐    ┌───────────────┐    ┌───────────────────┐    │
│  │  TCP      │───►│  Job Queue    │───►│  Core Scheduler   │    │
│  │  Listener │    │  Manager      │    │  (Multi-core)     │    │
│  └───────────┘    └───────────────┘    └───────────────────┘    │
│                          │                      │               │
│                          ▼                      ▼               │
│                   ┌─────────────┐        ┌───────────────┐      │
│                   │  Injector   │        │  Performance  │      │
│                   │  Executor   │        │  Counters     │      │
│                   └─────────────┘        └───────────────┘      │
│                          │                      │               │
│                          ▼                      ▼               │
│                   ┌─────────────────────────────────────┐       │
│                   │           MongoDB Storage           │       │
│                   └─────────────────────────────────────┘       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
profiling/
├── profiling_server/           # Main profiling server
│   ├── run_profile_server.py   # TCP server entry point
│   ├── injector_generator/     # Injector generation
│   │   ├── injector_generator.py
│   │   └── x86/                # x86-specific generators
│   ├── injector_templates/     # C++ templates
│   ├── injector/               # Compiled injectors (generated)
│   ├── code/                   # Generated source (generated)
│   ├── target_workload_runners/ # Workload execution scripts
│   ├── profile_results/        # Raw measurement results
│   └── tools/
│       ├── config.py           # Server configuration
│       ├── machine_data.py     # CPU specifications
│       ├── global_variable_generator.py
│       ├── DBManager.py        # MongoDB interface
│       ├── perf_counter.py     # Performance counter API
│       ├── measure_injector_single.py  # Injector baseline measurement
│       └── measure_combination.py      # Workload co-run measurement
│
└── live_server/                # Model training
    ├── generate_prediction_model.py
    ├── send_profiling_request_for_testing.py  # Test client
    └── tools/
       ├── calculate_workload_characteristics.py
       ├── global_variable_generator.py
       └── machine_data.py

```

## Configuration

### Server Configuration

Edit `profiling/profiling_server/tools/config.py`:

```python
# Network settings
HOST = "192.168.0.20"                      # Server bind address
PORT = 8080                                 # Server listen port
DB_SERVER = "mongodb://192.168.0.13:27017" # MongoDB connection

# Node identification
NODE_NAME = "intel-gen11"                   # Unique machine ID

# Profiling parameters
MAXIMUM_UTIL = 0.5      # Max CPU utilization (50%)
WARMUP_COUNT = 6        # Warmup iterations before measurement
SAMPLING_TIME = 10      # Measurement duration (seconds)
```

### Machine Specifications

Edit `profiling/profiling_server/tools/machine_data.py`:

```python
# Resource categories
SEQUENTIAL_TYPE = ["int_isq", "fp_isq", "load_isq", "uop_cache"]
PARALLEL_TYPE = ["l1_dcache", "l2_cache", "l1_dtlb", "l3_cache"]
PORT_TYPE = ["int_port", "fp_port"]

# Features to profile
TARGET_FEATURE = ['int_port', 'int_isq', 'fp_port', 'load_isq', 
                  'l1_dcache', 'l2_cache', "l1_dtlb"]

# Resource sizes (entries/lines)
SIZE = {
    "int_isq":     75,
    "fp_isq":      75,
    "load_isq":    46,
    "rob":         352,
    "l1_dcache":   64 * 12,
    "l2_cache":    1024 * 8,
    # ... add your CPU's specifications
}

# Minimum reserved entries
WATERMARK = {
    "int_isq":     6,
    "fp_isq":      6,
    "load_isq":    8,
    # ...
}
```

## Running the Profiling Server

### Step 1: Generate Injectors

```bash
# Example: generate injectors for an Intel Gen11 node (x86 is the default ISA)
cd profiling/profiling_server
python3 setup.py --node_name intel-gen11
```

This generates injector binaries for each resource and pressure level:

```
injector/
├── int_isq/
│   ├── int_isq.1.injector      # Low pressure
│   ├── int_isq.55.injector     # Medium pressure
│   └── int_isq.69.injector     # High pressure
├── l1_dcache/
│   ├── l1_dcache.1.injector    # Low contention
│   └── l1_dcache.4.injector    # High contention
└── ...
```

### Step 2: Create Workload Runners

Create a runner script for each workload in `target_workload_runners/`.
Each runner must handle `SIGTERM`/`SIGINT` signals for proper cleanup, because the profiling server terminates workloads between measurement phases.

```python
# target_workload_runners/workload_0.py
import subprocess
import signal
import sys
import os
import time

proc = None

def cleanup():
    global proc
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

def signal_handler(sig, frame):
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

proc = subprocess.Popen(["./my_benchmark", "--input", "data.txt"], start_new_session=True)
proc.wait()
```

### Step 3: Start MongoDB

```bash
sudo systemctl start mongodb
```

### Step 4: Start the Server

```bash
cd profiling/profiling_server
python3 run_profile_server.py
```

### Step 5: Request Profiling

From a client (can be the same machine):

```python
import socket

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(("192.168.0.20", 8080))

# Send job ID to profile
job_id = 0
client.sendall(str(job_id).encode())

# Wait for completion
response = client.recv(4096)
print(f"Profiling complete: {response.decode()}")

client.close()
```

## Profiling Process

### For Each Workload

1. **Warmup Phase**: Run the workload alone for `WARMUP_COUNT` iterations (the IPC from the final warmup iteration is saved as the solo/single baseline IPC)
2. **L3 Profiling**: Run L3 cache injector on all sibling threads to measure L3 scaling factor
3. **Injector Sweep**: For each resource and pressure level (iterating through `injector_exec_dir.txt` entries):
   - Run injector on SMT sibling thread
   - Measure workload IPC under contention (stored as `run_type="workload"`)
   - Measure injector IPC (stored as `run_type="injector"`)
4. **Store Results**: Save all measurements to MongoDB via upsert

### Pressure Levels

| Resource Type | Levels |
|---------------|--------|
| Queue | LOW, MEDIUM, HIGH |
| Cache | LOW, HIGH |
| Port | HIGH |

### Key Measurement Modules

The profiling pipeline uses two separate measurement modules, called from `setup.py` during initial setup:

#### `measure_injector_single.py` — Injector Baseline Measurement

Measures the IPC of each injector binary itself, **not** the target workload. This establishes baseline IPC values for injectors that are later used to calculate workload **intensity** (how much a workload degrades an injector's IPC).

For each injector in `injector_exec_dir.txt`:

1. **Solo run** (`global_jobid = -1`): Run the injector alone on one core, measure its IPC
2. **Co-located run** (parallel-type only, `global_jobid = -2/-3`): Run the injector on one core while a low/high contention injector runs on the sibling core, measure IPC degradation

Results are stored in MongoDB with `run_type="injector"` and negative `global_jobid` values.

#### `measure_combination.py` — Workload Co-run Measurement

Measures the actual IPC of workloads when co-running with other workloads on SMT sibling cores. This provides ground-truth data for training the prediction model.

Requires `TRAINING_JOB_IDS` and `MULTI_THREADED_WORKLOADS` to be configured in `global_variable_generator.py`.

For each workload in `TRAINING_JOB_IDS`:

1. **Alone measurement**: Run the workload solo, record its baseline IPC as `"single"`
2. **Self co-run**: Run two copies of the same workload on sibling cores, record average IPC
3. **Pairwise co-run**: For every pair `(A, B)` in `TRAINING_JOB_IDS`, run A and B on sibling cores simultaneously and record each workload's IPC

Multi-threaded workloads (`MULTI_THREADED_WORKLOADS`) are handled differently:
- During alone/self measurement, a single process is pinned to both sibling cores (`taskset -c cid0,cid1`) instead of spawning two separate copies
- By default, two multi-threaded workloads are not paired together in `measure_combination.py`. This was originally done due to memory constraints, not a fundamental limitation. If your system has sufficient memory, you can remove this check to allow multi-threaded pairs.

Results are saved to `tools/combination_measurement_result.json` and pushed to the MongoDB `combination` collection.

## MongoDB Schema

### Measurement Collection

```json
{
    "node_name": "intel-gen11",
    "global_jobid": 0,
    "feature": "int_isq",
    "feature_id": 2,
    "feature_type": 0,
    "pressure": 2,
    "run_type": "workload",
    "IPC": 1.234,
    "timestamp": 1705312200
}
```

Field notes:
- `feature_id`: Index from `FEATURE_TO_ID` mapping
- `feature_type`: `0` = queue, `1` = cache, `2` = port
- `feature`: Resource name (e.g., `"int_isq"`). Solo baseline IPC is stored with `feature="single"`
- `run_type`: `"workload"` or `"injector"`
- `timestamp`: Unix epoch integer

### Combination Collection

```json
{
    "node_name": "intel-gen11",
    "data": {
        "0": {
            "single": 1.5,
            "1": 1.2,
            "2": 1.1
        },
        "1": {
            "single": 2.0,
            "0": 1.8,
            "2": 1.6
        }
    }
}
```

## Training Prediction Model

### Overview

The prediction model uses linear regression with non-negative coefficients to estimate workload slowdown:

```
slowdown = intercept + c₀ × min_base_slowdown + Σᵢ (cᵢ × sensitivity_base_i × intensity_col_i × activation_i)
```

Where:
- `min_base_slowdown`: Minimum `base_slowdown` across all features for the base workload
- `sensitivity_base_i`: Base workload's sensitivity to resource i
- `intensity_col_i`: Co-located workload's intensity on resource i
- `activation_i`: Contention activation function for resource i
  - Sequential-type: $\max(0, U_A + U_B - 1)$ — contention only when combined usage exceeds capacity
  - Parallel-type: $U_A \cdot U_B \cdot (U_A + U_B) / 2$ — gradual superlinear contention

### Running Training

```bash
cd profiling/live_server
python3 generate_prediction_model.py
```

### Model Output

The trained model is saved to `profiling/live_server/outputs/prediction_model.json`:

```json
{
    "feature_list": ["base", "int_port", "int_isq", "fp_port", ...],
    "coefficients": [0.1234, 0.2345, 0.3456, ...],
    "intercept": 0.0123
}
```

## Characteristic Calculation

### Sequential-Type Resources

For sequential-type resources (int_isq, load_isq, etc.):

```python
# Intensity: How much workload slows injector
injector_solo_ipc = get_injector_ipc("single", LOW)
injector_corun_ipc = get_measured_ipc(LOW, INJECTOR)
intensity = 1 - (injector_corun_ipc / injector_solo_ipc)

# Base slowdown: Slowdown even under minimal contention
workload_solo_ipc = get_workload_solo_ipc()
workload_low_ipc = get_measured_ipc(LOW, WORKLOAD)
base_slowdown = 1 - (workload_low_ipc / workload_solo_ipc)

# Sensitivity: IPC degradation from low to high pressure
workload_high_ipc = get_measured_ipc(HIGH, WORKLOAD)
sensitivity = 1 - (workload_high_ipc / workload_low_ipc)

# Usage: Linear interpolation from medium→high IPC to find drop point
# Fit a line through (medium_pressure, medium_ipc) and (high_pressure, high_ipc)
# Solve for the pressure where IPC equals low_ipc (the "cliff" point)
workload_medium_ipc = get_measured_ipc(MEDIUM, WORKLOAD)
line = LinearEquation((medium_point, workload_medium_ipc), (high_point, workload_high_ipc))
drop_point = line.solve_for_x(workload_low_ipc)
usage = max((resource_size - drop_point) / resource_size, 0)  # if sensitivity > threshold
```

### Parallel-Type Resources

For parallel-type resources (l1_dcache, l2_cache, etc.):

```python
# Base slowdown
workload_solo_ipc = get_workload_solo_ipc()
workload_low_ipc = get_measured_ipc(LOW, WORKLOAD)
base_slowdown = 1 - (workload_low_ipc / workload_solo_ipc)

# Sensitivity: IPC degradation from low to high contention
workload_high_ipc = get_measured_ipc(HIGH, WORKLOAD)
sensitivity = 1 - (workload_high_ipc / workload_low_ipc)

# Usage: Compare injector IPC when co-running vs high-contention baseline
injector_max_ipc = get_injector_ipc("high", LOW)
injector_min_ipc = get_injector_ipc("high", HIGH)
injector_current_ipc = get_measured_ipc(HIGH, INJECTOR)
usage = (injector_max_ipc - injector_current_ipc) / (injector_max_ipc - injector_min_ipc)
```

## Injector Architecture

### Generator Scripts

Each injector generator creates contention-inducing code:

```python
# injector_generator/x86/int_isq.py (simplified)
def filler(num_ops, num_nops):
    """Generate integer operations to fill issue queue"""
    block = f'''
    asm volatile(".rept({num_ops})");
    asm volatile("addq %r13, %r15");
    asm volatile(".endr");
    asm volatile(".rept({num_nops})");
    asm volatile("xorq %r8, %r8");
    asm volatile(".endr");
'''
    return base.replace("//filling instructions", block)
```

> The `filler()` function inserts stress instructions into a `base` assembly template that contains
> the main loop structure (pointer chasing + lfence). `num_nops` pads the remaining ROB capacity
> with dummy xor operations to keep total ROB occupancy constant across pressure levels.

### Pressure Control

- **Queue resources**: Number of outstanding operations
- **Cache resources**: Number of conflicting cache lines
- **Port resources**: Maximum utilization of execution ports

## Adding New Resources

See [Extending SMTcheck](extending.md#adding-new-profiling-resources) for detailed instructions.

## Troubleshooting

### Server Won't Start

```bash
# Check if port is in use
netstat -tlpn | grep 8080

# Check MongoDB connection
mongo --host 192.168.0.13
```

### No Profiling Data

```bash
# Check MongoDB collections
mongo profile_data
> db.measurement.find().limit(5)
```

### Incorrect IPC Measurements

- Ensure workload runs long enough (> 10 seconds)
- Check for warmup completion
- Verify injector is running on correct core

### Injector Not Creating Contention

- Verify SMT sibling cores are correct
- Check injector binary exists and runs
- Ensure taskset is working properly

```bash
# Manual verification
taskset -c 1 ./injector/int_isq/int_isq.69.injector 0 &
taskset -c 0 ./your_workload
```
