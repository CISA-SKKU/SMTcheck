"""
Profiling Server Configuration

Update these values based on your deployment environment.
"""

# Network configuration
HOST = "192.168.0.20"                      # Server bind address
PORT = 8080                                 # Server listen port
DB_SERVER = "mongodb://192.168.0.13:27017" # MongoDB connection string

# Node identification
NODE_NAME = "intel-gen11"                   # Unique identifier for this machine

# Profiling parameters
MAXIMUM_UTIL = 0.5      # Maximum CPU utilization ratio for profiling (0.0-1.0)
WARMUP_COUNT = 6        # Number of warmup iterations before measurement
SAMPLING_TIME = 10      # Duration of each measurement in seconds