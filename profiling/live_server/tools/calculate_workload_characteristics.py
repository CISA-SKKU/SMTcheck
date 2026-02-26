"""
Workload Characteristics Calculator

This module calculates workload characteristics (sensitivity, usage, intensity, base_slowdown)
for each target feature by analyzing profiling data from MongoDB.

The characteristics are used to train a prediction model for SMT interference estimation.
"""

from pymongo import MongoClient
from collections import defaultdict
from enum import IntEnum
from .global_variable_generator import *
from .machine_data import *

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

# Database handler
db_handler = None


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


# =============================================================================
# Database Handler
# =============================================================================
class DatabaseHandler:
    """Handles MongoDB connections and queries for profiling data"""
    
    def __init__(self, node_name, connection_string):
        self.client = MongoClient(connection_string)
        self.db = self.client["profile_data"]
        self.combination_collection = self.db["combination"]
        self.measurement_collection = self.db["measurement"]
        self.node_name = node_name

    def fetch_profile_data(self, job_id):
        """Fetch all measurement documents for a specific job"""
        query = {
            "node_name": self.node_name,
            "global_jobid": job_id
        }
        return self.measurement_collection.find(query)

    def fetch_combination_data(self):
        """
        Fetch combination IPC data (pairwise workload measurements).
        Returns: dict mapping base_job_id -> {col_job_id -> IPC}
        """
        query = {"node_name": self.node_name}
        doc = self.combination_collection.find_one(query)
        
        result = dict()
        for base_key, value in doc["data"].items():
            base_job_id = int(base_key)
            result[base_job_id] = dict()
            for col_key, ipc in value.items():
                if col_key == "single":
                    result[base_job_id]["single"] = ipc
                else:
                    result[base_job_id][int(col_key)] = ipc
        return result
    
    def close(self):
        self.client.close()


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
def load_parallel_injector_data(db_handler, parallel_features):
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
                          f"pressure={pressure}, found {len(docs)}")
                    continue
                
                ipc = docs[0]["IPC"]
                parallel_injector_ipc[feature][config_name][pressure] = ipc
                print(f"[DEBUG] Parallel injector: feature={feature}, config={config_name}, "
                      f"pressure={pressure}, IPC={ipc:.4f}")


def load_sequential_injector_data(db_handler, sequential_features):
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
                      f"found {len(docs)}")
                continue
            
            ipc = docs[0]["IPC"]
            sequential_injector_ipc[feature]["single"][pressure] = ipc

    
def load_port_injector_data(db_handler, port_features):
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
            print(f"[WARNING] Expected 1 document for {feature}, found {len(docs)}")
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
    
    Sequential resources (issue queues, uop cache) have three pressure levels and
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
    
    Parallel resources (L1D, L2, L1 DTLB) have two pressure levels and
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
    global combination_ipc_data, profile_ipc_data, db_handler

    db_handler = DatabaseHandler(node_name=NODE_NAME, connection_string="mongodb://192.168.0.13:27017")
    combination_ipc_data = db_handler.fetch_combination_data()
    profile_ipc_data = dict()

    # Load injector data for each feature type
    parallel_features = [f for f in TARGET_FEATURE if f in PARALLEL_TYPE]
    sequential_features = [f for f in TARGET_FEATURE if f in SEQUENTIAL_TYPE]
    port_features = [f for f in TARGET_FEATURE if f in PORT_TYPE]
    
    load_parallel_injector_data(db_handler, parallel_features)
    load_sequential_injector_data(db_handler, sequential_features)
    load_port_injector_data(db_handler, port_features)


def add_workload(job_id):
    """
    Add a workload to the training set by loading its profile data.
    
    Args:
        job_id: Global job ID to add
    """
    global profile_ipc_data, db_handler
    
    raw_data = db_handler.fetch_profile_data(job_id)
    profile_ipc_data[job_id] = parse_profile_documents(raw_data)


def calculate_all_characteristics():
    """
    Calculate characteristics for all loaded workloads.
    
    Returns:
        tuple: (characteristics_dict, combination_ipc_data)
            - characteristics_dict: {job_id -> [WorkloadCharacteristics for each feature]}
            - combination_ipc_data: {base_job_id -> {col_job_id -> IPC}}
    """
    global profile_ipc_data, combination_ipc_data

    characteristics_dict = dict()

    for job_id in profile_ipc_data.keys():
        # Initialize characteristics list for all features
        characteristics_dict[job_id] = [WorkloadCharacteristics() for _ in TARGET_FEATURE]
        
        # Re-fetch profile data (in case it was updated)
        raw_data = db_handler.fetch_profile_data(job_id)
        profile_ipc_data[job_id] = parse_profile_documents(raw_data)
        
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
                print(f"[WARNING] Feature '{feature}' not in any known category")
                continue

            # Store results
            sensitivity, usage, intensity, base_slowdown = result
            characteristics_dict[job_id][feature_idx].sensitivity = sensitivity
            characteristics_dict[job_id][feature_idx].usage = usage
            characteristics_dict[job_id][feature_idx].intensity = intensity
            characteristics_dict[job_id][feature_idx].base_slowdown = base_slowdown
    
    return characteristics_dict, combination_ipc_data