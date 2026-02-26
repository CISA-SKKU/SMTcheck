# Scheduling System

The scheduling system uses trained prediction models to optimize CPU affinity for co-running workloads, minimizing SMT interference.

## Overview

The scheduling system consists of:

1. **Kernel Modules**: Monitor process runtime and IPC
2. **User-Space Components**: Calculate compatibility scores and apply affinity
3. **C++ Extension**: High-performance scheduling algorithm via pybind11

## Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                          Scheduling System                                │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│   Kernel Space                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  ┌─────────────────┐    ┌─────────────────┐                         │  │
│  │  │  IPC_monitor    │    │ runtime_monitor │                         │  │
│  │  │  (perf events)  │    │ (timer-based)   │                         │  │
│  │  └────────┬────────┘    └────────┬────────┘                         │  │
│  │           │                      │                                  │  │
│  │           ▼                      ▼                                  │  │
│  │  ┌─────────────────┐    ┌─────────────────┐                         │  │
│  │  │  Shared Memory  │    │    Netlink      │                         │  │
│  │  │  (mmap)         │    │   Messages      │                         │  │
│  │  └─────────────────┘    └─────────────────┘                         │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│           │                        │                                      │
│           ▼                        ▼                                      │
│   User Space                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐  │  │
│  │  │  c_struct.py    │    │ profile_data_   │    │ score_updater   │  │  │
│  │  │  (ctypes mmap)  │    │ loader.py       │    │ .py             │  │  │
│  │  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘  │  │
│  │           │                      │                      │           │  │
│  │           └──────────────────────┼──────────────────────┘           │  │
│  │                                  ▼                                  │  │
│  │                    ┌─────────────────────────┐                      │  │
│  │                    │    smtcheck_native      │                      │  │
│  │                    │    (C++ via pybind11)   │                      │  │
│  │                    │    - job_mapper.cpp     │                      │  │
│  │                    └─────────────────────────┘                      │  │
│  │                                  │                                  │  │
│  │                                  ▼                                  │  │
│  │                    ┌─────────────────────────┐                      │  │
│  │                    │    sched_setaffinity    │                      │  │
│  │                    │    (CPU affinity)       │                      │  │
│  │                    └─────────────────────────┘                      │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
scheduling/
├── kernel/
│   ├── Makefile
│   ├── include/
│   │   └── IPC_monitor.h       # Shared header
│   └── module/
│       ├── IPC_monitor.c       # IPC tracking module
│       └── runtime_monitor.c   # Long-running detection
│
├── userlevel/
│   ├── python/smtcheck/        # Python package
│   │   ├── __init__.py
│   │   ├── c_struct.py         # Ctypes for kernel structs
│   │   ├── profile_data_loader.py
│   │   ├── score_updater.py
│   │   ├── machine_data.py
│   │   └── global_variable_generator.py
│   ├── c/
│   │   ├── src/job_mapper.cpp  # Core scheduling algorithm
│   │   ├── include/job_mapper.h
│   │   └── pybind/
│   │       ├── bindings.cpp
│   │       └── Makefile
│   └── tools/
│       └── perf_counter.py     # Performance counter utilities
│
├── script/
│   ├── test/                   # Test scripts
│   │   ├── kernel_module_test.py
│   │   ├── data_loader_test.py
│   │   └── scheduling_test.py
│   ├── run_dummy_process.py
│   └── copy_trained_model.py
│
└── trained_model/
    └── prediction_model_*.json
```

## Kernel Modules

### IPC_monitor

Tracks IPC (Instructions Per Cycle) for registered process groups using hardware performance counters.

**Features:**
- Per-PGID cycle and instruction counting
- Shared memory for zero-copy user access
- RCU-safe slot management
- sched_switch tracepoint integration

**Device File:** `/dev/IPC_monitor`

**Shared Memory Layout:**
```c
struct ipc_shared {
    atomic_t count;
    unsigned long active_mask[64];  // Bitmap of active slots
    struct pgid_slot_user slots[4096];  // Per-PGID data (userspace-visible)
};

// Userspace-visible layout (pgid_slot_user in kernel, aligned to 16 bytes).
// The kernel-internal pgid_slot has additional fields (spinlock, reset_flag, gen)
// and is aligned to 64 bytes.
struct pgid_slot_user {
    uint32_t seq;           // Sequence for consistency
    int32_t pgid;           // Process group ID
    int32_t global_jobid;   // Application job ID
    int32_t worker_num;     // Number of workers
    uint64_t cycles;        // Total CPU cycles
    uint64_t instructions;  // Total instructions
};
```

### runtime_monitor

Detects long-running processes and triggers profiling requests.

**Features:**
- Timer-based process scanning
- Configurable runtime threshold
- Netlink notifications to userspace
- ACK-gated IPC registration

**Device File:** `/dev/runtime_monitor`

**ioctl Commands:**
```c
RTMON_IOC_ADD_PGID          // Register a PGID for monitoring
RTMON_IOC_REMOVE_PGID       // Unregister a PGID
RTMON_IOC_SET_THRESHOLD     // Set long-running threshold (seconds)
RTMON_IOC_SET_DATA_LOADER_PID  // Set userspace notification PID
RTMON_IOC_REQUEST_PROFILE   // Request profiling for a PGID
```

### Building and Loading

```bash
cd scheduling/kernel

# Build modules
make

# Load modules (requires root)
sudo insmod module/IPC_monitor.ko
sudo insmod module/runtime_monitor.ko

# Verify loading
lsmod | grep -E "IPC_monitor|runtime_monitor"

# Check device files
ls -la /dev/IPC_monitor /dev/runtime_monitor

# Unload modules
sudo rmmod runtime_monitor
sudo rmmod IPC_monitor
```

## User-Space Components

### c_struct.py

Provides ctypes bindings for reading kernel shared memory:

```python
from smtcheck.c_struct import SharedMemoryManager

# Open and map shared memory
shm = SharedMemoryManager()  # default: /dev/IPC_monitor
shm.map()

# Iterate active slots (active_slots is a @property, not a function call)
for slot in shm.active_slots:
    print(f"PGID: {slot.pgid}, IPC: {slot.instructions / slot.cycles}")

# Reset counters via ioctl
shm.reset_counters()

# Clean up
shm.close()
```

### profile_data_loader.py

Handles communication between scheduling and profiling systems:

```python
from smtcheck import profile_data_loader

# Initialize connections
profile_data_loader.initialize()

# Listen for kernel events
while True:
    pgid, job_id = profile_data_loader.netlink_listener()
    profile_data_loader.read_profile_data(job_id, pgid)
```

### score_updater.py

Calculates workload characteristics and compatibility scores:

```python
from smtcheck import score_updater

# Initialize (loads injector baselines from MongoDB)
score_updater.initialize()

# Load trained prediction model
score_updater.load_model_data(ROOT_DIR)

# Add workload for scoring
score_updater.add_workload(job_id=5)

# Update compatibility scores for all workloads
score_updater.update_score_table()

# Print the score board
score_updater.print_score_board()
```

### smtcheck_native (C++ Extension)

High-performance scheduling algorithm implementation:

```python
import smtcheck_native
from smtcheck.machine_data import sibling_core_dict

# Open shared memory (memory-mapped from IPC_monitor device)
smtcheck_native.open_mmap()

# Set up CPU sibling core topology (requires dict argument)
smtcheck_native.set_sibling_core_map(sibling_core_dict)

# Update score map (called by score_updater.update_score_table())
smtcheck_native.update_score_map(base_jobid, col_jobid, score)

# Update single IPC map entry
smtcheck_native.update_single_IPC_map(jobid, ipc_value)

# Retrieve the full score map as a Python dict
scores = smtcheck_native.get_score_map_py()

# Run scheduler (applies CPU affinity to co-running workloads)
smtcheck_native.schedule()
```

## Building the C++ Extension

```bash
cd scheduling/userlevel/c/pybind

# Build with Make
make

# Or manually with pybind11
g++ -O3 -Wall -shared -std=c++17 -fPIC \
    $(python3 -m pybind11 --includes) \
    bindings.cpp ../src/job_mapper.cpp \
    -o smtcheck_native$(python3-config --extension-suffix)
```

## Scheduling Algorithm

### Compatibility Score Calculation

For each workload pair (A, B), the predicted slowdown is:

```
feature_vector = [min_base_slowdown, contention_1, contention_2, ...]
contention_i   = sens_A_i × int_B_i × activation_i(usage_A_i, usage_B_i)

slowdown_A = intercept + Σⱼ (coefⱼ × feature_vector[j])
slowdown_B = intercept + Σⱼ (coefⱼ × feature_vector_B[j])

compat_A = scale_factor_A × (1 - slowdown_A)
compat_B = scale_factor_B × (1 - slowdown_B)
symbiotic_score = compat_A + compat_B
```

Where:
- `min_base_slowdown` is the minimum base slowdown across all resources for the base workload
- `intercept` is the linear regression model intercept
- `scale_factor = l3_cache_ipc / single_ipc` accounts for CMP-level contention (e.g., shared L3 cache) that exists regardless of SMT pairing, so workloads that suffer more from L3 contention are weighted accordingly in scheduling decisions
- `activation_i` is ReLU (sequential-type: `max(0, usage_A + usage_B - 1)`) or multiplicative (parallel-type: `usage_A × usage_B × (usage_A + usage_B) / 2`, where `usage_A × usage_B` models the collision probability and `(usage_A + usage_B) / 2` models the average usage)

Higher scores indicate better compatibility. Scores are clamped to [0, 1].

### Pair Selection Algorithm

The scheduler uses greedy selection with local search:

1. **Build candidate pairs**: Create all possible workload pairs with scores
2. **Sort by score**: Higher compatibility first
3. **Greedy selection**: Assign best pairs to physical cores
4. **Rearrangement**: Try swapping pairs to improve total score

### CPU Affinity Assignment

```cpp
// job_mapper.cpp
void set_pgid_affinity(int pgid, cpu_set_t cpu_set) {
    // Get all threads for this PGID
    auto tids = get_threads(pgid);
    
    // Set affinity for each thread
    for (int tid : tids) {
        sched_setaffinity(tid, sizeof(cpu_set_t), &cpu_set);
    }
    
    // Recursively handle child processes
    auto children = get_children(pgid);
    for (int child : children) {
        set_pgid_affinity(child, cpu_set);
    }
}
```

## Test Scripts

### kernel_module_test.py

Tests kernel module communication:

```bash
sudo python3 script/test/kernel_module_test.py
```

**Tests:**
- PGID registration with runtime_monitor
- Shared memory reading from IPC_monitor
- Performance counter collection

### data_loader_test.py

Tests netlink and MongoDB integration:

```bash
sudo python3 script/test/data_loader_test.py
```

**Tests:**
- Netlink event reception
- MongoDB query functionality
- Profile data parsing

### scheduling_test.py

Full integration test:

```bash
sudo python3 script/test/scheduling_test.py
```

**Tests:**
- Complete scheduling pipeline
- Event-driven workflow
- CPU affinity application

## Configuration

### Trained Model Deployment

```bash
# Copy model from profiling server
python3 script/copy_trained_model.py
```

This copies `profiling/live_server/outputs/prediction_model.json` to `scheduling/trained_model/prediction_model_<TIMESTAMP>.json`. It compares the source model with the latest existing model and only copies if the content differs.

### Runtime Threshold

Set the long-running process threshold (default: 3600 seconds):

```python
import os
import fcntl
import struct
from smtcheck.c_struct import RTMON_IOC_SET_THRESHOLD

fd = os.open("/dev/runtime_monitor", os.O_RDWR)
threshold = 300  # 5 minutes
fcntl.ioctl(fd, RTMON_IOC_SET_THRESHOLD, struct.pack("i", threshold))
```

## Usage Example

### Complete Workflow

```python
#!/usr/bin/env python3
"""SMT-aware scheduling example"""

import os
import sys
import threading

from smtcheck import profile_data_loader
from smtcheck import score_updater
from smtcheck.machine_data import sibling_core_dict
import smtcheck_native

def main():
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

    # Initialize components
    profile_data_loader.initialize()
    score_updater.initialize()
    smtcheck_native.open_mmap()
    smtcheck_native.set_sibling_core_map(sibling_core_dict)

    # Load trained prediction model
    score_updater.load_model_data(ROOT_DIR)

    # Main event loop
    while True:
        # Wait for kernel event (new long-running process)
        pgid, job_id = profile_data_loader.netlink_listener()

        # Request profiling if needed
        profile_data_loader.send_profiling_request(job_id)

        # Add workload and update compatibility scores
        score_updater.add_workload(job_id)
        score_updater.update_score_table()

        # Run scheduler (applies CPU affinity internally)
        smtcheck_native.schedule()

if __name__ == "__main__":
    main()
```

## Troubleshooting

### Kernel Module Won't Load

```bash
# Check kernel log
dmesg | tail -50

# Verify module dependencies
modinfo module/IPC_monitor.ko

# Check for symbol conflicts
cat /proc/kallsyms | grep ipc_monitor
```

### Shared Memory Access Failed

```bash
# Check device permissions
ls -la /dev/IPC_monitor

# Add udev rule for non-root access
echo 'KERNEL=="IPC_monitor", MODE="0666"' | sudo tee /etc/udev/rules.d/99-ipc.rules
sudo udevadm control --reload-rules
```

### Netlink Messages Not Received

```bash
# Check if runtime_monitor is loaded
lsmod | grep runtime_monitor

# Verify PID registration
cat /sys/module/runtime_monitor/parameters/data_loader_pid

# Check netlink socket
ss -xln | grep NETLINK
```

### C++ Extension Build Failed

```bash
# Install pybind11
pip3 install pybind11

# Check Python headers
python3-config --includes

# Verify compiler
g++ --version
```

### Scheduling Not Applied

```bash
# Check process affinity
taskset -p <pid>

# Verify CAP_SYS_NICE capability
getcap /usr/bin/python3

# Run with root
sudo python3 scheduling_test.py
```
