"""
Dummy Process Spawner

This script spawns a dummy process that can be monitored by the runtime_monitor
kernel module. It is useful for testing the scheduling system without running
actual workloads.

Usage:
    sudo python run_dummy_process.py --jobid <JOB_ID>

Arguments:
    --jobid: Integer job ID (0-30) to associate with this dummy process

Example:
    sudo python run_dummy_process.py --jobid 5

Behavior:
    - Forks into a daemon process
    - Creates a new session (setsid) to get its own PGID
    - Registers with runtime_monitor via ioctl
    - Runs an infinite loop (simulating workload)

Note:
    The process runs as a daemon, so the parent script exits immediately.
    To stop the dummy process, use: kill <DAEMON_PID>
"""

import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fcntl
import struct
import argparse
from userlevel.python.smtcheck.c_struct import *


def daemonize():
    """Fork into a daemon process.
    
    The parent process exits immediately, and the child continues.
    """
    pid = os.fork()
    if pid > 0:
        print(f"Daemon PID: {pid}")
        os._exit(0)  # Parent exits immediately
    os.setsid()  # Child: create new session (becomes session leader)


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(
        description="Spawn a dummy process for runtime_monitor testing."
    )
    arg_parser.add_argument(
        "--jobid", 
        type=int, 
        required=True, 
        help="Job ID (0-30) to associate with this dummy process."
    )
    args = arg_parser.parse_args()
    job_id = args.jobid

    if job_id < 0 or job_id > 30:
        print("Error: Job ID must be between 0 and 30.")
        sys.exit(1)

    # Fork into daemon
    daemonize()
    
    # Get our process group ID (we are now the session leader)
    pgid = os.getpgrp()

    # Register with runtime_monitor kernel module
    fd_runtime_monitor = os.open("/dev/runtime_monitor", os.O_RDWR)
    fcntl.ioctl(
        fd_runtime_monitor, 
        RTMON_IOC_ADD_PGID, 
        struct.pack("iii", pgid, job_id, 1)  # pgid, global_jobid, worker_num
    )

    # Run infinite loop (simulating workload)
    while True:
        pass