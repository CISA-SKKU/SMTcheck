"""
Profile Data Loader Module

This module handles communication between the scheduling system and:
- The MongoDB database (for fetching profile data)
- The kernel runtime_monitor module (via netlink and ioctl)
- The profiling server (via TCP for requesting profiles)

Key Functions:
    initialize(): Set up connections to kernel and database
    netlink_listener(): Block waiting for kernel events
    read_profile_data(): Fetch profile data from MongoDB
    send_profiling_request(): Request profiling from server
"""

import os, fcntl, struct, socket
import threading, time
from pymongo import MongoClient
import numpy as np
from .c_struct import RTMON_IOC_SET_DATA_LOADER_PID, _NLMSG_HDR_LEN, _NLMSG_HDR_FMT
from .machine_data import *
from .global_variable_generator import *

PROFILE_SERVER_IP = "192.168.0.20"
PORT = 8080
db_handler = None
kernel_sock = None
fd = None

lookup_history_table = dict()
lookup_history_table_lock = threading.Lock()

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
        print(f"[Database] Querying profile data for job_id={job_id} on node {self.node_name}", flush=True)
        return self.measurement_collection.find(query)

    def fetch_combination_data(self):
        """
        Fetch combination IPC data (pairwise workload measurements).
        Returns: dict mapping base_job_id -> {col_job_id -> IPC}
        """
        query = {"node_name": self.node_name}
        print(self.node_name)
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
                # print(f"[Database] Combination data - base_job_id={base_job_id}, col_job_id={col_key}, ipc={ipc}", flush=True)
        return result
    
    def close(self):
        self.client.close()

def send_profiling_request(global_jobid):
    """Send a profiling request to the profiling server via TCP.
    
    Connects to the profiling server and requests profiling for the given job ID.
    Blocks until the server responds, indicating profiling is complete.
    
    Args:
        global_jobid: The job ID to request profiling for
    """
    client = None
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((PROFILE_SERVER_IP, PORT))
        print(f"[TCP] Connected to {PROFILE_SERVER_IP}:{PORT}", flush=True)

        # Send job ID to server
        client.sendall(str(global_jobid).encode())
        print(f"[TCP] Sent job ID: {global_jobid}", flush=True)
        
        # Wait for server response (blocking)
        print("[TCP] Waiting for server response...", flush=True)
        data = client.recv(4096)
        if data:
            print(f"[TCP] Server response: {data.decode()}", flush=True)
        else:
            print("[TCP] Server closed connection without response", flush=True)
    except Exception as e:
        print(f"[TCP] Error: {e}", flush=True)
    finally:
        if client:
            client.close()
            print("[TCP] Connection closed", flush=True)

# =============================================================================
# Netlink Message Helpers
# =============================================================================

def make_msg(pgid: int, nlmsg_type: int = 0, nlmsg_flags: int = 0):
    """Build a netlink message to send to the kernel.
    
    Args:
        pgid: Process group ID to include in payload
        nlmsg_type: Netlink message type (default 0)
        nlmsg_flags: Netlink message flags (default 0)
        
    Returns:
        Bytes object containing the complete netlink message
    """
    payload = struct.pack("i", int(pgid))
    nlmsg_len = _NLMSG_HDR_LEN + len(payload)
    nlmsg_seq = 0
    nlmsg_pid = os.getpid()  # Sender PID
    hdr = struct.pack(_NLMSG_HDR_FMT, nlmsg_len, nlmsg_type, nlmsg_flags, nlmsg_seq, nlmsg_pid)
    msg = hdr + payload
    return msg


def read_profile_data(global_jobid, pgid):
    """Fetch profile data from MongoDB and notify kernel.
    
    Args:
        global_jobid: Job ID to fetch profile data for
        pgid: Process group ID to notify kernel about
        
    Returns:
        MongoDB cursor with profile documents, or None if not found
    """
    # Note: Sending profiling request is disabled for fast testing
    # Uncomment the following line to enable:
    # send_profiling_request(global_jobid)

    print(f"[Data Loader] Notifying kernel for PGID: {pgid}", flush=True)

    # Send netlink message to kernel (address (0, 0) goes to kernel)
    msg = make_msg(pgid)
    kernel_sock.sendto(msg, (0, 0))

    profile_documents = db_handler.fetch_profile_data(global_jobid)
    print(f"[Data Loader] Fetched profile data for job_id: {global_jobid}", flush=True)
    
    if not profile_documents:
        print(f"[Data Loader] No profile data found for job_id: {global_jobid}", flush=True)
        return
    
    for doc in profile_documents:
        print(doc, flush=True)
    
    return profile_documents

# =============================================================================
# Netlink Listener
# =============================================================================

def netlink_listener():
    """Block waiting for a netlink message from the kernel.
    
    The kernel sends messages when a long-running process is detected.
    Message format: "pgid,elapsed_seconds,global_jobid"
    
    Returns:
        Tuple of (pgid, global_jobid) as integers
    """
    data, addr = kernel_sock.recvfrom(65535)
    pgid, global_jobid = 0, 0
    
    if len(data) > 16:
        # Skip 16-byte netlink header
        payload = data[16:]
        msg = payload.split(b'\x00', 1)[0]
        decoded = msg.decode(errors="ignore").strip()
        pgid, _, global_jobid = decoded.split(",")
        print(f"[Netlink] Event received: job_id={global_jobid}", flush=True)
    else:
        print(f"[Netlink] Short message: {data}")
    
    return map(int, (pgid, global_jobid))

def initialize():
    """Initialize the profile data loader.
    
    Sets up:
    - Connection to MongoDB database
    - File descriptor to runtime_monitor device
    - Netlink socket for kernel communication
    - Registers this process's PID with the kernel module
    """
    global db_handler, fd, kernel_sock
    
    # Open runtime_monitor device and register our PID
    fd = os.open("/dev/runtime_monitor", os.O_RDWR)
    pid = os.getpid()
    buf = struct.pack("i", pid)
    
    # Connect to MongoDB
    db_handler = DatabaseHandler(NODE_NAME, "mongodb://192.168.0.13:27017")
    
    # Register with kernel via ioctl
    fcntl.ioctl(fd, RTMON_IOC_SET_DATA_LOADER_PID, buf)
    print(f"Registered PID with kernel: {pid}", flush=True)

    # Create netlink socket for receiving kernel events
    NETLINK_USER = 31
    kernel_sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_USER)
    kernel_sock.bind((os.getpid(), 0))  # Bind with our PID

    print("[Netlink] Listening for kernel events...", flush=True)

if __name__ == "__main__":
    initialize()
    while True:
        try:
            pgid, global_jobid = netlink_listener()
            if pgid != 0 and global_jobid != 0:
                threading.Thread(target=read_profile_data, args=(global_jobid, pgid)).start()
        except Exception as e:
            print("Error in main loop:", e, flush=True)
            time.sleep(1)