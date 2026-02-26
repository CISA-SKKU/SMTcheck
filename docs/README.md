# SMTcheck Documentation

This directory contains detailed documentation for the SMTcheck framework.

## Table of Contents

1. [Getting Started](getting-started.md) - Installation, prerequisites, and quick start guide
2. [Diagnostic System](diagnostics.md) - How to generate and run diagnostic programs
3. [Profiling System](profiling.md) - Workload profiling and characteristic measurement
4. [Scheduling System](scheduling.md) - SMT-aware process scheduling
5. [Extending SMTcheck](extending.md) - Adding new resources and customization
6. [Configuration Reference](configuration.md) - All configuration options explained

## Quick Links

| Topic | Description |
|-------|-------------|
| [Prerequisites](getting-started.md#prerequisites) | Required software and hardware |
| [Running Diagnostics](diagnostics.md#running-diagnostics) | Extract microarchitectural resource features |
| [Training Models](profiling.md#training-prediction-model) | Create interference prediction models |
| [Kernel Modules](scheduling.md#kernel-modules) | IPC and runtime monitoring |

## Architecture Overview

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
│        │                    │                           │                   │
│        ▼                    ▼                           ▼                   │
│  ┌───────────┐        ┌───────────┐              ┌───────────────┐          │
│  │ Binaries  │        │  MongoDB  │              │ Kernel Module │          │
│  │ & Results │        │  Storage  │              │ + Userspace   │          │
│  └───────────┘        └───────────┘              └───────────────┘          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Workflow

1. **Hardware Feature Extraction** (`diag/`): Generate and run diagnostic programs to extract hidden microarchitectural features and measure how each resource behaves under contention.

2. **Workload Profiling** (`profiling/`): Profile target workloads by running them alongside injector programs to measure sensitivity and intensity for each resource. Train a linear regression model using the measured characteristics to predict slowdown.

3. **Contention-Aware Scheduling** (`scheduling/`): Use the trained model at runtime to calculate compatibility scores and optimize CPU affinity assignments for co-running workloads.
