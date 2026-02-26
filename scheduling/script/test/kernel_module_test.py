"""
Kernel Module Test Script

This script tests the runtime_monitor and IPC_monitor kernel modules by:
1. Spawning dummy processes and registering them with the runtime_monitor
2. Reading IPC (Instructions Per Cycle) data from the shared memory
3. Verifying that the kernel modules correctly track process performance

Usage:
    sudo python kernel_module_test.py

Prerequisites:
    - IPC_monitor.ko and runtime_monitor.ko modules must be loaded
    - Script must be run with root privileges

Output:
    - Prints IPC data for each active slot in shared memory
    - Shows cycles, instructions, and calculated IPC for registered processes
"""

import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
    
import fcntl
import struct
import time
import signal
import multiprocessing as mp
from userlevel.python.smtcheck.c_struct import *

# File descriptors for kernel module device files
fd_runtime_monitor = os.open("/dev/runtime_monitor", os.O_RDWR)
fd_ipc_monitor = os.open("/dev/IPC_monitor", os.O_RDWR)

# Shared memory manager for reading IPC data from kernel
shared_memory_manager = SharedMemoryManager()
shared_memory_manager.map()

# Netlink protocol number for kernel-userspace communication
NETLINK_USER = 31
kernel_socket = None

# List of spawned dummy processes for cleanup
dummy_process_list: list[mp.Process] = []

# CPU core to pin dummy processes to
pinned_cpu_core = 0

def dummy_worker():
    """Worker function that runs in a separate process group and spins forever."""
    os.setsid()   # Create new session to separate from parent process group
    os.sched_setaffinity(0, {pinned_cpu_core})  # Pin to specified CPU core (0 = current process)
    while True:
        pass

def spawn_dummy_processes(num_processes):
    """Spawn dummy worker processes for testing.
    
    Args:
        num_processes: Number of dummy processes to spawn
    """
    global dummy_process_list
    for _ in range(num_processes):
        process = mp.Process(target=dummy_worker, daemon=False)
        process.start()
        dummy_process_list.append(process)

def set_long_running_threshold(threshold_seconds=10):
    """Set the threshold for long-running process detection.
    
    Args:
        threshold_seconds: Time in seconds after which a process is considered long-running
    """
    fcntl.ioctl(fd_runtime_monitor, RTMON_IOC_SET_THRESHOLD, struct.pack("i", threshold_seconds))

def test_runtime_monitor(num_processes=1, sleep_duration=10):
    """Test the runtime_monitor kernel module.
    
    Spawns dummy processes, registers them with the runtime_monitor,
    and waits for the specified duration.
    
    Args:
        num_processes: Number of dummy processes to spawn
        sleep_duration: Time to wait before cleanup (in seconds)
    """
    spawn_dummy_processes(num_processes)
    for process in dummy_process_list:
        pid = process.pid
        result = fcntl.ioctl(fd_runtime_monitor, RTMON_IOC_ADD_PGID, struct.pack("iii", pid, 0, num_processes))
        print(f"Added PGID {pid} to runtime_monitor, ioctl result: {result}")
    time.sleep(sleep_duration)

def read_slot_with_seqlock(slot):
    """Read slot data using seqlock protocol to ensure consistency.
    
    The kernel writes slots using a sequence counter that is odd during writes.
    We spin until we get a consistent read (same even sequence before and after).
    
    Args:
        slot: The shared memory slot to read
        
    Returns:
        tuple: (pgid, cycles, instructions)
    """
    while True:
        seq_before = slot.seq
        if seq_before & 1:
            continue  # Writer in progress, retry
        pgid = slot.pgid
        cycles = slot.cycles
        instructions = slot.instructions
        seq_after = slot.seq
        if seq_before == seq_after and not (seq_after & 1):
            return pgid, cycles, instructions

def test_ipc_monitor(target_pgid=None):
    """Test the IPC_monitor kernel module by reading active slots.
    
    Reads all active slots from shared memory and prints IPC data.
    If target_pgid is specified, only shows data for that PGID.
    
    Args:
        target_pgid: Optional PGID to filter results (None = show all)
    """
    slots_found = 0

    for slot in shared_memory_manager.active_slots:
        pgid, cycles, instructions = read_slot_with_seqlock(slot)
        print(f"Read slot PGID={pgid}, Cycles={cycles}, Instructions={instructions}")

        # Skip slots with invalid PGID (may occur due to timing during updates)
        if pgid <= 0:
            continue
        if target_pgid is not None and pgid != target_pgid:
            continue

        ipc = (instructions / cycles) if cycles > 0 else 0.0
        print(f"[Slot PGID={pgid}] Cycles={cycles}, Instructions={instructions}, IPC={ipc:.3f}")
        slots_found += 1

    if slots_found == 0:
        print("No matching active slots found.")
    else:
        # Reset counters after reading (using _IO ioctl with dummy argument)
        fcntl.ioctl(fd_ipc_monitor, IPC_IOC_RESET_COUNTERS, struct.pack("i", 0))
    
# =============================================================================
# Main Test Execution
# =============================================================================
if __name__ == "__main__":
    # Set long-running threshold to 10 seconds
    set_long_running_threshold(10)
    
    # Start runtime monitor test with 1 process, wait 20 seconds for it to become "long-running"
    test_runtime_monitor(num_processes=1, sleep_duration=20)
    
    # Read IPC data 10 times with 2-second intervals
    for _ in range(10):
        test_ipc_monitor()
        time.sleep(2)

    # Cleanup: close file descriptor and terminate dummy processes
    os.close(fd_runtime_monitor)
    for process in dummy_process_list:
        os.kill(process.pid, signal.SIGTERM)
        process.join()
    
    print("Test completed successfully.")