"""
C Structure Definitions for Kernel Module Communication

This module defines ctypes structures that mirror the kernel module's
data structures, enabling Python to read from shared memory mapped
from the kernel IPC_monitor module.

Structures:
    - PgidSlot: Per-process group performance counter slot
    - IpcShared: Main shared memory structure with active slots
    - SharedMemoryManager: Manages mmap and provides slot iteration
"""

import ctypes
import os
import mmap
import fcntl
import struct

# =============================================================================
# Constants
# =============================================================================
MAX_SLOTS = 4096
BITS_PER_LONG = ctypes.sizeof(ctypes.c_ulong) * 8
ACTIVE_MASK_SIZE = (MAX_SLOTS + BITS_PER_LONG - 1) // BITS_PER_LONG

# =============================================================================
# IOCTL Command Definitions
# =============================================================================
RTMON_IOC_MAGIC = ord('k')
RTMON_IOC_SET_DATA_LOADER_PID = 0x40046b03  # _IOW('k', 3, int)

# IOC bit field sizes (Linux convention)
_IOC_NRBITS   = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS  = 2

# IOC bit field shifts
_IOC_NRSHIFT   = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT  = _IOC_SIZESHIFT + _IOC_SIZEBITS

# IOC direction flags
_IOC_NONE  = 0
_IOC_WRITE = 1
_IOC_READ  = 2

# Netlink message header format
_NLMSG_HDR_FMT = "IHHII"
_NLMSG_HDR_LEN = struct.calcsize(_NLMSG_HDR_FMT)

def _IOC(direction, type_code, number, size):
    """Build an ioctl command number from components."""
    return ((direction << _IOC_DIRSHIFT) | 
            (type_code << _IOC_TYPESHIFT) | 
            (number << _IOC_NRSHIFT) | 
            (size << _IOC_SIZESHIFT))


def _IOW(type_char, number, size):
    """Build a write ioctl command number."""
    return _IOC(_IOC_WRITE, ord(type_char), number, size)


# Size of my_pair struct from runtime_monitor.h (3 ints = 12 bytes)
MY_PAIR_SIZE = struct.calcsize("iii")

# Runtime monitor ioctl commands
RTMON_IOC_MAGIC = 'k'
RTMON_IOC_ADD_PGID        = _IOW(RTMON_IOC_MAGIC, 0, MY_PAIR_SIZE)
RTMON_IOC_REMOVE_PGID     = _IOW(RTMON_IOC_MAGIC, 1, struct.calcsize("i"))
RTMON_IOC_SET_THRESHOLD   = _IOW(RTMON_IOC_MAGIC, 2, struct.calcsize("i"))
RTMON_IOC_SET_DATA_LOADER = _IOW(RTMON_IOC_MAGIC, 3, struct.calcsize("i"))
RTMON_IOC_REQUEST_PROFILE = _IOW(RTMON_IOC_MAGIC, 4, struct.calcsize("i"))

# IPC monitor ioctl commands
IPC_IOC_RESET_COUNTERS = 18688  # _IO('I', 0)

# ---- struct pgid_slot_only_counter (aligned(16)) ----
class PgidSlot(ctypes.Structure):
    _align_ = 16
    _fields_ = [
        ("seq", ctypes.c_uint32),
        ("pgid", ctypes.c_int32),
        ("global_jobid", ctypes.c_int32),
        ("worker_num", ctypes.c_int32),
        ("cycles", ctypes.c_uint64),
        ("instructions", ctypes.c_uint64),
        # aligned(16) attribute matches the kernel struct layout
    ]

# ---- struct ipc_shared ----
#
# struct ipc_shared {
#     atomic_t count;   // effectively 4 bytes on x86_64
#     unsigned long active_mask[64]; // 8*64 = 512 bytes
#     struct pgid_slot_only_counter slots[4096]; // 16*4096 = 65536 bytes
# };
#
# Important: alignment after atomic_t
# - atomic_t is 4 bytes, so the next field (unsigned long, 8-byte aligned)
#   will introduce 4 bytes implicit padding in C on x86_64.
#
class IpcShared(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_int32),
        ("_pad0", ctypes.c_int32),
        ("active_mask", ctypes.c_ulong * ACTIVE_MASK_SIZE),
        ("_pad_to_slots", ctypes.c_uint8 * 8),
        ("slots", PgidSlot * MAX_SLOTS),
    ]

class SharedMemoryManager:
    """
    Manager for shared memory mapped from the IPC_monitor kernel module.
    
    Provides methods to:
    - Map the /dev/IPC_monitor device into Python's address space
    - Iterate over active slots containing valid performance data
    - Reset kernel counters via ioctl
    
    Usage:
        manager = SharedMemoryManager()
        manager.map()
        for slot in manager.active_slots:
            print(f"PGID={slot.pgid}, IPC={slot.instructions/slot.cycles}")
    """
    
    def __init__(self, device_path="/dev/IPC_monitor"):
        self.device_path = device_path
        self.fd = -1
        self.mm = None
        self.data = None  # Will hold the IpcShared structure
        self.is_mapped = False

        # Build ioctl command for resetting counters: _IO('I', 0)
        self.IPC_IOC_MAGIC = ord('I')
        self.IPC_IOC_RESET_COUNTERS = (
            (0 << 30) | (self.IPC_IOC_MAGIC << 8) | (0 << 0) | (0 << 16)
        )

    def map(self):
        """Map the shared memory from kernel into userspace.
        
        Opens the device file and creates an mmap with PAGE_ALIGN size
        to match the kernel's allocation.
        """
        if self.is_mapped:
            print("Warning: Memory is already mapped.")
            return

        try:
            self.fd = os.open(self.device_path, os.O_RDWR)
            
            # Calculate PAGE_ALIGN size (same as kernel's calculation)
            base_size = ctypes.sizeof(IpcShared)
            page_size = os.sysconf("SC_PAGESIZE")
            mmap_size = ((base_size + page_size - 1) // page_size) * page_size
            
            print(f"Base size: {base_size}, Page size: {page_size}, Aligned mmap_size: {mmap_size}")
            
            self.mm = mmap.mmap(self.fd, mmap_size, 
                                   flags=mmap.MAP_SHARED, 
                                   prot=mmap.PROT_READ | mmap.PROT_WRITE)
            
            self.data = IpcShared.from_buffer(self.mm)
            self.is_mapped = True
            print("Shared memory mapped successfully.")
            
        except Exception as e:
            self.close()
            raise e
    
    def reset_counters(self):
        """Send ioctl to kernel to reset performance counters."""
        if not self.is_mapped:
            print("Error: Memory not mapped.")
            return

        try:
            fcntl.ioctl(self.fd, self.IPC_IOC_RESET_COUNTERS)
            print("ioctl: IPC_IOC_RESET_COUNTERS command sent.")
        except OSError as e:
            print(f"ioctl failed: {e}")

    def close(self):
        """Release mapped memory and file descriptor."""
        if self.mm:
            self.mm.close()
            self.mm = None
        if self.fd != -1:
            os.close(self.fd)
            self.fd = -1
        self.is_mapped = False
        print("Shared memory resources cleaned up.")

    def _scan_bitmask(self):
        """
        Generator that scans the active_mask bitmask and yields active slot indices.
        
        Uses bit manipulation to efficiently find set bits in the mask.
        """
        if not self.is_mapped or self.data.count == 0:
            print("No active slots to scan.")
            return

        for i, bits_chunk in enumerate(self.data.active_mask):
            if bits_chunk == 0:
                continue
            
            temp_chunk = bits_chunk
            while temp_chunk > 0:
                # Find rightmost set bit using two's complement trick
                rightmost_one = temp_chunk & -temp_chunk
                bit_position = rightmost_one.bit_length() - 1
                yield i * BITS_PER_LONG + bit_position
                temp_chunk &= ~rightmost_one  # Clear the rightmost bit

    @property
    def active_slots(self):
        """
        Iterator over active PgidSlot objects.
        
        Yields:
            PgidSlot objects for each active slot in the shared memory.
            
        Usage:
            for slot in manager.active_slots:
                print(f"PGID={slot.pgid}, Cycles={slot.cycles}")
        """
        for index in self._scan_bitmask():
            print(f"Active slot index found: {index}")
            yield self.data.slots[index]

# (optional) sanity prints
if __name__ == "__main__":
    print("sizeof(PgidSlot) =", ctypes.sizeof(PgidSlot))
    print("alignof(PgidSlot) =", ctypes.alignment(PgidSlot))
    print("sizeof(IpcShared) =", ctypes.sizeof(IpcShared))
    print("offset(active_mask) =", IpcShared.active_mask.offset)
    print("offset(slots) =", IpcShared.slots.offset)
