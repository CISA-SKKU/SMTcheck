# SMTcheck: SMT Interference Profiling and Scheduling Framework

A comprehensive framework for measuring, predicting, and mitigating Simultaneous Multi-Threading (SMT) interference on shared microarchitectural resources.

## Overview

When multiple threads share a physical CPU core via SMT (e.g., Intel Hyper-Threading), they compete for shared resources like caches, issue queues, and execution ports. This competition can cause significant performance degradation that varies based on workload characteristics.

SMTcheck provides a three-stage workflow:

1. **Hardware Feature Extraction** (`diag/`) - Extract hidden microarchitectural features
2. **Workload Profiling** (`profiling/`) - Characterize per-workload contention behavior for each shared resource
3. **Contention-Aware Scheduling** (`scheduling/`) - Predict interference and schedule workloads for optimal performance

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SMTcheck Framework                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────────────────┐  │
│  │    diag/    │    │   profiling/    │    │        scheduling/          │  │
│  │             │    │                 │    │                             │  │
│  │ Diagnostic  │───►│ Profiling       │───►│ SMT-aware Scheduling        │  │
│  │ Generation  │    │ & Training      │    │ & CPU Affinity              │  │
│  │             │    │                 │    │                             │  │
│  └─────────────┘    └─────────────────┘    └─────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Repository Structure

```
SMTcheck/
├── diag/                   # Diagnostic program generator & runner
│   ├── main.py             # Entry point
│   ├── parse_and_plot.py   # Result parsing & visualization
│   ├── diag_generator/     # Code generation (x86, etc.)
│   ├── parser/             # Output parsers per resource type
│   ├── run/                # Execution scripts
│   ├── templates/          # C++ code templates
│   ├── bin/                # Compiled diagnostic binaries (generated)
│   ├── code/               # Generated C++ source code (generated)
│   └── outputs/            # IPC measurement results (generated)
│
├── profiling/                                      # Workload profiling system
│   ├── profiling_server/                           # TCP server for profiling
│   │   ├── run_profile_server.py                   # Server entry point
│   │   ├── setup.py                                # Node setup script
│   │   ├── injector_generator/                     # Injector code generation
│   │   ├── injector_templates/                     # C++ templates for injectors
│   │   ├── tools/                                  # Configuration and utilities
│   │   ├── target_workload_runners/                # Workload execution wrappers
│   │   ├── injector/                               # Injector binaries (generated)
│   │   ├── code/                                   # Injector source code (generated)
│   │   └── profile_results/                        # Profiling output data (generated)
│   └── live_server/                                # Model training & live profiling
│       ├── generate_prediction_model.py            # Train prediction model
│       ├── send_profiling_request_for_testing.py   # Test client
│       ├── tools/                                  # Workload characterization utilities
│       └── outputs/                                # Trained model output (generated)
│
├── scheduling/             # SMT-aware process scheduling
│   ├── kernel/             # Linux kernel modules
│   │   └── module/         # IPC_monitor, runtime_monitor
│   ├── userlevel/          # User-space components
│   │   ├── python/smtcheck/
│   │   └── c/              # C++ native extension
│   ├── script/             # Test and utility scripts
│   └── trained_model/      # Prediction models (generated)
│
└── docs/                   # Detailed documentation
```

## Quick Start

### Prerequisites

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install build-essential linux-headers-$(uname -r)

# Install the pfm library
git clone https://github.com/wcohen/libpfm4.git
cd libpfm4
make
sudo make install

```

Additionally, install [MongoDB](https://www.mongodb.com/docs/manual/installation/) (required for profiling & scheduling).

### Run Diagnostics

```bash
cd diag
python3 main.py --target_resource load_isq --isa x86
```

### Profile Workloads

Before profiling, update `profiling/profiling_server/tools/config.py` to match your environment (`HOST`, `PORT`, `DB_SERVER`, etc.).

```bash
cd profiling/profiling_server

# Step 1: Generate injectors, run baseline measurements, and push results to DB.
#         This may take a while depending on the number of resources.
python3 setup.py --node_name <name>

# Step 2: Start the profiling server (listens for workload profiling requests).
python3 run_profile_server.py
```

### Train Model

```bash
cd profiling/live_server
python3 generate_prediction_model.py
```

### Enable Scheduling (Optional)

Requires root privileges for kernel module loading and CPU affinity control.

```bash
cd scheduling/kernel
make && make insmod    # insmod internally uses sudo
cd ..
sudo python3 script/test/scheduling_test.py
```

## Documentation

Detailed documentation is available in the [docs/](docs/) directory:

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, prerequisites, quick start |
| [Diagnostics](docs/diagnostics.md) | Diagnostic program generation and execution |
| [Profiling](docs/profiling.md) | Workload profiling and model training |
| [Scheduling](docs/scheduling.md) | Kernel modules and SMT-aware scheduling |
| [Extending](docs/extending.md) | Adding new resources and customization |
| [Configuration](docs/configuration.md) | All configuration options |

## Publication
TBD.