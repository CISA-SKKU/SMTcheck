"""
This is just an example. You can modify the job ID range 
according to your testing needs.
- All requests are sent in parallel using ThreadPoolExecutor
- The number of workers equals the number of job IDs to process
- Adjust max_workers if you need to limit concurrent connections
Profiling Request Test Client

This is a TESTING script that sends profiling requests to the profiling server.
It is used to trigger profiling for a range of workloads (by global_jobid) 
without running the actual scheduler or kernel modules.

Use this script to:
    - Test if the profiling server is running correctly
    - Manually trigger profiling for specific workloads
    - Populate the profile database with new workload measurements

The script sends TCP requests to the profiling server, which then runs
the workload with various injectors to measure resource sensitivity.

Usage:
    python send_profiling_request_for_testing.py <start_jobid> <end_jobid>
    
Example:
    python send_profiling_request_for_testing.py 0 10
    # Sends profiling requests for job IDs 0 through 10

"""

import socket
import sys
from concurrent.futures import ThreadPoolExecutor

# Profiling server connection settings
SERVER = "192.168.0.20"
PORT = 8080

# Parse command line arguments: range of job IDs to profile
start_global_jobid = int(sys.argv[1])
end_global_jobid   = int(sys.argv[2])


def send_request(global_jobid: str) -> None:
    """
    Send a profiling request to the server for a specific job ID.
    
    Connects to the profiling server via TCP and sends the job ID.
    The server will run the workload with injectors and store results.
    
    Args:
        global_jobid: String representation of the job ID to profile
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.connect((SERVER, PORT))
        client.sendall(global_jobid.encode("utf-8"))
        data = client.recv(1024)
        print(f"[{global_jobid}][Client] Received:", data.decode())

target_jobids = map(str, range(start_global_jobid, end_global_jobid + 1))

# Send profiling requests in parallel (faster execution)
with ThreadPoolExecutor(max_workers=(end_global_jobid - start_global_jobid + 1)) as ex:
    ex.map(send_request, target_jobids)

