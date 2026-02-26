#!/usr/bin/env python3
import ctypes
import os
import struct
import platform
import time

# =========================
# syscall / perf constants
# =========================
arch = platform.machine()
if arch == "x86_64":
    __NR_perf_event_open = 298
elif arch == "aarch64":
    __NR_perf_event_open = 241
elif arch == "riscv64":
    __NR_perf_event_open = 241
else:
    raise RuntimeError(f"Unsupported architecture: {arch}")

PERF_TYPE_HARDWARE = 0
PERF_COUNT_HW_CPU_CYCLES = 0
PERF_COUNT_HW_INSTRUCTIONS = 1

PERF_EVENT_IOC_ENABLE  = 0x2400
PERF_EVENT_IOC_DISABLE = 0x2401
PERF_EVENT_IOC_RESET   = 0x2403

FLAG_DISABLED       = 1 << 0
FLAG_INHERIT        = 1 << 1
FLAG_EXCLUDE_KERNEL = 1 << 5
FLAG_EXCLUDE_HV     = 1 << 6

libc = ctypes.CDLL(None, use_errno=True)

# =========================
# perf_event_attr (minimal)
# =========================
class perf_event_attr(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint),
        ("size", ctypes.c_uint),
        ("config", ctypes.c_ulonglong),
        ("sample_period", ctypes.c_ulonglong),
        ("sample_type", ctypes.c_ulonglong),
        ("read_format", ctypes.c_ulonglong),
        ("flags", ctypes.c_ulonglong),
        ("wakeup_events", ctypes.c_uint),
        ("bp_type", ctypes.c_uint),
        ("config1", ctypes.c_ulonglong),
        ("config2", ctypes.c_ulonglong),
    ]


def perf_event_open(attr, pid, cpu, group_fd=-1, flags=0):
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
    if libc.ioctl(fd, req, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def read_counter(fd):
    return struct.unpack("Q", os.read(fd, 8))[0]


# =========================
# open cycles + instructions
# =========================
def open_hw_counter_by_cid(config, cpu):
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
    ioctl(fd, PERF_EVENT_IOC_ENABLE)
    ioctl(fd, PERF_EVENT_IOC_RESET)

def disable_counter(fd):
    ioctl(fd, PERF_EVENT_IOC_DISABLE)


class PerfCounter:
    """Manages hardware performance counters for a CPU core."""
    def __init__(self, core_id):
        self.fd_cycles = open_hw_counter_by_cid(PERF_COUNT_HW_CPU_CYCLES, core_id)
        self.fd_insts = open_hw_counter_by_cid(PERF_COUNT_HW_INSTRUCTIONS, core_id)
        disable_counter(self.fd_cycles)
        disable_counter(self.fd_insts)

    def get_IPC(self):
        cycles = read_counter(self.fd_cycles)
        insts = read_counter(self.fd_insts)
        return insts / cycles if cycles > 0 else 0.0

    def disable(self):
        disable_counter(self.fd_cycles)
        disable_counter(self.fd_insts)
    
    def enable_and_reset(self):
        enable_and_reset_counter(self.fd_cycles)
        enable_and_reset_counter(self.fd_insts)
    
    def __del__(self):
        os.close(self.fd_cycles)
        os.close(self.fd_insts)

if __name__ == "__main__":
    cid = 0

    fd_cycles = open_hw_counter_by_cid(PERF_COUNT_HW_CPU_CYCLES, cid)
    fd_insts  = open_hw_counter_by_cid(PERF_COUNT_HW_INSTRUCTIONS, cid)

    # reset + enable
    ioctl(fd_cycles, PERF_EVENT_IOC_RESET)
    ioctl(fd_insts,  PERF_EVENT_IOC_RESET)
    ioctl(fd_cycles, PERF_EVENT_IOC_ENABLE)
    ioctl(fd_insts,  PERF_EVENT_IOC_ENABLE)

    try:
        while True:
            time.sleep(1)
            cycles = read_counter(fd_cycles)
            insts  = read_counter(fd_insts)

            print("cycles:", cycles)
            print("instructions:", insts)
            print("IPC:", insts / cycles if cycles > 0 else 0.0)

            ioctl(fd_cycles, PERF_EVENT_IOC_RESET)
            ioctl(fd_insts,  PERF_EVENT_IOC_RESET)
    finally:
        os.close(fd_cycles)
        os.close(fd_insts)
