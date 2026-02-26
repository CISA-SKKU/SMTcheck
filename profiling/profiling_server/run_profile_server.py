import socket
import threading
import subprocess
import os
from collections import deque
import sys
import time
import signal
import psutil
from dataclasses import dataclass
from tools.config import *
from tools.global_variable_generator import *
from tools import DBManager
from tools import perf_counter

# =============================================================================
# Constants
# =============================================================================
INJECTOR_KILL_CMD = "pkill -TERM -f '\.injector'"

# =============================================================================
# Global Variables
# =============================================================================
running = True
db_manager = None
cpu_topology = None        # socket_id -> core_id -> [logical_cpu0, logical_cpu1]
core_to_socket = None      # core_id -> socket_id
perf_counters = dict()     # core_id -> PerfCounter
request_queue = None
injector_info_list = []    # list of InjectorInfo
llc_diag_core_ids = None

# =============================================================================
# Data Classes
# =============================================================================
@dataclass
class JobState:
    """
    Tracks the execution state of a profiling job.
    
    Attributes:
        workload_core: CPU core running the target workload
        injector_core: SMT sibling core running the injector
        global_jobid: Unique identifier for this job
        l3_profiled: Whether L3 cache profiling is complete
        completed: Whether all profiling is complete
        warmup_done: Whether warmup iterations are complete
        warmup_count: Number of warmup iterations completed
        current_injector_idx: Index of current injector in the list
    """
    workload_core: int
    injector_core: int
    global_jobid: int
    l3_profiled: bool = False
    completed: bool = False
    warmup_done: bool = False
    warmup_count: int = 0
    current_injector_idx: int = 0


@dataclass
class CoreProcessInfo:
    """
    Information about a process running on a specific core.
    
    Attributes:
        global_jobid: Job ID associated with this process (-1 for injectors)
        process: Subprocess handle
        process_type: Either "workload" or "injector"
        should_terminate: Flag to mark process for termination
    """
    global_jobid: int
    process: subprocess.Popen
    process_type: str  # "workload" or "injector"
    should_terminate: bool = False

# =============================================================================
# Classes
# =============================================================================

class RequestQueue:
    """
    Thread-safe queue for managing client profiling requests.
    
    Maintains a mapping of job IDs to client connections and
    a queue of pending jobs waiting to be processed.
    """
    def __init__(self):
        self.connections = dict()       # global_jobid -> [connections]
        self.pending_jobs = deque([])   # Queue of pending job IDs
        self.lock = threading.Lock()

    def add_connection(self, global_jobid, conn):
        with self.lock:
            if global_jobid not in self.connections:
                self.connections[global_jobid] = []
                self.pending_jobs.append(global_jobid)
            self.connections[global_jobid].append(conn)

    def is_empty(self):
        with self.lock:
            return len(self.pending_jobs) == 0
        
    def pop_next_job(self):
        with self.lock:
            return self.pending_jobs.popleft()
            
    def notify_completion(self, global_jobid):
        with self.lock:
            for conn in self.connections[global_jobid]:
                conn.sendall(b"Benchmark completed")
            self.connections.pop(global_jobid, None)
        print(f"[Server] Done profiling for global_jobid {global_jobid}")


# =============================================================================
# Utility Functions
# =============================================================================
def kill_process_tree(pid, sig=signal.SIGTERM):
    """
    Terminate a process and all its children recursively.
    
    Args:
        pid: Process ID to terminate
        sig: Signal to send (default: SIGTERM)
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    children = parent.children(recursive=True)
    for child in children:
        try:
            print(f"Killing child {child.pid}")
            child.send_signal(sig)
        except psutil.NoSuchProcess:
            pass

    try:
        parent.send_signal(sig)
    except psutil.NoSuchProcess:
        pass


# =============================================================================
# CPU Topology Functions
# =============================================================================
def init_cpu_topology():
    """Parse CPU topology and store in global variable."""
    global cpu_topology
    cpu_topology = {}

    raw = os.popen("lscpu --parse=CPU,Core,Socket").read().strip().splitlines()
    lines = [x.strip() for x in raw if x.strip() and not x.startswith("#")]

    for line in lines:
        logical_cpu, core_id, socket_id = map(int, line.split(","))
        cpu_topology.setdefault(socket_id, {}).setdefault(core_id, []).append(logical_cpu)

    for socket_id in cpu_topology:
        for core_id in cpu_topology[socket_id]:
            cpu_topology[socket_id][core_id].sort()


def get_sibling_core(workload_core):
    """Return the SMT sibling core ID."""
    global cpu_topology, core_to_socket
    socket_id = core_to_socket[workload_core]
    return cpu_topology[socket_id][workload_core][1]


# =============================================================================
# Initialization Functions
# =============================================================================
def load_injector_configs():
    """Load injector configuration files."""
    global injector_info_list
    injector_info_list = []
    
    config_path = "tools/injector_exec_dir.txt"
    with open(config_path, "r") as f:
        for line in f.read().strip().split("\n"):
            parts = line.split(",")
            injector_info_list.append(InjectorInfo(
                feature=parts[0],
                pressure=int(parts[1]),
                injector_dir=parts[2],
            ))


# =============================================================================
# Performance Measurement Functions
# =============================================================================
def start_ipc_measurement(busy_cores):
    """Enable performance counters for IPC measurement."""
    global perf_counters
    
    target_cores = list(busy_cores) + [get_sibling_core(cid) for cid in busy_cores]
    for core_id in target_cores:
        perf_counters[core_id].enable_and_reset()


def stop_ipc_measurement(busy_cores):
    """Stop IPC measurement and disable counters."""
    global perf_counters
    
    target_cores = list(busy_cores) + [get_sibling_core(cid) for cid in busy_cores]
    for core_id in target_cores:
        perf_counters[core_id].disable()


def measure_ipc_for_duration(busy_cores, duration_sec):
    """Measure IPC for the specified duration."""
    start_ipc_measurement(busy_cores)
    print(f"[Server] Measuring IPC for {duration_sec} seconds...")
    time.sleep(duration_sec)
    stop_ipc_measurement(busy_cores)


def collect_ipc_results(busy_cores):
    """Collect measured IPC results."""
    global perf_counters
    
    target_cores = list(busy_cores) + [get_sibling_core(cid) for cid in busy_cores]
    
    results = {}
    for core_id in target_cores:
        results[core_id] = perf_counters[core_id].get_IPC()
    return results


# =============================================================================
# Process Execution Functions
# =============================================================================
def start_workload_process(global_jobid, core_id):
    """Start a workload process."""
    script_path = f"target_workload_runners/workload_{global_jobid}.py"
    proc = subprocess.Popen(
        f"taskset -c {core_id} python3 {script_path}".split(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    return proc


def run_l3_injector(core_to_process, busy_cores):
    """Run L3 cache injector for scaling factor measurement."""
    l3_injector_path = "injector/l3_cache/l3_cache.high.injector"
    print("[Server] Running L3 injector")

    for workload_core in llc_diag_core_ids:
        injector_core = get_sibling_core(workload_core)
        process = subprocess.Popen(
            ["/usr/bin/taskset", "-c", str(injector_core), l3_injector_path, "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    
    measure_ipc_for_duration(busy_cores, SAMPLING_TIME)
    os.system(INJECTOR_KILL_CMD)


def run_injectors_for_profiling(active_jobs, core_to_process, busy_cores):
    """Run injectors for workload profiling."""
    global injector_info_list
    print("[Server] Running profile per core =>")
    
    for job_state in active_jobs.values():
        if not job_state.warmup_done or job_state.completed:
            continue

        injector_info = injector_info_list[job_state.current_injector_idx]
        injector_core = job_state.injector_core

        process = subprocess.Popen(
            ["/usr/bin/taskset", "-c", str(injector_core), injector_info.injector_dir, "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        core_to_process[injector_core] = CoreProcessInfo(
            global_jobid=job_state.global_jobid,
            process=process,
            process_type="injector",
            should_terminate=True,
        )

    measure_ipc_for_duration(busy_cores, SAMPLING_TIME)
    os.system(INJECTOR_KILL_CMD)


# =============================================================================
# Result Processing Functions
# =============================================================================
def process_l3_results(ipc_results, active_jobs, core_to_process, db_manager):
    """Process L3 injector measurement results."""
    completed_jobs = []
    
    for core_id, ipc in ipc_results.items():
        if core_id not in core_to_process:
            continue
        global_jobid = core_to_process[core_id].global_jobid
        if global_jobid == -1:
            continue
            
        job_state = active_jobs[global_jobid]
        
        output = DBManager.wrap_data_for_db(
            feature="l3_cache",
            global_jobid=global_jobid,
            pressure=0,
            run_type="workload",
            IPC=ipc)
        db_manager.send_data(output)

        job_state.l3_profiled = True
        if job_state.completed:
            core_to_process[core_id].should_terminate = True
            completed_jobs.append(global_jobid)
    
    return completed_jobs


def process_normal_results(ipc_results, active_jobs, core_to_process, db_manager):
    """Process regular profiling results."""
    global injector_info_list
    completed_jobs = []
    jobs_to_advance = []
    
    for core_id, ipc in ipc_results.items():
        if core_id not in core_to_process:
            print(f"[Warning] Core {core_id} not found in core_to_process")
            continue
            
        global_jobid = core_to_process[core_id].global_jobid
        job_state = active_jobs[global_jobid]

        if job_state.completed:
            continue

        injector_info = injector_info_list[job_state.current_injector_idx]
        feature = injector_info.feature
        pressure = injector_info.pressure
        
        print(f"[Server] Processing IPC result for Core {core_id:2d}, global_jobid {global_jobid:3d}: "
              f"IPC={ipc:.6f} feature={feature} pressure={pressure}")

        # Handle warmup phase
        if not job_state.warmup_done:
            job_state.warmup_count += 1
            if job_state.warmup_count >= WARMUP_COUNT:
                job_state.warmup_done = True
                output = DBManager.wrap_data_for_db("single", global_jobid, 0, "workload", ipc)
                db_manager.send_data(output)
            continue

        # Handle workload/injector results
        process_type = core_to_process[core_id].process_type
        if process_type == "workload":
            jobs_to_advance.append((job_state, core_id))
            output = DBManager.wrap_data_for_db(feature, global_jobid, pressure, "workload", ipc)
        else:
            output = DBManager.wrap_data_for_db(feature, global_jobid, pressure, "injector", ipc)
        db_manager.send_data(output)

    # Advance to next injector
    for job_state, core_id in jobs_to_advance:
        job_state.current_injector_idx += 1
        if job_state.current_injector_idx == len(injector_info_list):
            job_state.completed = True
            if job_state.l3_profiled:
                core_to_process[core_id].should_terminate = True
                completed_jobs.append(job_state.global_jobid)
    
    return completed_jobs


def process_measurement_results(active_jobs, core_to_process, is_l3_phase, db_manager, busy_cores):
    """Main function for processing measurement results."""
    ipc_results = collect_ipc_results(busy_cores)

    if is_l3_phase:
        return process_l3_results(ipc_results, active_jobs, core_to_process, db_manager)
    else:
        return process_normal_results(ipc_results, active_jobs, core_to_process, db_manager)


# =============================================================================
# Scheduling Functions
# =============================================================================
def check_and_start_workloads(active_jobs, core_to_process):
    """
    Start workloads and check conditions for L3 injector execution.
    Returns: Whether L3 injector should be run.
    """
    should_run_l3 = True
    jobs_needing_l3 = 0
    
    for job_state in active_jobs.values():
        if job_state.warmup_count == 0:
            # Start new workload
            print(f"[Server] Starting workload process for global_jobid {job_state.global_jobid}")
            proc = start_workload_process(job_state.global_jobid, job_state.workload_core)
            core_to_process[job_state.workload_core] = CoreProcessInfo(
                global_jobid=job_state.global_jobid,
                process=proc,
                process_type="workload",
                should_terminate=False
            )
        elif job_state.l3_profiled:
            should_run_l3 = False

        if job_state.warmup_done and not job_state.l3_profiled:
            jobs_needing_l3 += 1

    if jobs_needing_l3 == 0:
        should_run_l3 = False
    
    return should_run_l3


def schedule_pending_requests(active_jobs, request_queue, available_cores, busy_cores):
    """Assign pending requests to available cores."""
    while not request_queue.is_empty() and available_cores:
        workload_core = available_cores.popleft()
        injector_core = get_sibling_core(workload_core)
        global_jobid = request_queue.pop_next_job()
        busy_cores.add(workload_core)

        active_jobs[global_jobid] = JobState(
            workload_core=workload_core,
            injector_core=injector_core,
            global_jobid=global_jobid,
        )
        print(f"[Server] Scheduled new request for global_jobid {global_jobid} => "
              f"workload_core: {workload_core}, injector_core: {injector_core}")


# =============================================================================
# Network Functions
# =============================================================================
def run_accept_thread():
    """Thread that accepts client connections."""
    global request_queue
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    server.settimeout(1.0)
    print(f"[Server] Listening on {HOST}:{PORT}")

    while running:
        try:
            conn, addr = server.accept()
        except socket.timeout:
            continue
        
        data = conn.recv(1024)
        global_jobid = int(data.decode().strip())
        request_queue.add_connection(global_jobid, conn)
        print(f"[Server] Accepted connection from {addr} => global_jobid={global_jobid}")

    server.close()
    print("[Server] Stopped")


# =============================================================================
# Cleanup Functions
# =============================================================================
def cleanup_completed_job(job_state, core_to_process, available_cores, busy_cores):
    """Clean up completed job resources."""
    workload_core = job_state.workload_core
    injector_core = job_state.injector_core
    global_jobid = job_state.global_jobid

    # Release cores
    available_cores.append(workload_core)
    busy_cores.remove(workload_core)

    # Notify completion
    request_queue.notify_completion(global_jobid)
    db_manager.send_done(global_jobid)

    # Terminate workload process
    proc = core_to_process[workload_core].process
    proc.terminate()
    proc.wait()

    # Cleanup
    core_to_process.pop(workload_core, None)
    core_to_process.pop(injector_core, None)


# =============================================================================
# Main Function
# =============================================================================
def main(db_manager):
    global cpu_topology, perf_counters, request_queue, core_to_socket, injector_info_list, llc_diag_core_ids
    
    core_to_process = {}      # core_id -> CoreProcessInfo
    core_to_socket = {}       # core_id -> socket_id
    active_jobs = {}          # global_jobid -> JobState

    available_cores = []
    busy_cores = set()
    
    # Initialize available cores
    for socket_id in cpu_topology:
        max_cores = int(len(cpu_topology[socket_id]) * MAXIMUM_UTIL)
        count = 0
        for core_id in cpu_topology[socket_id]:
            available_cores.append(core_id)
            core_to_socket[core_id] = socket_id
            count += 1
            if count >= max_cores:
                break

    # Initialize performance counters
    for core_id in available_cores:
        perf_counters[core_id] = perf_counter.PerfCounter(core_id)
        socket_id = core_to_socket[core_id]
        sibling_core = cpu_topology[socket_id][core_id][1]
        perf_counters[sibling_core] = perf_counter.PerfCounter(sibling_core)
        core_to_socket[sibling_core] = socket_id
    
    print(f"[Server] Available cores: {available_cores}")
    available_cores = deque(available_cores)
    llc_diag_core_ids = tuple(available_cores)

    # Main loop
    while True:
        schedule_pending_requests(active_jobs, request_queue, available_cores, busy_cores)

        if not active_jobs:
            time.sleep(1)
            continue
        
        is_l3_phase = check_and_start_workloads(active_jobs, core_to_process)

        print(f"[Server] Busy cores: {busy_cores}")
        if is_l3_phase:
            run_l3_injector(core_to_process, busy_cores)
        else:
            run_injectors_for_profiling(active_jobs, core_to_process, busy_cores)

        completed_jobs = process_measurement_results(
            active_jobs, core_to_process, is_l3_phase, db_manager, busy_cores
        )
        
        for global_jobid in completed_jobs:
            job_state = active_jobs.pop(global_jobid, None)
            if job_state:
                cleanup_completed_job(job_state, core_to_process, available_cores, busy_cores)


# =============================================================================
# Entry Point
# =============================================================================
try:
    if __name__ == "__main__":
        db_manager = DBManager.DBManager()
        request_queue = RequestQueue()
        init_cpu_topology()
        load_injector_configs()
        
        accept_thread = threading.Thread(target=run_accept_thread)
        accept_thread.start()
        main(db_manager)

finally:
    running = False
    if db_manager:
        db_manager.close()