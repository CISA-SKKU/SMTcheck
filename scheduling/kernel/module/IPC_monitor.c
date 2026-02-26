/**
 * @file IPC_monitor.c
 * @brief Kernel module for monitoring Instructions Per Cycle (IPC) of process groups (PGIDs)
 *
 * This module provides real-time IPC monitoring for registered PGIDs.
 * It uses per-CPU hardware performance counters (PMU) to track CPU cycles and instructions,
 * and hooks into the scheduler via the sched_switch tracepoint to attribute deltas to
 * the outgoing task's process group.
 *
 * Fixes in this version:
 *  - vmalloc-safe mmap: vzalloc() + per-page remap_pfn_range()
 *  - RCU-safe slot reuse: per-slot generation (gen) to reject stale updates
 *  - Correct “switch-in start / switch-out end-start” accounting using per-CPU state
 *  - Remove-path safety: gen bump under slot lock + lock-protected slot clear (no data race)
 *  - Thread-safe slot allocator: global spinlock protects free_list/tail_index/free_count
 *  - Duplicate PGID add: re-check under hash lock right before publishing map
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/perf_event.h>
#include <linux/hashtable.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/fs.h>
#include <linux/cdev.h>
#include <linux/sched.h>
#include <linux/sched/task.h>
#include <linux/mm.h>
#include <linux/vmalloc.h>
#include <linux/pid.h>
#include <linux/types.h>
#include <linux/device.h>
#include <linux/rcupdate.h>
#include <linux/tracepoint.h>
#include <trace/events/sched.h>
#include <linux/uaccess.h>
#include <linux/sched/signal.h>
#include <linux/bitmap.h>
#include <linux/atomic.h>

#include "IPC_monitor.h"

#define MAX_SLOTS       4096
#define PGID_HASH_BITS  10

/* ioctl command definitions */
#define IPC_IOC_MAGIC 'I'
#define IPC_IOC_RESET_COUNTERS _IO(IPC_IOC_MAGIC, 0)

/* =========================
 * Kernel-internal slot
 * ========================= */
struct pgid_slot {
    spinlock_t lock;

    __s32 pgid;
    __s32 global_jobid;

    __u32 reset_flag;
    __u32 worker_num;

    __u32 gen;
    __u32 _rsvd;

    __u64 cycles;
    __u64 instructions;
} __attribute__((aligned(64)));

/* =========================
 * Userspace-visible snapshot slot (mmap region)
 * =========================
 * seq: even = stable snapshot, odd = writer in progress
 */
struct pgid_slot_user {
    __u32 seq;
    __s32 pgid;
    __s32 global_jobid;
    __s32 worker_num;
    __u64 cycles;
    __u64 instructions;
} __attribute__((aligned(16)));

struct ipc_shared {
    atomic_t count;
    unsigned long active_mask[BITS_TO_LONGS(MAX_SLOTS)];
    struct pgid_slot_user slots[MAX_SLOTS];
};

/* Userspace-shared region mapped via mmap */
static struct ipc_shared *shared_mem;
static size_t shared_mem_size;

/* Kernel-internal slots (lock + metadata + gen + true counters) */
static struct pgid_slot kslots[MAX_SLOTS];

/* Slot allocation (indexes only) */
static int tail_index;
static int free_list[MAX_SLOTS];
static int free_count;
static DEFINE_SPINLOCK(slot_alloc_lock);

/* Character device structures */
static struct cdev ipc_cdev;
static dev_t dev_no;
static struct class *ipc_class;
static struct device *ipc_device;

/* PGID->slot mapping (RCU) */
struct pgid_map {
    pid_t pgid;
    int slot_idx;
    __u32 gen;
    struct hlist_node hnode;
    struct rcu_head rcu;
};
static DEFINE_HASHTABLE(pgid_hash, PGID_HASH_BITS);
static DEFINE_SPINLOCK(pgid_hash_lock);

/* Per-CPU accounting state (initialized for CPU hotplug safety) */
static DEFINE_PER_CPU(int, running_slot_idx) = -1;     /* -1 if none */
static DEFINE_PER_CPU(u32, running_slot_gen) = 0;      /* expected gen */
static DEFINE_PER_CPU(u64, running_start_cycles) = 0;
static DEFINE_PER_CPU(u64, running_start_insts) = 0;

/* Per-CPU PMU event handles */
static DEFINE_PER_CPU(struct perf_event *, cpu_cycles_event);
static DEFINE_PER_CPU(struct perf_event *, cpu_instructions_event);

/* Tracepoint handle */
static struct tracepoint *sched_switch_tracepoint;
static bool sched_switch_registered;

/* ---------- helpers ---------- */

static bool perf_event_is_valid(struct perf_event *event)
{
    return event && !IS_ERR(event) &&
           event->pmu && event->pmu->read &&
           event->state == PERF_EVENT_STATE_ACTIVE;
}

static inline u64 delta_u64_wrap(u64 cur, u64 prev)
{
    if (cur >= prev)
        return cur - prev;
    return (U64_MAX - prev + 1ULL) + cur;
}

/* Publish kslots[idx].cycles/instructions into shared_mem snapshot with seq protocol.
 * Caller must hold kslots[idx].lock.
 */
static inline void publish_snapshot_locked(int idx)
{
    u32 s = READ_ONCE(shared_mem->slots[idx].seq);

    /* Mark writer in progress: odd */
    WRITE_ONCE(shared_mem->slots[idx].seq, s + 1);
    smp_wmb();  /* Ensure seq increment is visible before data writes */

    WRITE_ONCE(shared_mem->slots[idx].cycles, kslots[idx].cycles);
    WRITE_ONCE(shared_mem->slots[idx].instructions, kslots[idx].instructions);
    WRITE_ONCE(shared_mem->slots[idx].pgid, kslots[idx].pgid);
    WRITE_ONCE(shared_mem->slots[idx].global_jobid, kslots[idx].global_jobid);
    WRITE_ONCE(shared_mem->slots[idx].worker_num, kslots[idx].worker_num);

    smp_wmb();  /* Ensure data writes are visible before seq completion */
    /* Publish complete: even */
    WRITE_ONCE(shared_mem->slots[idx].seq, s + 2);
}

/* ---------- slot allocator ---------- */

static int alloc_slot(void)
{
    int idx = -1;

    spin_lock(&slot_alloc_lock);

    if (free_count > 0) {
        idx = free_list[--free_count];
        spin_unlock(&slot_alloc_lock);
        return idx;
    }

    if (tail_index < MAX_SLOTS) {
        idx = tail_index++;
        spin_unlock(&slot_alloc_lock);
        return idx;
    }

    spin_unlock(&slot_alloc_lock);
    return -1;
}

static void push_free_idx(int idx)
{
    spin_lock(&slot_alloc_lock);
    if (free_count < MAX_SLOTS) {
        free_list[free_count++] = idx;
    } else {
        pr_warn("IPC_monitor: free_list overflow (idx=%d)\n", idx);
    }
    spin_unlock(&slot_alloc_lock);
}

/* Clear kernel slot contents under lock; keep gen as-is (or bump separately). */
static inline void clear_kslot_locked(int idx)
{
    kslots[idx].pgid = 0;
    kslots[idx].global_jobid = 0;
    kslots[idx].worker_num = 0;
    kslots[idx].reset_flag = 0;
    kslots[idx].cycles = 0;
    kslots[idx].instructions = 0;
}

/* ---------- exported API ---------- */

int ipcmon_add_pgid(pid_t pgid, int global_jobid, int worker_num)
{
    struct pgid_map *map;
    int slot_idx;
    unsigned long flags;

    pr_info("IPC_monitor: Adding pgid=%d, global_jobid=%d, worker_num=%d\n",
            pgid, global_jobid, worker_num);

    slot_idx = alloc_slot();
    if (slot_idx < 0)
        return -ENOMEM;

    map = kmalloc(sizeof(*map), GFP_KERNEL);
    if (!map) {
        pr_info("IPC_monitor: Failed to allocate pgid_map for pgid=%d\n", pgid);
        push_free_idx(slot_idx);
        return -ENOMEM;
    }

    /* Initialize kernel slot */
    spin_lock_irqsave(&kslots[slot_idx].lock, flags);
    kslots[slot_idx].gen++;
    map->gen = kslots[slot_idx].gen;

    kslots[slot_idx].pgid = pgid;
    kslots[slot_idx].global_jobid = global_jobid;
    kslots[slot_idx].worker_num = worker_num;
    kslots[slot_idx].reset_flag = 0;
    kslots[slot_idx].cycles = 0;
    kslots[slot_idx].instructions = 0;

    /* Publish initial snapshot (0,0) */
    publish_snapshot_locked(slot_idx);
    spin_unlock_irqrestore(&kslots[slot_idx].lock, flags);

    map->pgid = pgid;
    map->slot_idx = slot_idx;

    /* Publish map under hash lock with duplicate re-check */
    spin_lock(&pgid_hash_lock);
    {
        struct pgid_map *it;
        hash_for_each_possible(pgid_hash, it, hnode, pgid) {
            if (it->pgid == pgid) {
                spin_unlock(&pgid_hash_lock);

                /* Roll back slot */
                spin_lock_irqsave(&kslots[slot_idx].lock, flags);
                kslots[slot_idx].gen++;           /* invalidate */
                clear_kslot_locked(slot_idx);
                publish_snapshot_locked(slot_idx);
                spin_unlock_irqrestore(&kslots[slot_idx].lock, flags);

                push_free_idx(slot_idx);
                kfree(map);
                return -EEXIST;
            }
        }
        hash_add_rcu(pgid_hash, &map->hnode, pgid);
    }
    spin_unlock(&pgid_hash_lock);

    set_bit(slot_idx, shared_mem->active_mask);
    atomic_inc(&shared_mem->count);

    pr_info("IPC_monitor: Added pgid=%d (slot=%d, gen=%u)\n", pgid, slot_idx, map->gen);
    return 0;
}
EXPORT_SYMBOL(ipcmon_add_pgid);

int ipcmon_remove_pgid(pid_t pgid)
{
    struct pgid_map *map;
    unsigned long flags;

    spin_lock(&pgid_hash_lock);
    hash_for_each_possible(pgid_hash, map, hnode, pgid) {
        int slot_idx = map->slot_idx;
        if (map->pgid == pgid) {
            pr_info("IPC_monitor: Removing pgid=%d (slot=%d, gen=%u, slot[%d] = (%d, %d, %d, %d))\n",
                    pgid, map->slot_idx, map->gen, map->slot_idx,
                    kslots[slot_idx].pgid, kslots[slot_idx].global_jobid,
                    kslots[slot_idx].worker_num, kslots[slot_idx].reset_flag);
            pr_info("snapshot[0]: seq=%u pgid=%d cycles=%llu inst=%llu\n",
                    shared_mem->slots[slot_idx].seq,
                    shared_mem->slots[slot_idx].pgid,
                    shared_mem->slots[slot_idx].cycles,
                    shared_mem->slots[slot_idx].instructions);    

            /* Hide from userspace polling immediately */
            clear_bit(slot_idx, shared_mem->active_mask);

            /* Remove lookup first */
            hash_del_rcu(&map->hnode);
            spin_unlock(&pgid_hash_lock);

            /* Invalidate any stale per-CPU state and clear kernel slot */
            spin_lock_irqsave(&kslots[slot_idx].lock, flags);
            kslots[slot_idx].gen++;      /* invalidate stale expected_gen */
            clear_kslot_locked(slot_idx);
            publish_snapshot_locked(slot_idx);
            spin_unlock_irqrestore(&kslots[slot_idx].lock, flags);

            push_free_idx(slot_idx);

            kfree_rcu(map, rcu);
            atomic_dec(&shared_mem->count);

            pr_info("IPC_monitor: Removed pgid=%d (slot=%d)\n", pgid, slot_idx);
            return 0;
        }
    }
    spin_unlock(&pgid_hash_lock);
    return -ENOENT;
}
EXPORT_SYMBOL(ipcmon_remove_pgid);

/* ---------- tracepoint handler ---------- */

static void tracepoint_sched_switch_handler(void *data, bool preempt,
                                            struct task_struct *prev,
                                            struct task_struct *next,
                                            unsigned int prev_state)
{
    int cpu = smp_processor_id();

    /* PREV state: only valid if previously armed for a monitored task */
    int prev_slot_idx = per_cpu(running_slot_idx, cpu);
    u32 prev_expected_gen = per_cpu(running_slot_gen, cpu);

    /* Decide whether NEXT is monitored (RCU lookup) */
    pid_t next_pgid = pid_nr(task_pgrp(next));
    struct pgid_map *map;
    int next_slot_idx = -1;
    u32 next_expected_gen = 0;

    rcu_read_lock();
    hash_for_each_possible_rcu(pgid_hash, map, hnode, next_pgid) {
        if (map->pgid == next_pgid) {
            next_slot_idx = map->slot_idx;
            next_expected_gen = map->gen;
            break;
        }
    }
    rcu_read_unlock();

    /* If neither prev needs end nor next needs start, do nothing (no PMU read). */
    if (prev_slot_idx < 0 && next_slot_idx < 0)
        return;

    /* Read PMU counters only when needed */
    {
        struct perf_event *cycles = per_cpu(cpu_cycles_event, cpu);
        struct perf_event *inst   = per_cpu(cpu_instructions_event, cpu);
        u64 enabled, running;
        u64 now_cycles, now_insts;

        if (!perf_event_is_valid(cycles) || !perf_event_is_valid(inst))
            goto disarm_and_out;

        now_cycles = perf_event_read_value(cycles, &enabled, &running);
        now_insts  = perf_event_read_value(inst,   &enabled, &running);

        /* 1) switch-out: accumulate for PREV if it was monitored */
        if (prev_slot_idx >= 0) {
            u64 start_cycles = per_cpu(running_start_cycles, cpu);
            u64 start_insts  = per_cpu(running_start_insts, cpu);
            u64 delta_cycles = delta_u64_wrap(now_cycles, start_cycles);
            u64 delta_insts  = delta_u64_wrap(now_insts,  start_insts);
            unsigned long flags;

            spin_lock_irqsave(&kslots[prev_slot_idx].lock, flags);

            /* Reject stale updates after slot reuse */
            if (kslots[prev_slot_idx].gen == prev_expected_gen) {
                if (kslots[prev_slot_idx].reset_flag) {
                    kslots[prev_slot_idx].cycles = delta_cycles;
                    kslots[prev_slot_idx].instructions = delta_insts;
                    kslots[prev_slot_idx].reset_flag = 0;
                } else {
                    kslots[prev_slot_idx].cycles += delta_cycles;
                    kslots[prev_slot_idx].instructions += delta_insts;
                }
                publish_snapshot_locked(prev_slot_idx);
            }

            spin_unlock_irqrestore(&kslots[prev_slot_idx].lock, flags);
        }

        /* 2) switch-in: arm NEXT if it is monitored, else disarm */
        if (next_slot_idx >= 0) {
            per_cpu(running_slot_idx, cpu) = next_slot_idx;
            per_cpu(running_slot_gen, cpu) = next_expected_gen;
            per_cpu(running_start_cycles, cpu) = now_cycles;
            per_cpu(running_start_insts,  cpu) = now_insts;
        } else {
            per_cpu(running_slot_idx, cpu) = -1;
            per_cpu(running_slot_gen, cpu) = 0;
        }

        return;
    }

disarm_and_out:
    /* If PMU read fails, be conservative: disarm if NEXT isn't monitored. */
    if (next_slot_idx < 0) {
        per_cpu(running_slot_idx, cpu) = -1;
        per_cpu(running_slot_gen, cpu) = 0;
    }
}

/* ---------- tracepoint discovery ---------- */

static void find_sched_switch_tracepoint(struct tracepoint *tp, void *priv)
{
    if (tp && tp->name && strcmp(tp->name, "sched_switch") == 0)
        sched_switch_tracepoint = tp;
}

/* ---------- mmap / ioctl ---------- */

static int ipc_mmap(struct file *filp, struct vm_area_struct *vma)
{
    unsigned long vma_size = vma->vm_end - vma->vm_start;
    unsigned long uaddr = vma->vm_start;
    unsigned long offset = 0;

    if (vma_size != shared_mem_size) {
        pr_err("IPC_monitor: mmap size mismatch (requested=%lu, expected=%zu)\n",
               vma_size, shared_mem_size);
        return -EINVAL;
    }

    vm_flags_set(vma, VM_IO | VM_DONTEXPAND | VM_DONTDUMP);

    while (offset < vma_size) {
        struct page *page = vmalloc_to_page((void *)((char *)shared_mem + offset));
        if (!page) {
            pr_err("IPC_monitor: vmalloc_to_page failed at offset=%lu\n", offset);
            return -EFAULT;
        }

        if (remap_pfn_range(vma,
                            uaddr + offset,
                            page_to_pfn(page),
                            PAGE_SIZE,
                            vma->vm_page_prot)) {
            pr_err("IPC_monitor: remap_pfn_range failed at offset=%lu\n", offset);
            return -EIO;
        }

        offset += PAGE_SIZE;
    }

    return 0;
}

static long ipc_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
    int i;

    switch (cmd) {
    case IPC_IOC_RESET_COUNTERS:
        /* Mark reset_flag in kernel slots; applied at next switch-out update */
        for (i = 0; i < MAX_SLOTS; i++) {
            unsigned long flags;

            if (!test_bit(i, shared_mem->active_mask))
                continue;

            spin_lock_irqsave(&kslots[i].lock, flags);
            if (kslots[i].pgid != 0)
                kslots[i].reset_flag = 1;
            spin_unlock_irqrestore(&kslots[i].lock, flags);
        }
        return 0;

    default:
        return -ENOTTY;
    }
}

static const struct file_operations fops = {
    .mmap           = ipc_mmap,
    .unlocked_ioctl = ipc_ioctl,
    .owner          = THIS_MODULE,
};

/* ---------- module init/exit ---------- */

static int __init IPC_monitor_init(void)
{
    int cpu, i;
    struct perf_event_attr cycles_attr, inst_attr;
    int ret;

    tail_index = 0;
    free_count = 0;
    sched_switch_tracepoint = NULL;
    sched_switch_registered = false;

    /* init kernel slot locks once */
    for (i = 0; i < MAX_SLOTS; i++)
        spin_lock_init(&kslots[i].lock);

    /* allocate userspace-visible shared memory (vmalloc space) */
    shared_mem_size = PAGE_ALIGN(sizeof(struct ipc_shared));
    shared_mem = vzalloc(shared_mem_size);
    if (!shared_mem)
        return -ENOMEM;

    atomic_set(&shared_mem->count, 0);
    bitmap_zero(shared_mem->active_mask, MAX_SLOTS);
    for (i = 0; i < MAX_SLOTS; i++) {
        shared_mem->slots[i].seq = 0;
        shared_mem->slots[i].cycles = 0;
        shared_mem->slots[i].instructions = 0;
        shared_mem->slots[i].pgid = -1;
    }

    pr_info("ipc_shared sizeof=%zu\n", sizeof(struct ipc_shared));
    pr_info("slot sizeof=%zu align=%zu\n",
            sizeof(struct pgid_slot_user),
            __alignof__(struct pgid_slot_user));
    pr_info("offset count=%zu active_mask=%zu slots=%zu\n",
            offsetof(struct ipc_shared, count),
            offsetof(struct ipc_shared, active_mask),
            offsetof(struct ipc_shared, slots));

    /* perf attrs */
    memset(&cycles_attr, 0, sizeof(cycles_attr));
    cycles_attr.type = PERF_TYPE_HARDWARE;
    cycles_attr.config = PERF_COUNT_HW_CPU_CYCLES;
    cycles_attr.size = sizeof(cycles_attr);
    cycles_attr.disabled = 1;

    memset(&inst_attr, 0, sizeof(inst_attr));
    inst_attr.type = PERF_TYPE_HARDWARE;
    inst_attr.config = PERF_COUNT_HW_INSTRUCTIONS;
    inst_attr.size = sizeof(inst_attr);
    inst_attr.disabled = 1;

    /* create per-cpu PMU events + init per-cpu state */
    for_each_online_cpu(cpu) {
        struct perf_event *event_cycles, *event_inst;

        event_cycles = perf_event_create_kernel_counter(&cycles_attr, cpu, NULL, NULL, NULL);
        if (IS_ERR(event_cycles)) {
            pr_err("Failed to create cycles event on CPU %d (err=%ld)\n",
                   cpu, PTR_ERR(event_cycles));
            goto fail_cleanup;
        }
        per_cpu(cpu_cycles_event, cpu) = event_cycles;
        perf_event_enable(event_cycles);

        event_inst = perf_event_create_kernel_counter(&inst_attr, cpu, NULL, NULL, NULL);
        if (IS_ERR(event_inst)) {
            pr_err("Failed to create instructions event on CPU %d (err=%ld)\n",
                   cpu, PTR_ERR(event_inst));
            goto fail_cleanup;
        }
        per_cpu(cpu_instructions_event, cpu) = event_inst;
        perf_event_enable(event_inst);

        /* per-cpu running state */
        per_cpu(running_slot_idx, cpu) = -1;
        per_cpu(running_slot_gen, cpu) = 0;
        per_cpu(running_start_cycles, cpu) = 0;
        per_cpu(running_start_insts, cpu) = 0;
    }

    /* register sched_switch tracepoint */
    for_each_kernel_tracepoint(find_sched_switch_tracepoint, NULL);
    if (!sched_switch_tracepoint) {
        pr_err("IPC_monitor: sched_switch tracepoint not found\n");
        goto fail_cleanup;
    }

    ret = tracepoint_probe_register(sched_switch_tracepoint,
                                    tracepoint_sched_switch_handler, NULL);
    if (ret < 0) {
        pr_err("IPC_monitor: Failed to register tracepoint (err=%d)\n", ret);
        goto fail_cleanup;
    }
    sched_switch_registered = true;

    /* char device */
    ret = alloc_chrdev_region(&dev_no, 0, 1, "IPC_monitor");
    if (ret < 0) {
        pr_err("IPC_monitor: alloc_chrdev_region failed (%d)\n", ret);
        goto fail_cleanup;
    }

    cdev_init(&ipc_cdev, &fops);
    ret = cdev_add(&ipc_cdev, dev_no, 1);
    if (ret < 0) {
        pr_err("IPC_monitor: cdev_add failed (%d)\n", ret);
        unregister_chrdev_region(dev_no, 1);
        goto fail_cleanup;
    }

    ipc_class = class_create("IPC_monitor_class");
    if (IS_ERR(ipc_class)) {
        pr_err("IPC_monitor: class_create failed\n");
        ipc_class = NULL;
        cdev_del(&ipc_cdev);
        unregister_chrdev_region(dev_no, 1);
        goto fail_cleanup;
    }

    ipc_device = device_create(ipc_class, NULL, dev_no, NULL, "IPC_monitor");
    if (IS_ERR(ipc_device)) {
        pr_err("IPC_monitor: device_create failed\n");
        ipc_device = NULL;
        class_destroy(ipc_class);
        ipc_class = NULL;
        cdev_del(&ipc_cdev);
        unregister_chrdev_region(dev_no, 1);
        goto fail_cleanup;
    }

    pr_info("IPC_monitor: loaded (/dev/IPC_monitor), shared_mem_size=%zu\n", shared_mem_size);
    return 0;

fail_cleanup:
    if (sched_switch_registered && sched_switch_tracepoint) {
        tracepoint_probe_unregister(sched_switch_tracepoint,
                                    tracepoint_sched_switch_handler, NULL);
        sched_switch_registered = false;
    }

    for_each_online_cpu(cpu) {
        if (per_cpu(cpu_cycles_event, cpu)) {
            perf_event_disable(per_cpu(cpu_cycles_event, cpu));
            perf_event_release_kernel(per_cpu(cpu_cycles_event, cpu));
            per_cpu(cpu_cycles_event, cpu) = NULL;
        }
        if (per_cpu(cpu_instructions_event, cpu)) {
            perf_event_disable(per_cpu(cpu_instructions_event, cpu));
            perf_event_release_kernel(per_cpu(cpu_instructions_event, cpu));
            per_cpu(cpu_instructions_event, cpu) = NULL;
        }
    }

    if (ipc_device) {
        device_destroy(ipc_class, dev_no);
        ipc_device = NULL;
    }
    if (ipc_class) {
        class_destroy(ipc_class);
        ipc_class = NULL;
    }

    if (shared_mem) {
        vfree(shared_mem);
        shared_mem = NULL;
    }

    return -ENODEV;
}

static void __exit IPC_monitor_exit(void)
{
    int cpu, bkt;
    struct pgid_map *map;
    struct hlist_node *tmp;

    if (sched_switch_registered && sched_switch_tracepoint) {
        tracepoint_probe_unregister(sched_switch_tracepoint,
                                    tracepoint_sched_switch_handler, NULL);
        sched_switch_registered = false;
    }

    for_each_online_cpu(cpu) {
        struct perf_event *cycles = per_cpu(cpu_cycles_event, cpu);
        struct perf_event *inst   = per_cpu(cpu_instructions_event, cpu);

        if (cycles) {
            perf_event_disable(cycles);
            perf_event_release_kernel(cycles);
            per_cpu(cpu_cycles_event, cpu) = NULL;
        }
        if (inst) {
            perf_event_disable(inst);
            perf_event_release_kernel(inst);
            per_cpu(cpu_instructions_event, cpu) = NULL;
        }
    }

    /* remove all maps */
    spin_lock(&pgid_hash_lock);
    hash_for_each_safe(pgid_hash, bkt, tmp, map, hnode) {
        clear_bit(map->slot_idx, shared_mem->active_mask);
        hash_del_rcu(&map->hnode);
        kfree_rcu(map, rcu);
    }
    spin_unlock(&pgid_hash_lock);
    synchronize_rcu();

    if (ipc_device) {
        device_destroy(ipc_class, dev_no);
        ipc_device = NULL;
    }
    if (ipc_class) {
        class_destroy(ipc_class);
        ipc_class = NULL;
    }

    cdev_del(&ipc_cdev);
    unregister_chrdev_region(dev_no, 1);

    if (shared_mem) {
        vfree(shared_mem);
        shared_mem = NULL;
    }

    pr_info("IPC_monitor: unloaded\n");
}

module_init(IPC_monitor_init);
module_exit(IPC_monitor_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Sanghyun Kim");
MODULE_DESCRIPTION("Per-PGID IPC monitoring via PMU and sched_switch tracepoint");