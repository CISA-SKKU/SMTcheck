"""
Injector Generator Module

Generates injector programs for each target microarchitectural resource.
Injectors create controlled contention for profiling workload sensitivity.

To add a new resource:
1. Create a generator script at: injector_generator/{isa}/{resource}.py
2. The script receives (code_dir, injector_dir, sample_points, extra_data)
3. Generate injector binaries at different pressure levels
"""

import os
import sys
import subprocess
from tools import machine_data


def run_script(generator_script, code_dir, injector_dir, sample_points, extra_data):
    """
    Execute a generator script using the current Python interpreter.
    
    Args:
        generator_script: Path to the generator script
        code_dir: Directory for generated source files
        injector_dir: Directory for compiled binaries
        sample_points: Comma-separated pressure levels
        extra_data: Additional resource-specific parameters
    """
    result = subprocess.run(
        [sys.executable, generator_script, code_dir, injector_dir, sample_points, extra_data],
        capture_output=True,
        text=True,
        check=False,
    )

    print("stdout:")
    print(result.stdout)
    print("stderr:")
    print(result.stderr)
    print("returncode:", result.returncode)

def gen_sample_points(feature):
    """
    Generate sample pressure points based on resource type.
    
    Sequential-type: [1, medium, max] based on effective size and ratio
    Parallel-type: [1, 4] representing low and high contention
    Port-type: [1] only high contention level
    
    Args:
        feature: Resource feature name
    
    Returns:
        Comma-separated string of sample points
    """
    if feature in machine_data.SEQUENTIAL_TYPE:
        effective_size = machine_data.SIZE[feature] - machine_data.WATERMARK[feature]
        medium = int(effective_size * machine_data.MEDIUM_RATIO)
        sample_points = ",".join(map(str, [1, medium, effective_size]))
    elif feature in machine_data.PARALLEL_TYPE:
        sample_points = "1,4"
    else:
        sample_points = "1"
    
    return sample_points


def get_extra_data(feature):
    """
    Get additional resource-specific parameters.
    
    Sequential-type: ROB size for pipeline depth matching
    Parallel-type: Cache set size for proper addressing
    Port-type: Not used (returns "0")
    
    Args:
        feature: Resource feature name
    
    Returns:
        String containing extra parameter data
    """
    if feature in machine_data.SEQUENTIAL_TYPE:
        if feature == "uop_cache":
            return f"{machine_data.UOP_CACHE_WINDOW_SIZE},{machine_data.UOP_CACHE_NUM_SETS}"
        effective_rob_size = machine_data.SIZE["rob"] - machine_data.WATERMARK["rob"]
        return str(effective_rob_size)
    elif feature in machine_data.PORT_TYPE:
        return "0"
    elif feature in machine_data.PARALLEL_TYPE:
        unit_size = machine_data.SIZE[feature] // 2
        return str(unit_size)
    else:
        return "0"


def run_generator(isa):
    """
    Generate injector binaries for all target resources.
    
    Iterates through TARGET_FEATURE list plus L3 cache (used for scaling),
    finds corresponding generator scripts, and creates injector binaries.
    
    Args:
        isa: Instruction set architecture (e.g., "x86")
    
    Returns:
        List of features that failed to generate
    """
    failed_features = []
    
    # Include L3 cache injector for IPC scaling factor calculation
    all_features = machine_data.TARGET_FEATURE + ["l3_cache"]
    
    for feature in all_features:
        script_name = f"injector_generator/{isa}/{feature}.py"
        
        if os.path.exists(script_name):
            print(f"[INFO] Generating injector for {feature}...")

            sample_points = gen_sample_points(feature)
            extra_data = get_extra_data(feature)
            code_dir = f"code/{feature}"
            injector_dir = f"injector/{feature}"

            # Prepare output directories (clean previous files)
            os.makedirs(code_dir, exist_ok=True)
            os.system(f"rm -f {code_dir}/*")
            os.makedirs(injector_dir, exist_ok=True)
            os.system(f"rm -f {injector_dir}/*")

            run_script(script_name, code_dir, injector_dir, sample_points, extra_data)
        else:
            print(f"[WARNING] Generator script for {feature} not found.")
            failed_features.append(feature)

    if failed_features:
        print(f"[ERROR] Failed to generate injectors for: {', '.join(failed_features)}")
    
    return failed_features


if __name__ == "__main__":
    # Generate all x86 injectors
    failed_features = run_generator("x86")