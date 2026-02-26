from . import machine_data
import os
import subprocess

def read_injector_list():
    injector_exec_dir = "tools/injector_exec_dir.txt"
    with open(injector_exec_dir, "r") as file:
        injectors = [line.strip() for line in file.readlines()]
    return injectors

def parse_IPC(output):
    for line in output.split("\n"):
        line = line.split(": ")

        if line[0] == "IPC":
            return float(line[1])
    return 0.0

# - global_jobid is defined as
#   single: -1, low: -2, high: -3
# - pressure is defined as
#   single => 0
#   sequential_type => low: 0, medium: 1, high: 2
#   parallel_type => low: 0, high: 1
counter = dict()
def run_injector(injector, feature, output_file_name, core_id, global_jobid, single_output_metadata):
    global counter
    result = subprocess.run(["timeout", "-s", "SIGINT", f"{machine_data.SAMPLING_INTERVAL}s", "taskset", "-c", str(core_id), injector],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    injector_name = injector.split("/")[-1]

    if result.returncode == 124:
        with open(output_file_name, "w") as out_file:
            print(result.stdout, file=out_file)
    else:
        with open(output_file_name, "w") as out_file:
            print(result.stdout, file=out_file)
            print(result.stderr, file=out_file)
        print(f"[ERROR] Injector {injector_name} for feature {feature} failed with return code {result.returncode}")

    if (feature, global_jobid) not in counter:
        counter[(feature, global_jobid)] = 0
    pressure = counter[(feature, global_jobid)]
    single_output_metadata.append((feature, global_jobid, pressure, parse_IPC(result.stdout), injector))

    counter[(feature, global_jobid)] += 1

def profile_injector(injector, feature, output_dir, core_ids, single_output_metadata):
    injector_dir = os.path.dirname(injector)
    for col_type, global_jobid in zip(["low", "high"], [-2, -3]):
        col_injector = f"{injector_dir}/{feature}.{col_type}.injector"
        if not os.path.exists(col_injector):
            print(f"[WARNING] Co-located injector {col_injector} does not exist. Skipping profiling for {feature} {col_type}.")
            continue
        
        process = subprocess.Popen(["taskset", "-c", str(core_ids[1]), col_injector],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output_file_name = f"{output_dir}/{col_type}-{injector.split('/')[-1].replace('.injector', '.out')}"
        run_injector(injector, feature, output_file_name, core_ids[0], global_jobid, single_output_metadata)

        process.terminate()
        process.wait()

def run_injectors(injectors, output_root_dir, core_ids):
    dir_initialized = set()
    single_output_metadata = []

    for injector in injectors:
        print(f"[INFO] Running injector: {injector}")
        feature = injector.split("/")[1]
        core_id = core_ids[0]
        output_dir = f"{output_root_dir}/{feature}"
        if output_dir not in dir_initialized:
            os.makedirs(output_dir, exist_ok=True)
            os.system(f"rm -rf {output_dir}/*")
            dir_initialized.add(output_dir)

        output_file_name = f"{output_dir}/single-{injector.split('/')[-1].replace('.injector', '.out')}"
        run_injector(injector, feature, output_file_name, core_id, -1, single_output_metadata)
        if feature in machine_data.PARALLEL_TYPE:
            profile_injector(injector, feature, output_dir, core_ids, single_output_metadata)

    return single_output_metadata