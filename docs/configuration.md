# Configuration Reference

This document lists all configuration options in the SMTcheck framework.

> **Note:** All values shown in this document (IP addresses, port numbers, resource sizes, thresholds, etc.) are examples based on our test environment. You must adjust them to match your own deployment environment and CPU microarchitecture.

## Profiling Server Configuration

**File:** `profiling/profiling_server/tools/config.py`

### Network Settings

| Variable | Type | Description | Default |
|----------|------|-------------|---------|
| `HOST` | string | Server bind address | `"192.168.0.20"` |
| `PORT` | int | Server listen port | `8080` |
| `DB_SERVER` | string | MongoDB connection string | `"mongodb://192.168.0.13:27017"` |

### Node Identification

| Variable | Type | Description | Default |
|----------|------|-------------|---------|
| `NODE_NAME` | string | Unique identifier for this machine | `"intel-gen11"` |

### Profiling Parameters

| Variable | Type | Description | Default |
|----------|------|-------------|---------|
| `MAXIMUM_UTIL` | float | Maximum CPU utilization ratio (0.0-1.0) | `0.5` |
| `WARMUP_COUNT` | int | Warmup iterations before measurement | `6` |
| `SAMPLING_TIME` | int | Measurement duration in seconds | `10` |

### Example

```python
# profiling/profiling_server/tools/config.py

# Network configuration
HOST = "192.168.0.20"                      # Server bind address
PORT = 8080                                 # Server listen port
DB_SERVER = "mongodb://192.168.0.13:27017" # MongoDB connection string

# Node identification
NODE_NAME = "intel-gen11"                   # Unique identifier for this machine

# Profiling parameters
MAXIMUM_UTIL = 0.5      # Maximum CPU utilization ratio for profiling (0.0-1.0)
WARMUP_COUNT = 6        # Number of warmup iterations before measurement
SAMPLING_TIME = 10      # Duration of each measurement in seconds
```

---

## Machine Data Configuration

**File:** `profiling/profiling_server/tools/machine_data.py`

### Resource Categories

| Variable | Type | Description |
|----------|------|-------------|
| `SEQUENTIAL_TYPE` | list[str] | Resources with discrete entries |
| `PARALLEL_TYPE` | list[str] | Cache/TLB resources |
| `PORT_TYPE` | list[str] | Execution port resources |

### Target Features

| Variable | Type | Description |
|----------|------|-------------|
| `TARGET_FEATURE` | list[str] | Features to profile (order matters for indexing) |

### Profiling Parameters

| Variable | Type | Description | Default |
|----------|------|-------------|---------|
| `SAMPLING_INTERVAL` | int | Seconds between measurements | `2` |
| `UOP_CACHE_WINDOW_SIZE` | int | Uop cache window size in uops | `64` |
| `UOP_CACHE_NUM_SETS` | int | Number of uop cache sets | `64` |
| `MEDIUM_RATIO` | float | Medium pressure = MAX * MEDIUM_RATIO | `0.8` |

### Resource Sizes

`SIZE` dictionary maps resource names to their total capacity:

| Resource | Description | Example Value |
|----------|-------------|---------------|
| `int_isq` | Integer issue queue entries | `75` |
| `fp_isq` | Floating-point issue queue entries | `75` |
| `load_isq` | Load issue queue entries | `46` |
| `load_lsq` | Load-store queue entries | `128` |
| `rob` | Reorder buffer entries | `352` |
| `l1_dcache` | L1 data cache lines | `64 * 12` |
| `l2_cache` | L2 cache lines | `1024 * 8` |
| `l3_cache` | L3 cache lines | `16384 * 16` |
| `l1_dtlb` | L1 data TLB entries | `16 * 4` |
| `uop_cache` | Uop cache ways per set | `8` |

### Resource Watermarks

`WATERMARK` dictionary maps resource names to minimum reserved entries:

| Resource | Description | Example Value |
|----------|-------------|---------------|
| `int_isq` | Minimum integer IQ entries | `6` |
| `fp_isq` | Minimum FP IQ entries | `6` |
| `load_isq` | Minimum load IQ entries | `8` |
| `load_lsq` | Minimum LSQ entries | `64` |
| `rob` | Minimum ROB entries | `176` |
| `uop_cache` | Minimum uop cache ways | `4` |

### Example

```python
# profiling/profiling_server/tools/machine_data.py

# Resource type categories
SEQUENTIAL_TYPE = ["int_isq", "fp_isq", "load_isq", "uop_cache"]
PARALLEL_TYPE = ["l1_dcache", "l2_cache", "l1_dtlb", "l3_cache"]
PORT_TYPE = ["int_port", "fp_port"]

# Features to profile (order matters)
TARGET_FEATURE = ['int_port', 'int_isq', 'fp_port', 'load_isq', 
                  'l1_dcache', 'l2_cache', "l1_dtlb"]

# Profiling parameters
SAMPLING_INTERVAL = 2
MEDIUM_RATIO = 0.8

# Resource sizes (update for your CPU)
SIZE = {
    "int_isq":     75,
    "fp_isq":      75,
    "load_isq":    46,
    "load_lsq":    128,
    "rob":         352,
    "l1_dcache":   64 * 12,    # 64 sets * 12 ways
    "l2_cache":    1024 * 8,   # 1024 sets * 8 ways
    "l3_cache":    16384 * 16, # 16384 sets * 16 ways
    "l1_dtlb":     16 * 4,     # 16 sets * 4 ways
    "uop_cache":   8,          # 8 ways
}

# Watermarks (minimum reserved)
WATERMARK = {
    "int_isq":     6,
    "fp_isq":      6,
    "load_isq":    8,
    "load_lsq":    64,
    "rob":         176,
    "l1_dcache":   0,
    "l2_cache":    0,
    "l3_cache":    0,
    "l1_dtlb":     0,
    "uop_cache":   4,
}
```

---

## Global Variable Generator

**File:** `profiling/profiling_server/tools/global_variable_generator.py`

### Injector Information

| Variable | Type | Description |
|----------|------|-------------|
| `InjectorInfo` | dataclass | Feature, pressure, and path info |
| `injector_directory_list` | list | All available injectors |

### Feature Mappings

| Variable | Type | Description |
|----------|------|-------------|
| `FEATURE_TO_ID` | dict | Feature name to numeric ID |
| `FEATURE_TO_INDEX` | dict | Feature name to array index |
| `FEATURE_TYPE_TABLE` | dict | Feature name to type (SEQUENTIAL/PARALLEL/PORT) |

> **Note:** Legacy aliases (`feature_to_featureID`, `feature_to_arr_idx`, `feature_type_table`, `ENUM_SEQUENTIAL`, `ENUM_PARALLEL`) exist for backward compatibility but new code should use the canonical names above.

### Training Configuration

| Variable | Type | Description |
|----------|------|-------------|
| `TRAINING_JOB_IDS` | list[int] | Job IDs whose profiling data is used for model training and co-run combination measurement |
| `MULTI_THREADED_WORKLOADS` | set[int] | Job IDs of inherently multi-threaded workloads. During profiling, these are pinned to both sibling cores simultaneously (`taskset -c cid0,cid1`) instead of separate per-core copies. Two multi-threaded workloads are never co-run together. |
| `ALL_JOB_IDS` | list[int] | All profiled job IDs (superset of `TRAINING_JOB_IDS`). Used by the scheduling system. Not needed in `profiling_server`. |

> **Note:** Legacy aliases (`training_jobid_list`, `multi_threaded_workloads`, `global_jobid_list`) exist for backward compatibility. Note that `ALL_JOB_IDS` (and its alias `global_jobid_list`) is only defined in `profiling/live_server/tools/global_variable_generator.py`, not in the `profiling_server` copy.

### Pressure Points

| Variable | Type | Description |
|----------|------|-------------|
| `PRESSURE_POINTS` | dict | Pressure levels for each feature |

> **Note:** Legacy alias `target_points` exists for backward compatibility.

---

## Scheduling Configuration

### Profile Data Loader

**File:** `scheduling/userlevel/python/smtcheck/profile_data_loader.py`

| Variable | Type | Description | Default |
|----------|------|-------------|---------|
| `PROFILE_SERVER_IP` | string | Profiling server address | `"192.168.0.20"` |
| `PORT` | int | Profiling server port | `8080` |

### Machine Data (Scheduling)

**File:** `scheduling/userlevel/python/smtcheck/machine_data.py`

Same structure as profiling machine_data.py but may have different values for the scheduling machine.

---

## Kernel Module Configuration

### IPC_monitor

**File:** `scheduling/kernel/module/IPC_monitor.c`

| Constant | Type | Description | Value |
|----------|------|-------------|-------|
| `MAX_SLOTS` | int | Maximum tracked PGIDs | `4096` |
| `PGID_HASH_BITS` | int | Hash table size bits | `10` |

### runtime_monitor

**File:** `scheduling/kernel/module/runtime_monitor.c`

| Constant | Type | Description | Value |
|----------|------|-------------|-------|
| `INTERVAL_MS` | int | Timer interval (ms) | `1000` |
| `NETLINK_USER` | int | Netlink protocol number | `31` |
| `long_running_threshold` | int | Runtime threshold (seconds) | `3600` |

---

## Diagnostic Templates

### queue_type.cpp

**File:** `diag/templates/queue_type.cpp`

| Constant | Description | Value |
|----------|-------------|-------|
| `ACCESS_CACHELINES` | Number of cache lines | `1 << 20` |
| `ARRAY_SIZE` | Array size in bytes | `ACCESS_CACHELINES << 6` |
| `EVENT_COUNT` | Performance events to monitor | `2` |

### Performance Events

```cpp
static const char* event_list[EVENT_COUNT] = {
    "cycles",
    "instructions"
};
```

---

## Environment Variables

### Perf Event Access

```bash
# Allow user-space perf events (recommended for SMTcheck)
sudo sysctl -w kernel.perf_event_paranoid=-1
```

### Library Paths

```bash
# libpfm4 library path (if not in standard location)
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH

# Python path for smtcheck module
export PYTHONPATH=/path/to/SMTcheck/scheduling/userlevel:$PYTHONPATH
```

---

## MongoDB Collections

### Database: `profile_data`

| Collection | Description |
|------------|-------------|
| `measurement` | Individual IPC measurements |
| `combination` | Pairwise workload measurements |

### Measurement Document Schema

```json
{
    "node_name": "string",
    "global_jobid": "int",
    "feature": "string",
    "feature_id": "int",
    "feature_type": "int",
    "pressure": "int",
    "run_type": "workload|injector",
    "IPC": "float",
    "timestamp": "int (Unix epoch)"
}
```

### Combination Document Schema

```json
{
    "node_name": "string",
    "data": {
        "<job_id>": {
            "single": "float",
            "<other_job_id>": "float"
        }
    }
}
```

---

## Trained Model Format

**File:** `scheduling/trained_model/prediction_model_*.json`

```json
{
    "feature_list": ["base", "int_port", "int_isq", ...],
    "coefficients": [0.1234, 0.2345, ...],
    "intercept": 0.0123
}
```

| Field | Type | Description |
|-------|------|-------------|
| `feature_list` | list[str] | Feature names (first is "base") |
| `coefficients` | list[float] | Model coefficients (one per feature) |
| `intercept` | float | Model intercept term |
