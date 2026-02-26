import subprocess
import json
import time
from .config import *
from .global_variable_generator import *
from . import perf_counter
from pymongo import MongoClient

WARMUP_TIME_SEC = SAMPLING_TIME * WARMUP_COUNT

result = subprocess.run("sudo cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list", shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
cids  = list(map(int, result.stdout.strip().split(",")))

measure_value_dict = dict()
perf_counters = dict()     # core_id -> PerfCounter

def start_workload_process(global_jobid, core_id):
    """Start a workload process pinned to the specified core."""
    script_path = f"target_workload_runners/workload_{global_jobid}.py"
    proc = subprocess.Popen(
        f"taskset -c {core_id} python3 {script_path}".split(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    return proc

def measure_alone(global_jobid, cids, duration_sec):
    measure_value_dict[global_jobid] = dict()
    # Measure single-threaded IPC (workload alone on one core)
    proc = start_workload_process(global_jobid, cids[0])

    time.sleep(WARMUP_TIME_SEC)

    perf_counters[cids[0]].enable_and_reset()
    time.sleep(duration_sec)
    perf_counters[cids[0]].disable()

    proc.terminate()

    measure_value_dict[global_jobid]["single"] = round(perf_counters[cids[0]].get_IPC(), 6)

    # Measure same-workload co-run IPC (identical workload on both cores)
    proc_list = []

    if global_jobid in multi_threaded_workloads:
        proc = start_workload_process(global_jobid, ",".join(map(str, cids)))
        proc_list.append(proc)
    else:
        for cid in cids:
            proc = start_workload_process(global_jobid, cid)
            proc_list.append(proc)
    
    time.sleep(WARMUP_TIME_SEC)
    for cid in cids:
        perf_counters[cid].enable_and_reset()
    time.sleep(duration_sec)
    for cid in cids:
        perf_counters[cid].disable()
    
    for proc in proc_list:
        proc.terminate()
    
    total_ipc = 0.0
    for cid in cids:
        total_ipc += perf_counters[cid].get_IPC()
    measure_value_dict[global_jobid][global_jobid] = round(total_ipc / len(cids), 2)

def measure_combination(global_jobids, cids, duration_sec):
    proc_list = []
    for jobid, cid in zip(global_jobids, cids):
        proc = start_workload_process(jobid, cid)
        proc_list.append(proc)
    
    time.sleep(WARMUP_TIME_SEC)
    for cid in cids:
        perf_counters[cid].enable_and_reset()
    time.sleep(duration_sec)
    for cid in cids:
        perf_counters[cid].disable()

    for proc in proc_list:
        proc.terminate()

    measure_value_dict[global_jobids[0]][global_jobids[1]] = round(perf_counters[cids[0]].get_IPC(), 6)
    measure_value_dict[global_jobids[1]][global_jobids[0]] = round(perf_counters[cids[1]].get_IPC(), 6)

def measure():
    print(f"Socket 0, Core 0 logical CPUs: {cids}")
    print("Training workload jobids:", training_jobid_list)

    for cid in cids:
        perf_counters[cid] = perf_counter.PerfCounter(cid)
    
    for jobid in training_jobid_list:
        print(f"Measuring alone for workload {jobid}...")
        measure_alone(jobid, cids, SAMPLING_TIME)
    
    for i in range(len(training_jobid_list)):
        for j in range(i+1, len(training_jobid_list)):
            jobid1 = training_jobid_list[i]
            jobid2 = training_jobid_list[j]
            # Skip pairing two multi-threaded workloads. — not a fundamental limitation.
            if jobid1 in multi_threaded_workloads and jobid2 in multi_threaded_workloads:
                continue
            print(f"Measuring combination for workloads {jobid1} and {jobid2}...")
            measure_combination([jobid1, jobid2], [cids[0], cids[1]], SAMPLING_TIME)

    with open("tools/combination_measurement_result.json", "w") as f:
        json.dump(measure_value_dict, f, indent=4)

def push_results():

    # 1. Load JSON measurement results
    with open("tools/combination_measurement_result_temp.json", "r") as f:
        measure_value_dict = json.load(f)

    assert isinstance(measure_value_dict, dict)
    assert len(measure_value_dict) > 0

    # 2. Connect to MongoDB
    client = MongoClient(DB_SERVER)
    db = client["profile_data"]
    collection = db["combination"]

    # 3. Update or insert (store all results in one document)
    collection.update_one(

        {"node_name": NODE_NAME},
        {
            "$set": {
                "node_name": NODE_NAME,          # Include field for upsert
                "data": measure_value_dict,      # Store under "data" field
            }
        },
        upsert=True
    )

    client.close()
