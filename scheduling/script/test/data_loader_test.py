"""
Data Loader Test Script

This script tests the profile data loading functionality by:
1. Listening for netlink messages from the kernel (runtime_monitor)
2. Fetching profile data from MongoDB for received job IDs
3. Displaying the profile data for verification

Usage:
    sudo python data_loader_test.py

Prerequisites:
    - runtime_monitor.ko kernel module must be loaded
    - MongoDB server must be running with profile data
    - Script must be run with root privileges

Output:
    - Prints netlink events (PGID, Global JobID) as they are received
    - Prints profile data documents from MongoDB for each job
"""

import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import time
from userlevel.python.smtcheck.c_struct import *
import userlevel.python.smtcheck.profile_data_loader as profile_data_loader

if __name__ == "__main__":
    # Initialize the profile data loader (registers with kernel, sets up netlink)
    profile_data_loader.initialize()
    
    print("[Data Loader Test] Starting netlink listener. Press Ctrl+C to stop.")
    print("[Data Loader Test] Waiting for kernel events...")
    
    while True:
        try:
            # Block until a netlink message is received from the kernel
            pgid, global_jobid = profile_data_loader.netlink_listener()
            print(f"[Netlink] Received event - PGID: {pgid}, Global JobID: {global_jobid}")
            
            # Fetch and display profile data from MongoDB
            profile_documents = profile_data_loader.read_profile_data(global_jobid, pgid)

            for doc in profile_documents:
                print(doc)
                
        except Exception as e:
            print(f"[Error] Exception in main loop: {e}")
            time.sleep(1)