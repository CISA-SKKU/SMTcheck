#!/usr/bin/env python3
"""
Performance Counter Module

This module provides a Python interface to Linux perf_event system calls
for measuring hardware performance counters (CPU cycles and instructions).
It uses ctypes to directly invoke the perf_event_open syscall without
requiring external libraries like libpfm4.

Supported architectures:
- x86_64 (Intel/AMD)
- aarch64 (ARM64)
- riscv64 (RISC-V 64-bit)

The module measures user-space only events (excluding kernel and hypervisor)
to provide accurate IPC (Instructions Per Cycle) measurements for workload
characterization.

Usage:
    counter = PerfCounter(core_id=0)
    counter.enable_and_reset()
    # ... code to measure ...
    ipc = counter.get_IPC()
    counter.disable()
"""

import ctypes
import os
import struct
import platform

# =========================
# syscall / perf constants
# =========================
# Architecture-specific syscall numbers for perf_event_open
arch = platform.machine()
if arch == "x86_64":
    __NR_perf_event_open = 298
elif arch == "aarch64":
    __NR_perf_event_open = 241
elif arch == "riscv64":
    __NR_perf_event_open = 241
else:
    raise RuntimeError(f"Unsupported architecture: {arch}")

# Hardware performance counter types
PERF_TYPE_HARDWARE = 0
PERF_COUNT_HW_CPU_CYCLES = 0       # Count CPU clock cycles
PERF_COUNT_HW_INSTRUCTIONS = 1    # Count retired instructions

# ioctl request codes for controlling perf counters
PERF_EVENT_IOC_ENABLE  = 0x2400   # Start counting
PERF_EVENT_IOC_DISABLE = 0x2401   # Stop counting
PERF_EVENT_IOC_RESET   = 0x2403   # Reset counter to zero

# Event attribute flags
FLAG_DISABLED       = 1 << 0   # Counter starts disabled
FLAG_INHERIT        = 1 << 1   # Child processes inherit counter
FLAG_EXCLUDE_KERNEL = 1 << 5   # Don't count kernel code
FLAG_EXCLUDE_HV     = 1 << 6   # Don't count hypervisor code

# Load libc for syscall access
libc = ctypes.CDLL(None, use_errno=True)


# =========================
# perf_event_attr (minimal)
# =========================
class perf_event_attr(ctypes.Structure):
    """
    Minimal perf_event_attr structure for syscall.
    
    This is a simplified version containing only the fields
    needed for basic hardware counter configuration.
    See linux/perf_event.h for the full structure.
    """
    _fields_ = [
        ("type", ctypes.c_uint),           # Type of event (hardware, software, etc.)
        ("size", ctypes.c_uint),           # Size of this structure
        ("config", ctypes.c_ulonglong),    # Event configuration (counter type)
        ("sample_period", ctypes.c_ulonglong),
        ("sample_type", ctypes.c_ulonglong),
        ("read_format", ctypes.c_ulonglong),
        ("flags", ctypes.c_ulonglong),     # Event flags (disabled, exclude_kernel, etc.)
        ("wakeup_events", ctypes.c_uint),
        ("bp_type", ctypes.c_uint),
        ("config1", ctypes.c_ulonglong),
        ("config2", ctypes.c_ulonglong),
    ]


def perf_event_open(attr, pid, cpu, group_fd=-1, flags=0):
    """
    Open a performance monitoring counter.
    
    This is a direct wrapper around the perf_event_open syscall.
    
    Args:
        attr: perf_event_attr structure with event configuration
        pid: Process ID to monitor (-1 for any process on specified CPU)
        cpu: CPU core to monitor (-1 for any CPU)
        group_fd: File descriptor of group leader (-1 for new group)
        flags: Additional flags for the syscall
        
    Returns:
        File descriptor for the opened perf event
        
    Raises:
        OSError: If the syscall fails
    """
    fd = libc.syscall(
        ctypes.c_long(__NR_perf_event_open),
        ctypes.byref(attr),
        ctypes.c_int(pid),
        ctypes.c_int(cpu),
        ctypes.c_int(group_fd),
        ctypes.c_ulong(flags),
    )
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return fd


def ioctl(fd, req):
    """
    Send an ioctl request to a perf counter file descriptor.
    
    Args:
        fd: File descriptor from perf_event_open
        req: ioctl request code (ENABLE, DISABLE, or RESET)
        
    Raises:
        OSError: If the ioctl fails
    """
    if libc.ioctl(fd, req, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def read_counter(fd):
    """
    Read the current value from a perf counter.
    
    Args:
        fd: File descriptor from perf_event_open
        
    Returns:
        Counter value as unsigned 64-bit integer
    """
    return struct.unpack("Q", os.read(fd, 8))[0]


# =========================
# open cycles + instructions
# =========================
def open_hw_counter_by_cid(config, cpu):
    """
    Open a hardware performance counter on a specific CPU.
    
    Creates a user-space only counter that excludes kernel
    and hypervisor events for accurate application profiling.
    
    Args:
        config: Counter type (PERF_COUNT_HW_CPU_CYCLES or PERF_COUNT_HW_INSTRUCTIONS)
        cpu: CPU core ID to monitor
        
    Returns:
        File descriptor for the opened counter
    """
    attr = perf_event_attr()
    attr.type = PERF_TYPE_HARDWARE
    attr.size = ctypes.sizeof(perf_event_attr)
    attr.config = config
    attr.flags = (
        FLAG_DISABLED |
        FLAG_INHERIT |
        FLAG_EXCLUDE_KERNEL |
        FLAG_EXCLUDE_HV
    )
    return perf_event_open(attr, -1, cpu)


def enable_and_reset_counter(fd):
    """Enable counting and reset counter value to zero."""
    ioctl(fd, PERF_EVENT_IOC_ENABLE)
    ioctl(fd, PERF_EVENT_IOC_RESET)


def disable_counter(fd):
    """Stop counting events (value is preserved)."""
    ioctl(fd, PERF_EVENT_IOC_DISABLE)


class PerfCounter:
    """
    High-level interface for measuring IPC on a specific CPU core.
    
    Manages paired cycles and instructions counters for calculating
    Instructions Per Cycle (IPC) measurements. Automatically handles
    counter lifecycle and cleanup.
    
    Example:
        counter = PerfCounter(core_id=0)
        counter.enable_and_reset()
        # ... run workload ...
        ipc = counter.get_IPC()
        counter.disable()
    
    Attributes:
        fd_cycles: File descriptor for CPU cycles counter
        fd_insts: File descriptor for instructions counter
    """
    
    def __init__(self, core_id):
        """
        Initialize performance counters for a CPU core.
        
        Opens cycles and instructions counters, initially disabled.
        
        Args:
            core_id: CPU core ID to monitor (0-indexed)
        """
        self.fd_cycles = open_hw_counter_by_cid(PERF_COUNT_HW_CPU_CYCLES, core_id)
        self.fd_insts = open_hw_counter_by_cid(PERF_COUNT_HW_INSTRUCTIONS, core_id)
        disable_counter(self.fd_cycles)
        disable_counter(self.fd_insts)

    def get_IPC(self):
        """
        Calculate IPC from current counter values.
        
        Returns:
            Instructions Per Cycle (float), or 0.0 if no cycles counted
        """
        cycles = read_counter(self.fd_cycles)
        insts = read_counter(self.fd_insts)
        return insts / cycles if cycles > 0 else 0.0

    def disable(self):
        """Stop both counters (preserves values for reading)."""
        disable_counter(self.fd_cycles)
        disable_counter(self.fd_insts)
    
    def enable_and_reset(self):
        """Reset counters to zero and start counting."""
        enable_and_reset_counter(self.fd_cycles)
        enable_and_reset_counter(self.fd_insts)
    
    def __del__(self):
        """Clean up file descriptors on object destruction."""
        os.close(self.fd_cycles)
        os.close(self.fd_insts)


if __name__ == "__main__":
    # Example: Measure IPC for a simple loop on CPU 0
    cid = 0

    fd_cycles = open_hw_counter_by_cid(PERF_COUNT_HW_CPU_CYCLES, cid)
    fd_insts  = open_hw_counter_by_cid(PERF_COUNT_HW_INSTRUCTIONS, cid)

    # reset + enable
    ioctl(fd_cycles, PERF_EVENT_IOC_RESET)
    ioctl(fd_insts,  PERF_EVENT_IOC_RESET)
    ioctl(fd_cycles, PERF_EVENT_IOC_ENABLE)
    ioctl(fd_insts,  PERF_EVENT_IOC_ENABLE)

    # ---- measured region ----
    for _ in range(10_000_000):
        pass
    # -------------------------

    # disable
    ioctl(fd_cycles, PERF_EVENT_IOC_DISABLE)
    ioctl(fd_insts,  PERF_EVENT_IOC_DISABLE)

    cycles = read_counter(fd_cycles)
    insts  = read_counter(fd_insts)

    print("cycles:", cycles)
    print("instructions:", insts)
    print("IPC:", insts / cycles if cycles > 0 else 0.0)

    os.close(fd_cycles)
    os.close(fd_insts)
