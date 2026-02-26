"""
Global Variable Generator

This module defines machine-specific constants, feature mappings, and
pre-computed lookup tables used throughout the profiling system.
"""

from .config import *
from dataclasses import dataclass
from .machine_data import WATERMARK, SIZE, TARGET_FEATURE, MEDIUM_RATIO, PARALLEL_TYPE, SEQUENTIAL_TYPE


# =============================================================================
# Data Classes
# =============================================================================
@dataclass
class InjectorInfo:
    """
    Configuration for an injector binary.
    
    Attributes:
        feature: Resource feature name (e.g., 'int_isq', 'l1_dcache')
        pressure: Pressure level (0=LOW, 1=MEDIUM, 2=HIGH for sequential types)
        injector_dir: Path to the compiled injector binary
    """
    feature: str
    pressure: int
    injector_dir: str


# =============================================================================
# Resource Size Lookups
# =============================================================================
# Pre-computed lists for quick index-based access
WATERMARK_SIZE = [
    WATERMARK[feature] if feature in {"int_isq", "fp_isq", "load_isq", "uop_cache"} else 0 
    for feature in TARGET_FEATURE
]

RESOURCE_SIZE = [
    SIZE[feature] if feature in {"int_isq", "fp_isq", "load_isq", "uop_cache"} else 0 
    for feature in TARGET_FEATURE
]


# =============================================================================
# Pressure Points Configuration
# =============================================================================
# Pressure points for each feature: (low, medium, high) thresholds
PRESSURE_POINTS = dict()
for feature, size, watermark in zip(TARGET_FEATURE, RESOURCE_SIZE, WATERMARK_SIZE):
    if feature in {"int_isq", "fp_isq", "load_isq", "uop_cache"}:
        # Sequential-type: use ratio-based medium point
        PRESSURE_POINTS[feature] = (1, int((size - watermark) * MEDIUM_RATIO), size - watermark)
    elif "port" in feature:
        # Port-type: no discrete pressure points
        PRESSURE_POINTS[feature] = []
    else:
        # Parallel-type: binary pressure levels
        PRESSURE_POINTS[feature] = (1, 4)


# =============================================================================
# Feature Index Mappings
# =============================================================================
# Map feature name to its canonical ID (for database storage)
FEATURE_TO_ID = {
    feature: idx for idx, feature in enumerate([
        'uop_cache', 'int_port', 'int_isq', 'fp_port', 'fp_isq',
        'load_isq', 'l1_dcache', 'l2_cache', 'l1_dtlb',
    ])
}

# Map feature name to its index in TARGET_FEATURE list
FEATURE_TO_INDEX = {
    feature: idx for idx, feature in enumerate(TARGET_FEATURE)
}


# =============================================================================
# Feature Type Constants
# =============================================================================
FEATURE_TYPE_SEQUENTIAL = 0
FEATURE_TYPE_PARALLEL = 1
FEATURE_TYPE_PORT = 2

# Map each feature to its type
FEATURE_TYPE_TABLE = {
    feature: (FEATURE_TYPE_SEQUENTIAL if feature in SEQUENTIAL_TYPE else 
              FEATURE_TYPE_PARALLEL if feature in PARALLEL_TYPE else 
              FEATURE_TYPE_PORT)
    for feature in ['uop_cache', 'int_port', 'int_isq', 'fp_port', 'fp_isq', 
                    'load_isq', 'l1_dcache', 'l2_cache', 'l1_dtlb']
}


# =============================================================================
# Injector Directory Configuration
# =============================================================================
# Load injector directory list from configuration file
# Format: feature,pressure_level,path_to_injector
injector_directory_list = []
with open("tools/injector_exec_dir.txt", "r") as f:
    lines = f.read().strip().split("\n")
    for line in lines:
        feature, pressure, injector_dir = line.strip().split(",")
        pressure = int(pressure)
        injector_directory_list.append(InjectorInfo(feature=feature, pressure=pressure, injector_dir=injector_dir))


# =============================================================================
# Training Configuration
# =============================================================================
# NOTE: The following variables must be set before running measure_combination.
#       The job IDs correspond to workloads registered in MongoDB after profiling.
#
# - TRAINING_JOB_IDS:
#     List of job IDs whose workloads will be measured for co-run training data.
#     measure_combination iterates over all pairs in this list to measure
#     IPC degradation when two workloads share a physical core via SMT.
#     Example: [0, 1, 2, 5, 8]
#
# - MULTI_THREADED_WORKLOADS:
#     Set of job IDs that are themselves multi-threaded.
#     These workloads already use multiple threads internally, so during
#     alone-measurement they are pinned to BOTH sibling cores at once
#     (taskset -c cid0,cid1) instead of spawning separate per-core copies.
#     When building co-run pairs, two multi-threaded workloads are never
#     paired together because each already occupies both logical cores.
#     Example: {25, 26, 29, 30}
#
# ALL_JOB_IDS is NOT used in profiling_server; it is only relevant to
# the scheduling and live_server components.
# =============================================================================
TRAINING_JOB_IDS = []
MULTI_THREADED_WORKLOADS = set()


# =============================================================================
# Legacy Compatibility Aliases
# =============================================================================
# These aliases maintain backward compatibility with existing code
target_points = PRESSURE_POINTS
feature_to_arr_idx = FEATURE_TO_INDEX
feature_to_featureID = FEATURE_TO_ID
feature_type_table = FEATURE_TYPE_TABLE
training_jobid_list = TRAINING_JOB_IDS
multi_threaded_workloads = MULTI_THREADED_WORKLOADS
# Note: global_jobid_list (ALL_JOB_IDS) is intentionally absent here;
# it is only needed by live_server and scheduling components.

# Legacy enum aliases
ENUM_SEQUENTIAL = FEATURE_TYPE_SEQUENTIAL
ENUM_PARALLEL = FEATURE_TYPE_PARALLEL