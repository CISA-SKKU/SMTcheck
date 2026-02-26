/**
 * @file IPC_monitor.h
 * @brief Header file for IPC Monitor kernel module API
 *
 * This header provides the interface for tracking Instructions Per Cycle (IPC)
 * of process groups (PGIDs). It is used by the runtime_monitor module to register
 * and unregister processes for performance monitoring.
 */

#ifndef _IPC_MONITOR_H
#define _IPC_MONITOR_H

#include <linux/types.h>

/**
 * ipcmon_add_pgid - Register a process group for IPC monitoring
 * @pgid: Process group ID to monitor
 * @global_jobid: Global job identifier for the workload
 * @worker_num: Number of worker threads in this process group
 *
 * This function adds a PGID to the IPC monitoring system. Once registered,
 * the module will track CPU cycles and instructions for all context switches
 * involving this process group.
 *
 * Return: 0 on success, -ENOMEM if no slots available or allocation fails
 */
int ipcmon_add_pgid(pid_t pgid, int global_jobid, int worker_num);

/**
 * ipcmon_remove_pgid - Unregister a process group from IPC monitoring
 * @pgid: Process group ID to remove
 *
 * This function removes a PGID from the IPC monitoring system and frees
 * the associated slot.
 *
 * Return: 0 on success, -ENOENT if PGID not found
 */
int ipcmon_remove_pgid(pid_t pgid);

#endif /* _IPC_MONITOR_H */