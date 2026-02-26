# Getting Started with SMTcheck

This guide covers installation, prerequisites, and getting up and running with SMTcheck.

## Prerequisites

### Hardware Requirements

- **SMT-capable processor** (e.g., Intel Hyper-Threading, AMD SMT)
  - *Note: Current implementation is for x86-64. The framework can be extended to other architectures.*
- **Performance counters**: CPU with accessible PMU (Performance Monitoring Unit)
- **Root access**: Required for perf_event and kernel module operations

### Software Requirements

```bash
# Build tools and kernel headers
sudo apt-get update
sudo apt-get install build-essential linux-headers-$(uname -r)

# Install libpfm4 (required for performance counter access)
git clone https://github.com/wcohen/libpfm4.git
cd libpfm4
make
sudo make install
cd ..

# Install Python dependencies
pip3 install pymongo psutil scikit-learn numpy pybind11
```

### Kernel Configuration

Ensure performance counters are accessible:

```bash
# Check current setting
cat /proc/sys/kernel/perf_event_paranoid

# Set to allow user-space access (requires root)
sudo sysctl -w kernel.perf_event_paranoid=-1

# Make persistent (add to /etc/sysctl.conf)
echo "kernel.perf_event_paranoid=-1" | sudo tee -a /etc/sysctl.conf
```

### Verify SMT is Enabled

```bash
# Check SMT status
cat /sys/devices/system/cpu/smt/active

# List sibling threads
cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list
```

## Installation

### Configure Machine Parameters

Update CPU-specific parameters in configuration files:

1. **For Profiling & Scheduling**: Edit `profiling/profiling_server/tools/machine_data.py`:
   - This file contains resource sizes, watermarks, and feature categories
   - The same configuration is used by the scheduling system

2. **Machine Data Configuration** (`profiling/profiling_server/tools/machine_data.py`):

```python
# Example: Intel Core i7-11700
SIZE = {
    "int_isq":     75,          # Integer issue queue entries
    "fp_isq":      75,          # Floating-point issue queue entries
    "load_isq":    46,          # Load issue queue entries
    "rob":         352,         # Reorder buffer entries
    "l1_dcache":   64 * 12,     # L1D cache lines (sets * ways)
    "l2_cache":    1024 * 8,    # L2 cache lines
    # ... add your CPU's specifications
}
```

3. **For Profiling Server**: Edit `profiling/profiling_server/tools/config.py`:

```python
HOST = "192.168.0.20"                       # Server bind address
PORT = 8080                                 # Server listen port
DB_SERVER = "mongodb://192.168.0.13:27017"  # MongoDB connection string
NODE_NAME = "intel-gen11"                   # Unique node identifier
```

### Build Kernel Modules (for Scheduling)

```bash
cd scheduling/kernel
make
sudo insmod module/IPC_monitor.ko
sudo insmod module/runtime_monitor.ko
```

### Build C++ Extension (for Scheduling)

```bash
cd scheduling/userlevel/c/pybind
make
```

## Quick Start

### 1. Run Diagnostics

Generate and run diagnostic programs to measure resource contention behavior:

```bash
cd diag

# Generate and run load issue queue diagnostics
python3 main.py --target_resource load_isq --isa x86

# Generate and run L1 data cache diagnostics
python3 main.py --target_resource l1_dcache --isa x86
```

Results are saved to `diag/outputs/{resource}/w_smt/` and `wo_smt/`.

### 2. Generate Injector Binaries

Generate injector programs for workload profiling:

```bash
cd profiling/profiling_server

# Example: generate injectors for an Intel Gen11 node (x86 is the default ISA)
python3 setup.py --node_name intel-gen11

# To specify a different ISA explicitly:
# python3 setup.py --node_name intel-gen11 --isa x86
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--node_name` | Yes | — | Unique node identifier (should match `NODE_NAME` in `tools/config.py`) |
| `--isa` | No | `x86` | Target instruction set architecture |

### 3. Start MongoDB and Profiling Server

> **Note**: The MongoDB server and the profiling server are independent services that can run on different machines. Make sure to configure the connection settings in `profiling/profiling_server/tools/config.py` before starting.

#### MongoDB Server Setup

If MongoDB is not installed on your database server, follow the [official MongoDB installation guide](https://www.mongodb.com/docs/manual/installation/) to set it up. Then start the service:

```bash
sudo systemctl start mongod
sudo systemctl enable mongod
```

#### Profiling Server Setup

On your profiling machine, start the profiling server:

```bash
cd profiling/profiling_server
python3 run_profile_server.py
```

### 4. Train Prediction Model

After profiling several workloads, you must configure the training variables in `profiling/live_server/tools/global_variable_generator.py`

```python
# Job IDs whose profiling data will be used to train the prediction model
TRAINING_JOB_IDS = [0, 1, 2, 5, 8]

# Job IDs of inherently multi-threaded workloads.
# During profiling, these are pinned to both sibling cores at once
# (taskset -c cid0,cid1) instead of spawning separate per-core copies.
# Two multi-threaded workloads are never co-run together.
MULTI_THREADED_WORKLOADS = {25, 26, 29, 30}

# All profiled job IDs (superset of TRAINING_JOB_IDS)
ALL_JOB_IDS = list(range(0, 31))
```

> **Note**: Job IDs are assigned by the profiling server and stored in MongoDB. `TRAINING_JOB_IDS` selects which workloads to train on. `MULTI_THREADED_WORKLOADS` marks workloads that already use multiple threads internally — these are given both sibling core IDs when measured alone, and are excluded from mutual co-run pairs. `ALL_JOB_IDS` should list every profiled job ID (training + evaluation).
>
> The same `TRAINING_JOB_IDS` and `MULTI_THREADED_WORKLOADS` should also be set in `profiling/profiling_server/tools/global_variable_generator.py` if you run `measure_combination` on the profiling server.

Then run the model training:

```bash
cd profiling/live_server
python3 generate_prediction_model.py
```

### 5. Run Scheduling (Optional)

```bash
# Load kernel modules
cd scheduling/kernel
sudo insmod module/IPC_monitor.ko
sudo insmod module/runtime_monitor.ko
cd ..

# Copy trained model
python3 script/copy_trained_model.py

# Run scheduling test
sudo python3 script/test/scheduling_test.py
```

## Verifying Installation

### Test Performance Counters

```bash
# Simple perf test
perf stat -e cycles,instructions sleep 1
```

### Test MongoDB Connection

```python
from pymongo import MongoClient
client = MongoClient("mongodb://localhost:27017")
print(client.list_database_names())
```

### Test Kernel Modules

```bash
# Check if modules are loaded
lsmod | grep -E "IPC_monitor|runtime_monitor"

# Check device files
ls -la /dev/IPC_monitor /dev/runtime_monitor
```

## Troubleshooting

### libpfm4 not found

```bash
# Install development package
sudo apt-get install libpfm4-dev

# Set library path if needed
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
```

### Permission denied for perf events

```bash
# Temporarily allow user access
sudo sysctl -w kernel.perf_event_paranoid=-1

# Or run as root
sudo python3 main.py --target_resource load_isq
```

### MongoDB connection refused

```bash
# Start MongoDB service
sudo systemctl start mongodb
sudo systemctl enable mongodb

# Check if running
sudo systemctl status mongodb
```

### Kernel module build fails

```bash
# Ensure kernel headers are installed
sudo apt-get install linux-headers-$(uname -r)

# Check for build errors
cd scheduling/kernel
make clean
make
```

## Next Steps

- [Learn about diagnostics](diagnostics.md) - Understand how diagnostic programs work
- [Set up profiling](profiling.md) - Profile your workloads
- [Configure scheduling](scheduling.md) - Enable SMT-aware scheduling
