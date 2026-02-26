"""
Global Variable Generator

This module defines machine-specific constants, feature mappings, and
pre-computed lookup tables used throughout the profiling system.
"""

from dataclasses import dataclass
from .machine_data import WATERMARK, SIZE, TARGET_FEATURE, MEDIUM_RATIO, PARALLEL_TYPE, SEQUENTIAL_TYPE

# =============================================================================
# Data Classes
# =============================================================================
@dataclass
class InjectorInfo:
    """Information about an injector executable"""
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
        # Port-type: only high pressure
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
# Training Configuration
# =============================================================================
# Job IDs used for training the prediction model
TRAINING_JOB_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 25, 26, 29, 30]

# Multi-threaded workloads (require special handling)
MULTI_THREADED_WORKLOADS = {25, 26, 29, 30}

# All available job IDs
ALL_JOB_IDS = list(range(0, 31))


# =============================================================================
# Legacy Compatibility Aliases
# =============================================================================
# These aliases maintain backward compatibility with existing code
target_points = PRESSURE_POINTS
feature_to_arr_idx = FEATURE_TO_INDEX
feature_to_featureID = FEATURE_TO_ID
training_jobid_list = TRAINING_JOB_IDS
multi_threaded_workloads = MULTI_THREADED_WORKLOADS
global_jobid_list = ALL_JOB_IDS