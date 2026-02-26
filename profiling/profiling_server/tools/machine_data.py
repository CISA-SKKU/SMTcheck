"""
Machine-Specific Resource Specifications

This file contains CPU microarchitecture parameters that must be
updated for each target machine. These values define:

- Resource sizes (number of entries, ways, lines)
- Watermark levels (minimum reserved entries)
- Resource categorization (sequential, parallel, port types)

Update these values based on your CPU's specifications.
"""

import os

# =============================================================================
# Resource Type Categories
# =============================================================================
SEQUENTIAL_TYPE = ["int_isq", "fp_isq", "load_isq", "uop_cache"]
PARALLEL_TYPE = ["l1_dcache", "l2_cache", "l1_dtlb", "l3_cache"]
PORT_TYPE = ["int_port", "fp_port"]

# Features to profile (order matters for indexing)
TARGET_FEATURE = ['int_port', 'int_isq', 'fp_port', 'load_isq', 'l1_dcache', 'l2_cache', "l1_dtlb"]
# =============================================================================
# Profiling Parameters
# =============================================================================
SAMPLING_INTERVAL = 2       # Seconds between measurements
UOP_CACHE_WINDOW_SIZE = 64  # Uop cache window size in uops
UOP_CACHE_NUM_SETS = 64     # Number of uop cache sets
MEDIUM_RATIO = 0.8          # Medium pressure = MAX * MEDIUM_RATIO
NODE_NAME = None            # Set at runtime by setup.py

# =============================================================================
# Resource Watermarks (Minimum Reserved Entries)
# =============================================================================
# These represent the minimum entries that must be available for the core
# to function properly. Effective size = SIZE - WATERMARK
WATERMARK = {
    "int_isq":     6,       # Integer issue queue entries
    "fp_isq":      6,       # Floating-point issue queue entries
    "load_isq":    8,       # Load issue queue entries
    "load_lsq":    64,      # Load-store queue entries
    "rob":         176,     # Reorder buffer entries
    "l1_dcache":   0,       # L1 data cache (no watermark)
    "l2_cache":    0,       # L2 cache (no watermark)
    "l3_cache":    0,       # L3 cache (no watermark)
    "l1_dtlb":     0,       # L1 data TLB (no watermark)
    "uop_cache":   4,       # Uop cache ways
}

# =============================================================================
# Resource Sizes (Total Capacity)
# =============================================================================
SIZE = {
    "int_isq":     75,          # Integer issue queue entries
    "fp_isq":      75,          # Floating-point issue queue entries
    "load_isq":    46,          # Load issue queue entries
    "load_lsq":    128,         # Load-store queue entries
    "rob":         352,         # Reorder buffer entries
    "l1_dcache":   64 * 12,     # L1D cache lines (sets * ways)
    "l2_cache":    1024 * 8,    # L2 cache lines
    "l3_cache":    16384 * 16,  # L3 cache lines
    "l1_dtlb":     16 * 4,      # L1 DTLB entries (sets * ways)
    "uop_cache":   8,           # Uop cache ways per set
}

def gen_sibling_core_dict():
    sibling_core_dict = dict()
    lines = list(map(lambda x: x.strip(), os.popen("lscpu --parse=CPU,Core").read().strip().split("\n")))
    for idx in range(len(lines)):
        line = lines[idx]
        if(line[0] != "#"):
            lines = lines[idx:]
            break
    for line in lines:
        logical_core, physical_core = map(int, line.split(","))
        if physical_core not in sibling_core_dict:
            sibling_core_dict[physical_core] = []
        sibling_core_dict[physical_core].append(logical_core)

    return sibling_core_dict
sibling_core_dict = gen_sibling_core_dict()