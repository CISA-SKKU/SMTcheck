"""
Score Updater

This module calculates workload characteristics (sensitivity, usage, intensity, base_slowdown)
for each target feature by analyzing profiling data from MongoDB.

The characteristics are used to train a prediction model for SMT interference estimation.
"""

from pymongo import MongoClient
from collections import defaultdict
from enum import IntEnum
from .global_variable_generator import *
from .machine_data import *
from . import profile_data_loader
from . import smtcheck_native
import numpy as np
from collections import defaultdict
import itertools
import glob
import json
import os
import sys

# =============================================================================
# Constants & Enums
# =============================================================================
class SequentialPressureLevel(IntEnum):
    """Pressure levels for sequential-type resources (int_isq, fp_isq, load_isq, uop_cache)"""
    LOW = 0
    MEDIUM = 1
    HIGH = 2


class ParallelPressureLevel(IntEnum):
    """Pressure levels for parallel-type resources (l1_dcache, l2_cache, l1_dtlb)"""
    LOW = 0
    HIGH = 1


class PortPressureLevel(IntEnum):
    """Pressure levels for port-type resources (int_port, fp_port)"""
    HIGH = 0


class ProcessType(IntEnum):
    """Type of process: workload being profiled or injector creating contention"""
    WORKLOAD = 0
    INJECTOR = 1


# =============================================================================
# Data Classes
# =============================================================================
@dataclass
class WorkloadCharacteristics:
    """
    Characteristics of a workload for a specific resource feature.
    
    Attributes:
        sensitivity: How much the workload slows down under contention (0-1)
        usage: How much of the resource the workload uses (0-1)
        intensity: How much contention the workload creates for others (0-1)
        base_slowdown: Slowdown even with minimal contention (0-1)
    """
    sensitivity: float = 0.0
    usage: float = 0.0
    intensity: float = 0.0
    base_slowdown: float = 0.0

# =============================================================================
# Global State
# =============================================================================
# Injector IPC data by feature type (populated during initialization)
parallel_injector_ipc = dict()     # feature -> {pressure_config -> IPC}
sequential_injector_ipc = dict()     # feature -> {pressure_config -> IPC}
port_injector_ipc = dict()      # feature -> {pressure_config -> IPC}

# Combination IPC data (job pairs)
combination_ipc_data = dict()   # base_job_id -> {col_job_id -> IPC}

# Profile data for each workload
profile_ipc_data = dict()       # job_id -> {feature -> {(pressure, process_type) -> IPC}}

profile_data_table = defaultdict(dict)  # job_id -> {feature -> WorkloadCharacteristics}
model_coef = np.array([0.0 for _ in range(len(TARGET_FEATURE) + 1)])  # Extra element at index 0 for base_slowdown term
model_intercept = 0.0
output_slowdowns: np.ndarray = None
output_slowdown_index_map = dict()
target_global_jobids = set()
scale_factor_table = dict()
single_ipc_table = dict()
characteristics_dict: dict[int, list[WorkloadCharacteristics]] = dict()
stale_target = set()

# =============================================================================
# Utility Functions
# =============================================================================
def clamp(value, min_val=0, max_val=1):
    """Clamp value between min_val and max_val"""
    return min(max(value, min_val), max_val)

class LinearEquation:
    """
    Linear equation solver for two-point line interpolation.
    Used to find resource drop points from IPC measurements.
    """
    def __init__(self, point1, point2):
        x1, y1 = point1
        x2, y2 = point2
        
        # Avoid division by zero
        if x2 == x1:
            x1 = x2 - 0.001
        
        self.slope = (y2 - y1) / (x2 - x1)
        if self.slope == 0:
            self.slope = 0.001
        
        self.y_intercept = y1 - self.slope * x1
        
    def solve_for_y(self, x_value):
        """Calculate y for given x"""
        return self.slope * x_value + self.y_intercept
    
    def solve_for_x(self, y_value):
        """Calculate x for given y"""
        return (y_value - self.y_intercept) / self.slope
    
def compute_activation(usage_base, usage_col, feature):
    """
    Compute activation value for resource contention.
    
    Different resource types have different contention models:
    - Sequential-type: Linear contention when total usage exceeds capacity
    - Parallel-type: Multiplicative contention based on combined usage
    
    Args:
        usage_base: Resource usage of base workload (0-1)
        usage_col: Resource usage of co-located workload (0-1)
        feature: Resource feature name
        
    Returns:
        Activation value representing contention intensity
    """
    if feature in SEQUENTIAL_TYPE:
        # Sequential-type contention: only matters when total usage > 1 (overflow)
        return max(0, (usage_base + usage_col) - 1)
    else:
        # Parallel-type contention: multiplicative model
        return usage_base * usage_col * (usage_base + usage_col) / 2

# =============================================================================
# Data Processing Functions
# =============================================================================
def parse_profile_documents(raw_documents):
    """
    Parse raw MongoDB documents into structured IPC dictionary.
    
    Args:
        raw_documents: MongoDB cursor with profile measurement documents
        
    Returns:
        dict: {feature -> {(pressure, process_type) -> IPC}}
    """
    ipc_by_feature = defaultdict(dict)

    for doc in raw_documents:
        feature = doc["feature"]
        pressure = doc["pressure"]
        ipc = doc["IPC"]
        process_type = ProcessType.INJECTOR if doc["run_type"] == "injector" else ProcessType.WORKLOAD

        key = (pressure, process_type)
        ipc_by_feature[feature][key] = ipc
    
    return ipc_by_feature


# =============================================================================
# Injector Data Loading Functions
# =============================================================================
def load_parallel_injector_data(db_handler: profile_data_loader.DatabaseHandler, parallel_features):
    """
    Load injector IPC data for parallel-type features from database.
    Populates global parallel_injector_ipc dictionary.
    """
    global parallel_injector_ipc
    
    for feature in parallel_features:
        parallel_injector_ipc[feature] = defaultdict(dict)

        # Load data for single, low-contention, and high-contention injector runs
        job_id_mapping = [(-1, "single"), (-2, "low"), (-3, "high")]
        
        for job_id, config_name in job_id_mapping:
            for pressure in ParallelPressureLevel:
                query = {
                    "node_name": NODE_NAME,
                    "feature": feature,
                    "global_jobid": job_id,
                    "pressure": pressure,
                }
                docs = list(db_handler.measurement_collection.find(query))
                
                if len(docs) != 1:
                    print(f"[WARNING] Expected 1 document for {feature}, job_id={job_id}, "
                          f"pressure={pressure}, found {len(docs)}", flush=True)
                    continue
                
                ipc = docs[0]["IPC"]
                parallel_injector_ipc[feature][config_name][pressure] = ipc
                print(f"[DEBUG] Parallel injector: feature={feature}, config={config_name}, "
                      f"pressure={pressure}, IPC={ipc:.4f}", flush=True)


def load_sequential_injector_data(db_handler: profile_data_loader.DatabaseHandler, sequential_features):
    """
    Load injector IPC data for sequential-type features from database.
    Populates global sequential_injector_ipc dictionary.
    """
    global sequential_injector_ipc
    
    for feature in sequential_features:
        sequential_injector_ipc[feature] = {"single": dict()}

        for pressure in SequentialPressureLevel:
            query = {
                "node_name": NODE_NAME,
                "feature": feature,
                "global_jobid": -1,
                "pressure": pressure,
            }
            docs = list(db_handler.measurement_collection.find(query))
            
            if len(docs) != 1:
                print(f"[WARNING] Expected 1 document for {feature}, pressure={pressure}, "
                      f"found {len(docs)}", flush=True)
                continue
            
            ipc = docs[0]["IPC"]
            sequential_injector_ipc[feature]["single"][pressure] = ipc

    
def load_port_injector_data(db_handler: profile_data_loader.DatabaseHandler, port_features):
    """
    Load injector IPC data for port-type features from database.
    Populates global port_injector_ipc dictionary.
    """
    global port_injector_ipc
    
    for feature in port_features:
        port_injector_ipc[feature] = {"single": dict()}

        query = {
            "node_name": NODE_NAME,
            "feature": feature,
            "global_jobid": -1,
            "pressure": PortPressureLevel.HIGH,
        }
        docs = list(db_handler.measurement_collection.find(query))
        
        if len(docs) != 1:
            print(f"[WARNING] Expected 1 document for {feature}, found {len(docs)}", flush=True)
            continue
        
        ipc = docs[0]["IPC"]
        port_injector_ipc[feature]["single"][PortPressureLevel.HIGH] = ipc


# =============================================================================
# Characteristic Calculators
# =============================================================================
def calculate_sequential_characteristics(profile_ipc, solo_ipc, pressure_points, 
                                     injector_ipc, resource_size, watermark):
    """
    Calculate workload characteristics for sequential-type resources.
    
    Sequential-type resources (issue queues, uop cache) have three pressure levels and
    use linear interpolation to find the resource usage drop point.
    
    Args:
        profile_ipc: IPC measurements for this workload {(pressure, process_type) -> IPC}
        solo_ipc: Solo run IPC data {"single" -> IPC}
        pressure_points: (low, medium, high) pressure point values
        injector_ipc: Injector-only IPC measurements
        resource_size: Total size of the resource
        watermark: Minimum usable resource threshold
        
    Returns:
        tuple: (sensitivity, usage, intensity, base_slowdown) all clamped to [0, 1]
    """
    # Intensity: How much this workload slows down the injector
    injector_solo_ipc = injector_ipc["single"][SequentialPressureLevel.LOW]
    injector_corun_ipc = profile_ipc[(SequentialPressureLevel.LOW, ProcessType.INJECTOR)]
    intensity = 1 - (injector_corun_ipc / injector_solo_ipc)

    # Base slowdown: Slowdown even under minimal contention
    workload_solo_ipc = solo_ipc["single"]
    workload_low_ipc = profile_ipc[(SequentialPressureLevel.LOW, ProcessType.WORKLOAD)]
    base_slowdown = 1 - (workload_low_ipc / workload_solo_ipc)

    # Sensitivity: Slowdown increase from low to high pressure
    workload_high_ipc = profile_ipc[(SequentialPressureLevel.HIGH, ProcessType.WORKLOAD)]
    sensitivity = 1 - (workload_high_ipc / workload_low_ipc)

    # Usage: Find drop point using linear interpolation
    workload_medium_ipc = profile_ipc[(SequentialPressureLevel.MEDIUM, ProcessType.WORKLOAD)]
    
    line = LinearEquation(
        (pressure_points[1], workload_medium_ipc),
        (pressure_points[2], workload_high_ipc)
    )
    drop_point = line.solve_for_x(workload_low_ipc)

    # Clamp drop_point to valid range [watermark, size - watermark]
    usable_max = resource_size - watermark
    if line.slope > 0:
        # Abnormal: IPC increases with pressure -> assume minimal usage
        drop_point = usable_max
    elif drop_point <= watermark:
        # Left boundary -> maximum usage
        drop_point = watermark
    elif drop_point >= usable_max:
        # Right boundary -> minimum usage
        drop_point = usable_max

    # Avoid division by zero
    if sensitivity <= 0:
        sensitivity = 1e-7

    # Calculate usage based on sensitivity threshold
    SENSITIVITY_THRESHOLD = 0.05
    if sensitivity > SENSITIVITY_THRESHOLD:
        usage = max((resource_size - drop_point) / resource_size, 0)
    else:
        usage = 0  # Negligible usage if sensitivity is very low

    return tuple(map(clamp, [sensitivity, usage, intensity, base_slowdown]))


def calculate_parallel_characteristics(profile_ipc, solo_ipc, injector_ipc):
    """
    Calculate workload characteristics for parallel-type resources.
    
    Parallel-type resources (L1D, L2, L1 DTLB) have two pressure levels and
    measure usage by comparing injector slowdown.
    
    Args:
        profile_ipc: IPC measurements for this workload
        solo_ipc: Solo run IPC data
        injector_ipc: Injector IPC measurements under different configurations
        
    Returns:
        tuple: (sensitivity, usage, intensity, base_slowdown) all clamped to [0, 1]
    """
    # Intensity: How much this workload slows down the injector
    injector_solo_ipc = injector_ipc["single"][ParallelPressureLevel.LOW]
    injector_corun_ipc = profile_ipc[(ParallelPressureLevel.LOW, ProcessType.INJECTOR)]
    intensity = 1 - (injector_corun_ipc / injector_solo_ipc)
    
    # Base slowdown
    workload_solo_ipc = solo_ipc["single"]
    workload_low_ipc = profile_ipc[(ParallelPressureLevel.LOW, ProcessType.WORKLOAD)]
    base_slowdown = 1 - (workload_low_ipc / workload_solo_ipc)
    
    # Sensitivity
    workload_high_ipc = profile_ipc[(ParallelPressureLevel.HIGH, ProcessType.WORKLOAD)]
    sensitivity = 1 - (workload_high_ipc / workload_low_ipc)

    # Usage: Compare injector IPC when co-running vs high-contention baseline
    injector_max_ipc = injector_ipc["high"][ParallelPressureLevel.LOW]
    injector_min_ipc = injector_ipc["high"][ParallelPressureLevel.HIGH]
    injector_current_ipc = profile_ipc[(ParallelPressureLevel.HIGH, ProcessType.INJECTOR)]
    
    usage = (injector_max_ipc - injector_current_ipc) / (injector_max_ipc - injector_min_ipc)

    return tuple(map(clamp, [sensitivity, usage, intensity, base_slowdown]))


def calculate_port_characteristics(profile_ipc, solo_ipc, injector_ipc):
    """
    Calculate workload characteristics for port-type resources.
    
    Port resources (INT port, FP port) only have high pressure level.
    
    Args:
        profile_ipc: IPC measurements for this workload
        solo_ipc: Solo run IPC data
        injector_ipc: Injector IPC measurements
        
    Returns:
        tuple: (sensitivity, usage, intensity, base_slowdown) all clamped to [0, 1]
    """
    # Intensity
    injector_solo_ipc = injector_ipc["single"][PortPressureLevel.HIGH]
    injector_corun_ipc = profile_ipc[(PortPressureLevel.HIGH, ProcessType.INJECTOR)]
    intensity = 1 - (injector_corun_ipc / injector_solo_ipc)
    
    # Base slowdown and sensitivity are the same for ports
    workload_solo_ipc = solo_ipc["single"]
    workload_high_ipc = profile_ipc[(PortPressureLevel.HIGH, ProcessType.WORKLOAD)]
    base_slowdown = 1 - (workload_high_ipc / workload_solo_ipc)
    sensitivity = base_slowdown
    
    # Usage equals intensity for ports
    usage = intensity

    return tuple(map(clamp, [sensitivity, usage, intensity, base_slowdown]))

# =============================================================================
# Public API Functions
# =============================================================================
def initialize():
    """
    Initialize the calculator module.
    
    - Connects to MongoDB
    - Loads combination IPC data
    - Loads injector baseline data for all feature types
    """
    global combination_ipc_data, profile_ipc_data

    combination_ipc_data = profile_data_loader.db_handler.fetch_combination_data()
    profile_ipc_data = dict()

    # Load injector data for each feature type
    parallel_features = [f for f in TARGET_FEATURE if f in PARALLEL_TYPE]
    sequential_features = [f for f in TARGET_FEATURE if f in SEQUENTIAL_TYPE]
    port_features = [f for f in TARGET_FEATURE if f in PORT_TYPE]
    
    load_parallel_injector_data(profile_data_loader.db_handler, parallel_features)
    load_sequential_injector_data(profile_data_loader.db_handler, sequential_features)
    load_port_injector_data(profile_data_loader.db_handler, port_features)

    smtcheck_native.update_score_map(-1, -1, 0.0)

def load_model_data(ROOT_DIR, timestamp = None):
    global model_coef, model_intercept

    if timestamp is None:
        candidates = glob.glob(f"{ROOT_DIR}/trained_model/prediction_model_*.json")
        if not candidates:
            print(
                "[ERROR] Prediction model not found. Run profiling/live_server/generate_prediction_model.py to create model data.",
                flush=True,
            )
            sys.exit(1)
        target_timestamp = max([int(fname.split('_')[-1].split('.')[0]) for fname in candidates])
    else:
        target_timestamp = timestamp
    
    model_path = f"{ROOT_DIR}/trained_model/prediction_model_{target_timestamp}.json"
    if not os.path.exists(model_path):
        print(
            f"[ERROR] Prediction model not found at {model_path}. Run profiling/live_server/generate_prediction_model.py to create model data.",
            flush=True,
        )
        sys.exit(1)
    print(f"[INFO] Loading prediction model from {model_path}", flush=True)
    with open(model_path, 'r') as f:
        model_data = json.load(f)
        model_coef = np.array(list(map(float, model_data["coefficients"])))
        model_intercept = float(model_data["intercept"])

def add_workload(job_id):
    """
    Add a workload to the training set by loading its profile data.
    
    Args:
        job_id: Global job ID to add
    """
    global profile_ipc_data, stale_target
    
    raw_data = profile_data_loader.db_handler.fetch_profile_data(job_id)
    profile_ipc_data[job_id] = parse_profile_documents(raw_data)
    stale_target.add(job_id)

def calculate_all_characteristics():
    """
    Calculate characteristics for all loaded workloads.
    
    Returns:
        tuple: (characteristics_dict, combination_ipc_data)
            - characteristics_dict: {job_id -> [WorkloadCharacteristics for each feature]}
            - combination_ipc_data: {base_job_id -> {col_job_id -> IPC}}
    """
    global profile_ipc_data, combination_ipc_data, scale_factor_table, characteristics_dict, target_global_jobids

    for job_id in profile_ipc_data.keys():
        target_global_jobids.add(job_id)
        # Initialize characteristics list for all features
        characteristics_dict[job_id] = [WorkloadCharacteristics() for _ in TARGET_FEATURE]
        single_ipc_table[job_id] = profile_ipc_data[job_id]["single"][(0, ProcessType.WORKLOAD)]
        print(f"[DEBUG] Single IPC for job_id={job_id}: {single_ipc_table[job_id]:.4f}", flush=True)
        scale_factor_table[job_id] = profile_ipc_data[job_id]["l3_cache"][(ParallelPressureLevel.LOW, ProcessType.WORKLOAD)] / single_ipc_table[job_id]
        print(f"[DEBUG] Scale factor for job_id={job_id}: {scale_factor_table[job_id]:.4f} <- {profile_ipc_data[job_id]['l3_cache'][(ParallelPressureLevel.LOW, ProcessType.WORKLOAD)]:.4f} / {single_ipc_table[job_id]:.4f}", flush=True)
        
        for feature in TARGET_FEATURE:
            feature_idx = FEATURE_TO_INDEX[feature]
            profile_ipc = profile_ipc_data[job_id][feature]
            solo_ipc = combination_ipc_data[job_id]
            
            # Select calculator based on feature type
            if feature in PARALLEL_TYPE:
                result = calculate_parallel_characteristics(
                    profile_ipc, solo_ipc, parallel_injector_ipc[feature]
                )
            elif feature in PORT_TYPE:
                result = calculate_port_characteristics(
                    profile_ipc, solo_ipc, port_injector_ipc[feature]
                )
            elif feature in SEQUENTIAL_TYPE:
                result = calculate_sequential_characteristics(
                    profile_ipc, solo_ipc, 
                    PRESSURE_POINTS[feature],
                    sequential_injector_ipc[feature], 
                    RESOURCE_SIZE[feature_idx], 
                    WATERMARK_SIZE[feature_idx]
                )
            else:
                print(f"[WARNING] Feature '{feature}' not in any known category", flush=True)
                continue

            # Store results
            sensitivity, usage, intensity, base_slowdown = result
            characteristics_dict[job_id][feature_idx].sensitivity = sensitivity
            characteristics_dict[job_id][feature_idx].usage = usage
            characteristics_dict[job_id][feature_idx].intensity = intensity
            characteristics_dict[job_id][feature_idx].base_slowdown = base_slowdown

    profile_ipc_data = dict() # Clear loaded profile data to free memory

def expire_workload(job_id):
    """
    Remove a workload from the training set.
    
    Args:
        job_id: Global job ID to remove
    """
    global profile_data_table, target_global_jobids, characteristics_dict
    target_global_jobids.discard(job_id)
    if job_id in profile_data_table:
        del profile_data_table[job_id]
    
    if job_id in characteristics_dict:
        del characteristics_dict[job_id]

def calculate_compatibility_score(base_jobid, col_jobid):
    global model_coef, model_intercept, characteristics_dict
    base_chars = characteristics_dict[base_jobid]
    col_chars = characteristics_dict[col_jobid]
    feature_vector = [0.0 for _ in range(len(TARGET_FEATURE) + 1)]
    feature_vector[0] = min([base_chars[i].base_slowdown for i in range(len(TARGET_FEATURE))])

    for feature in TARGET_FEATURE:
        idx = FEATURE_TO_INDEX[feature]
        base_char = base_chars[idx]
        col_char = col_chars[idx]
        activation = compute_activation(base_char.usage, col_char.usage, feature)
        contention_term = base_char.sensitivity * col_char.intensity * activation
        feature_vector[1 + idx] = contention_term
    
    compatibility_score = scale_factor_table[base_jobid] * (1 - (np.dot(model_coef, feature_vector) + model_intercept))
    compatibility_score = clamp(compatibility_score, 0.0, 1.0)
    print(f"[DEBUG] Compatibility Score Calculation: base_jobid={base_jobid}, col_jobid={col_jobid}, score={compatibility_score:.4f} -> {scale_factor_table[base_jobid]:.4f} * {(1 - (np.dot(model_coef, feature_vector) + model_intercept)):.4f}", flush=True)
    return compatibility_score

def update_score_table():
    global target_global_jobids, stale_target
    print(f"[INFO] Stale targets to update scores: {stale_target}", flush=True)
    calculate_all_characteristics()

    for jobid in stale_target:
        smtcheck_native.update_single_IPC_map(jobid, single_ipc_table[jobid])
        smtcheck_native.update_score_map(jobid, -1, 1.0)

    for base_jobid, col_jobid in list(itertools.combinations(target_global_jobids, 2)) + list(zip(target_global_jobids, target_global_jobids)):
            if base_jobid not in stale_target and col_jobid not in stale_target:
                continue

            base_compat_score = calculate_compatibility_score(base_jobid, col_jobid)
            if base_jobid == col_jobid:
                col_compat_score = base_compat_score
            else:
                col_compat_score = calculate_compatibility_score(col_jobid, base_jobid)
            
            symbiotic_score = (base_compat_score + col_compat_score) 
            smtcheck_native.update_score_map(base_jobid, col_jobid, symbiotic_score)
    
    stale_target = set() # Clear stale targets after updating scores

def print_score_board():
    global target_global_jobids
    print(f"target workloads: {sorted(target_global_jobids)}", flush=True)
    print("=== Symbiotic Score Board ===", flush=True)
    SPEC_2017_RATE   = ["500.perlbench_r", "502.gcc_r", "503.bwaves_r", "505.mcf_r", "507.cactuBSSN_r", "508.namd_r", "510.parest_r", "511.povray_r", "519.lbm_r", "520.omnetpp_r", "521.wrf_r", "523.xalancbmk_r", "525.x264_r", "526.blender_r", "527.cam4_r", "531.deepsjeng_r", "538.imagick_r", "541.leela_r", "544.nab_r", "548.exchange2_r", "549.fotonik3d_r", "554.roms_r", "557.xz_r", ]
    GAP_BENCH_KERNEL = ("bfs", "cc", "pr","bc") # For database lookup
    GAP_BENCH_GRAPH  = ("urand", "road")
    GAP              = [f"{kernel}_{graph}" for kernel, graph in itertools.product(GAP_BENCH_KERNEL, GAP_BENCH_GRAPH)]

    jobid_to_name = {i: name for i, name in enumerate(SPEC_2017_RATE + GAP)}
    output_data = []
    for (base_jobid, col_jobid), score in smtcheck_native.get_score_map_py().items():
        base_name = jobid_to_name.get(base_jobid, f"base_jobid")
        col_name = jobid_to_name.get(col_jobid, f"col_jobid")
        output_data.append((base_name, col_name, score))
    max_score = max([x[2] for x in output_data]) if output_data else 1.0
    for i in range(len(output_data)):
        output_data[i] = (output_data[i][0], output_data[i][1], output_data[i][2] / max_score)
    output_data.sort(key=lambda x: (x[2]), reverse=True)
    for base_name, col_name, score in output_data:
        print(f"Workload Pair (JobID {base_name:<20}, JobID {col_name:<20}): Score = {score:.4f}", flush=True)
    print("=================================", flush=True)

if __name__ == "__main__":
    initialize()
    # Example usage
    for i in range(31):
        add_workload(i)
    calculate_all_characteristics()
    print_score_board()