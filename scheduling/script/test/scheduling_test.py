"""
Full Scheduling Integration Test

This script provides a complete integration test of the SMTcheck scheduling system by:
1. Listening for kernel events when long-running processes are detected
2. Requesting profiling for new workloads from the profiling server
3. Updating compatibility scores as profile data becomes available
4. Running the SMT-aware scheduler to optimize CPU affinity

Usage:
    sudo python scheduling_test.py

Prerequisites:
    - IPC_monitor.ko and runtime_monitor.ko kernel modules must be loaded
    - MongoDB server must be running with profile data
    - Profiling server should be running (optional, can be disabled)
    - Trained prediction model must exist in trained_model/
    - Script must be run with root privileges

Output:
    - Netlink events as long-running processes are detected
    - Profiling request status and completion
    - Updated compatibility scores
    - Scheduling decisions and CPU affinity assignments

Architecture:
    The test runs three main components:
    1. Main thread: Listens for kernel netlink events
    2. ThreadPoolExecutor: Handles async profiling requests
    3. Drain thread: Processes completed profiles and triggers rescheduling
"""

import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import userlevel.python.smtcheck.profile_data_loader as profile_data_loader
import userlevel.python.smtcheck.score_updater as score_updater
import userlevel.python.smtcheck.smtcheck_native as smtcheck_native
from userlevel.python.smtcheck.c_struct import *

from concurrent.futures import ThreadPoolExecutor
import time
import queue
import threading
import fcntl
import struct
import traceback
from collections import defaultdict

# File descriptor for runtime_monitor device
fd_runtime_monitor = os.open("/dev/runtime_monitor", os.O_RDWR)

# =============================================================================
# Pending Request Management
# =============================================================================

# Dictionary tracking pending profiling requests by job ID
# Maps: job_id -> list of PGIDs waiting for profiling
pending_profile_requests: dict[int, list[int]] = dict()
pending_requests_lock = threading.Lock()

# Queue for completed profiling requests (job_id, exception or None)
completed_requests_queue: "queue.Queue[tuple[int, Exception|None]]" = queue.Queue()

# =============================================================================
# First-Touch Emulation via Request Counter
# =============================================================================
# 
# Problem: When a process first exceeds the long-running threshold, it is NOT
# yet registered with IPC_monitor. The kernel only sends a profiling request
# on first detection. After profiling completes and userspace sends ACK, the
# kernel registers the process with IPC_monitor on the NEXT detection event.
#
# This creates a "first touch" pattern:
#   1st event: Process detected as long-running → request profiling (no IPC registration)
#   2nd event: Profiling complete, ACK sent → register with IPC_monitor
#
# To emulate this behavior in our test, we use a counter:
#   - counter == 1: First touch - profiling requested but not yet complete
#   - counter >= 2: Subsequent touches - can register with IPC_monitor
#
# This allows us to properly synchronize score updates with IPC monitoring.
# =============================================================================
first_touch_counter: dict[int, int] = dict()

def submit_profiling_request(job_id: int):
    """Worker function to submit a profiling request for a job.
    
    This is executed in a ThreadPoolExecutor to avoid blocking the main loop.
    Results are placed in completed_requests_queue for processing.
    
    Args:
        job_id: The global job ID to request profiling for
    """
    try:
        # Note: Actual profiling request is disabled for fast testing
        # Uncomment the following line to enable:
        # profile_data_loader.send_profiling_request(job_id)
        print(f"[Profiling Request] job_id={job_id} (simulated)", flush=True)
        completed_requests_queue.put((job_id, None))

    except Exception as e:
        completed_requests_queue.put((job_id, e))

def register_pending_request(job_id: int, pgid: int) -> bool:
    """Register a PGID as pending profiling for a job ID.
    
    Args:
        job_id: The global job ID
        pgid: The process group ID to register
        
    Returns:
        True if this is a new job (first registration), False if already pending
    """
    with pending_requests_lock:
        if job_id in pending_profile_requests:
            pending_profile_requests[job_id].append(pgid)
            return False
        pending_profile_requests[job_id] = [pgid]
        return True

def notify_kernel_profiling_complete(job_id: int):
    """Notify the kernel that profiling is complete for all PGIDs of a job.
    
    Sends netlink messages to the kernel for each PGID associated with the job,
    allowing the kernel to proceed with IPC monitoring registration.
    
    Note: Only sends ACK on second touch (counter > 1) because:
    - First touch: Process just became long-running, profiling not yet complete
    - Second touch: Profiling is complete, safe to register with IPC_monitor
    
    Args:
        job_id: The global job ID whose profiling is complete
    """
    print(f"[Notify Kernel] Profiling complete for job_id={job_id}", flush=True)
    
    with pending_requests_lock:
        if job_id not in pending_profile_requests:
            print(f"[Warning] notify_kernel_profiling_complete: job_id={job_id} not found in pending requests.")
            return
            
        touch_count = first_touch_counter.get(job_id, 0)
        pgid_list = pending_profile_requests[job_id]
        
        if touch_count > 1:
            # Second touch or later: Send ACK to kernel for IPC registration
            for pgid in pgid_list:
                msg = profile_data_loader.make_msg(pgid)
                profile_data_loader.kernel_sock.sendto(msg, (0, 0))
                print(f"[Profiling Complete] Notified kernel for PGID: {pgid} (job_id={job_id})", flush=True)
        else:
            # First touch: Just log, no ACK sent
            print(f"[First Touch] job_id={job_id} - clearing {len(pgid_list)} PGIDs from pending (no ACK)", flush=True)
        
        # Always clear pending list after processing (both first touch and later)
        pending_profile_requests[job_id] = []

def process_completed_requests_thread():
    """Background thread that processes completed profiling requests.
    
    This thread:
    1. Waits for completed profiling requests from the queue
    2. Tracks first-touch state via counter
    3. Adds workloads to score updater (only after first touch)
    4. Notifies the kernel that profiling is complete
    5. Periodically updates the score table and triggers rescheduling
    """
    while True:
        print("[Drain Thread] Waiting for completed profiling requests...", flush=True)
        job_id, error = completed_requests_queue.get()  # Blocking wait
        print(f"[Drain Thread] Processing completed profiling request for job_id={job_id}", flush=True)

        requests_processed = 0

        def handle_completed_request(job_id: int, error: Exception | None):
            """Process a single completed profiling request."""
            nonlocal requests_processed
            
            # Update first-touch counter
            if job_id not in first_touch_counter:
                first_touch_counter[job_id] = 1
                print(f"[First Touch] job_id={job_id} - initial detection, profiling started")
            else:
                first_touch_counter[job_id] += 1
                # Only add to score updater after first touch (profiling complete)
                score_updater.add_workload(job_id)
                
                if error is None:
                    print(f"[Profiling Done] job_id={job_id} (touch #{first_touch_counter[job_id]})")
                else:
                    print(f"[Profiling Failed] job_id={job_id}, error={error}")

            notify_kernel_profiling_complete(job_id)
            completed_requests_queue.task_done()
            requests_processed += 1

        handle_completed_request(job_id, error)

        # Wait to allow more requests to batch up before processing
        print("[Drain Thread] Waiting 5 seconds to batch additional requests...", flush=True)
        time.sleep(5)

        # Drain any additional completed requests that arrived during the wait
        while True:
            try:
                job_id, error = completed_requests_queue.get_nowait()
            except queue.Empty:
                break
            handle_completed_request(job_id, error)
        
        print(f"[Drain Thread] Processed {requests_processed} requests in this batch.", flush=True)

        # Update scores for all batched completions
        time.sleep(5)
        print(f"[Score Update] Updating scores for {requests_processed} completed profiles...", flush=True)
        score_updater.update_score_table()
        print(f"[Score Update] Done. Triggering reschedule.")
        smtcheck_native.schedule()

def set_long_running_threshold(threshold_seconds: int = 10):
    """Set the kernel's long-running process detection threshold.
    
    Args:
        threshold_seconds: Time in seconds after which a process is considered long-running
    """
    fcntl.ioctl(fd_runtime_monitor, RTMON_IOC_SET_THRESHOLD, struct.pack("i", threshold_seconds))


# =============================================================================
# Main Entry Point
# =============================================================================
if __name__ == "__main__":
    # Initialize all modules
    profile_data_loader.initialize()
    score_updater.initialize()
    score_updater.load_model_data(ROOT)
    
    # Set long-running threshold to 60 seconds for testing
    set_long_running_threshold(60)
    
    # Open shared memory for IPC monitoring
    smtcheck_native.open_mmap()
    smtcheck_native.set_sibling_core_map(profile_data_loader.sibling_core_dict)

    # Start background thread for processing completed profiling requests
    completed_requests_thread = threading.Thread(target=process_completed_requests_thread, daemon=True)
    completed_requests_thread.start()

    print("[Scheduler] Initialization complete. Listening for kernel events...")
    print("[Scheduler] Press Ctrl+C to stop.")
    
    # Main event loop with thread pool for async profiling
    with ThreadPoolExecutor(max_workers=32) as executor:
        while True:
            try:
                # Block until kernel sends a netlink message about a process
                pgid, global_job_id = profile_data_loader.netlink_listener()
                print(f"[Kernel Event] PGID={pgid}, job_id={global_job_id}")

                if global_job_id is not None and global_job_id >= 0:
                    is_new_job = register_pending_request(global_job_id, pgid)
                    
                    if is_new_job:
                        # New job - submit profiling request (async)
                        executor.submit(submit_profiling_request, global_job_id)
                    else:
                        # Known job - put in queue to trigger ACK processing
                        # This allows the drain thread to increment counter and send ACKs
                        print(f"[Re-trigger] job_id={global_job_id} - queueing for ACK check")
                        completed_requests_queue.put((global_job_id, None))
                else:
                    print(f"[Ignore] Invalid job_id={global_job_id}")

            except Exception as e:
                print(f"[Error] Main loop exception: {e}")
                traceback.print_exc()
                time.sleep(1)
