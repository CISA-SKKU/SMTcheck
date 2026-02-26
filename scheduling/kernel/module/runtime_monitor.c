/**
 * @file runtime_monitor.c
 * @brief Kernel module for monitoring long-running processes
 *
 * Implementation uses ACK-gated IPC registration:
 *  - When threshold is exceeded: send profiling request to userspace only
 *  - When userspace completes profiling and sends ACK via netlink: set profile_done=1
 *  - Timer callback checks (is_long_running && profile_done && !ipcmon_registered)
 *    and only then registers with IPC_monitor (ipcmon_add_pgid)
 */

#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>
#include <linux/timer.h>
#include <linux/slab.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/device.h>
#include <linux/ioctl.h>
#include <linux/pid.h>
#include <linux/spinlock.h>
#include <linux/time.h>
#include <linux/netlink.h>
#include <net/sock.h>
#include <linux/hashtable.h>
#include <linux/list.h>
#include <linux/ktime.h>

#include "IPC_monitor.h"

#define INTERVAL_MS 1000
#define DEVICE_NAME "runtime_monitor"
#define CLASS_NAME  "rtmon"
#define NETLINK_USER 31

struct my_pair {
    int pgid;
    int global_jobid;
    int worker_num;
};

/* ioctl command definitions */
#define RTMON_IOC_MAGIC 'k'
#define RTMON_IOC_ADD_PGID              _IOW(RTMON_IOC_MAGIC, 0, struct my_pair)
#define RTMON_IOC_REMOVE_PGID           _IOW(RTMON_IOC_MAGIC, 1, int)
#define RTMON_IOC_SET_THRESHOLD         _IOW(RTMON_IOC_MAGIC, 2, int)
#define RTMON_IOC_SET_DATA_LOADER_PID   _IOW(RTMON_IOC_MAGIC, 3, int)
#define RTMON_IOC_REQUEST_PROFILE       _IOW(RTMON_IOC_MAGIC, 4, int)
#define RTMON_IOC_MAXNR 5

/**
 * struct pgid_entry - Hash table entry for tracked PGIDs
 *
 * ACK-gated registration:
 *  - profile_done: Set when userspace profiling completion ACK is received
 */
struct pgid_entry {
    pid_t pgid;
    u64 start_time_ns;

    int need_send_request;
    int is_long_running;

    int profile_done;       /* Userspace profiling ACK gate */
    int ipcmon_registered;  /* Actual IPC_monitor registration status */

    int global_jobid;
    int worker_num;

    struct pid *pgid_pid;

    struct hlist_node hnode;
    struct list_head gc_node;
};

/* long-running threshold in seconds */
static int long_running_threshold = 3600;

/* userspace PID for netlink notifications */
static int data_loader_pid;

/* periodic monitoring timer */
static struct timer_list monitor_timer;

/* tracking table */
DEFINE_HASHTABLE(pgid_table, 10);
static DEFINE_SPINLOCK(pgid_table_lock);

/* char device */
static int major_number;
static struct class *rtmon_class;
static struct device *rtmon_device;

/* netlink */
static struct sock *nl_sk;

static bool is_valid_userspace_pid(int pid)
{
    return pid > 0;
}

static int send_to_user(const char *msg, int pid)
{
    struct sk_buff *skb;
    struct nlmsghdr *nlh;
    int msg_size;

    if (!nl_sk)
        return -ENOTCONN;
    if (!is_valid_userspace_pid(pid))
        return -EINVAL;

    msg_size = strlen(msg) + 1;

    skb = nlmsg_new(msg_size, GFP_KERNEL);
    if (!skb)
        return -ENOMEM;

    nlh = nlmsg_put(skb, 0, 0, NLMSG_DONE, msg_size, 0);
    if (!nlh) {
        kfree_skb(skb);
        return -ENOMEM;
    }

    memcpy(nlmsg_data(nlh), msg, msg_size);

    return netlink_unicast(nl_sk, skb, pid, MSG_DONTWAIT);
}

static struct pgid_entry *lookup_entry_locked(pid_t pgid)
{
    struct pgid_entry *entry;

    hash_for_each_possible(pgid_table, entry, hnode, pgid) {
        if (entry->pgid == pgid)
            return entry;
    }
    return NULL;
}

static struct pid *get_pgid_pidref(pid_t pgid)
{
    return find_get_pid(pgid);
}

static bool pgid_has_any_task(struct pid *pgid_pid)
{
    return pid_task(pgid_pid, PIDTYPE_PGID) != NULL;
}

/* ---------- ioctl handling ---------- */

static long device_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    struct my_pair pair;
    pid_t pgid;
    int pid;
    unsigned long flags;

    if (_IOC_TYPE(cmd) != RTMON_IOC_MAGIC)
        return -ENOTTY;
    if (_IOC_NR(cmd) > RTMON_IOC_MAXNR)
        return -ENOTTY;

    switch (cmd) {
    case RTMON_IOC_ADD_PGID: {
        struct pgid_entry *entry;
        struct pid *pgid_pid;

        if (copy_from_user(&pair, (struct my_pair __user *)arg, sizeof(pair)))
            return -EFAULT;

        if (pair.pgid <= 0)
            return -EINVAL;

        pgid = (pid_t)pair.pgid;

        pgid_pid = get_pgid_pidref(pgid);
        if (!pgid_pid)
            return -ESRCH;

        entry = kzalloc(sizeof(*entry), GFP_KERNEL);
        if (!entry) {
            put_pid(pgid_pid);
            return -ENOMEM;
        }

        INIT_LIST_HEAD(&entry->gc_node);
        entry->pgid = pgid;
        entry->pgid_pid = pgid_pid;
        entry->start_time_ns = ktime_get_ns();

        entry->need_send_request = 1;
        entry->is_long_running = 0;

        entry->profile_done = 0;        /* not yet profiled */
        entry->ipcmon_registered = 0;   /* not registered yet */

        entry->global_jobid = pair.global_jobid;
        entry->worker_num = pair.worker_num;

        spin_lock_irqsave(&pgid_table_lock, flags);
        if (lookup_entry_locked(pgid)) {
            spin_unlock_irqrestore(&pgid_table_lock, flags);
            put_pid(pgid_pid);
            kfree(entry);
            return -EEXIST;
        }
        hash_add(pgid_table, &entry->hnode, entry->pgid);
        spin_unlock_irqrestore(&pgid_table_lock, flags);

        pr_info("rt_monitor: Added PGID %d via ioctl (job=%d worker=%d)\n",
                pgid, entry->global_jobid, entry->worker_num);
        return 0;
    }

    case RTMON_IOC_REMOVE_PGID: {
        struct pgid_entry *entry;
        int ipcmon_registered;
        int worker_pgid;
        LIST_HEAD(to_free);

        if (copy_from_user(&pgid, (int __user *)arg, sizeof(pgid)))
            return -EFAULT;
        if (pgid <= 0)
            return -EINVAL;

        spin_lock_irqsave(&pgid_table_lock, flags);
        entry = lookup_entry_locked(pgid);
        if (!entry) {
            spin_unlock_irqrestore(&pgid_table_lock, flags);
            return -ENOENT;
        }

        ipcmon_registered = entry->ipcmon_registered;
        worker_pgid = entry->pgid;

        hash_del(&entry->hnode);
        list_add(&entry->gc_node, &to_free);
        spin_unlock_irqrestore(&pgid_table_lock, flags);

        if (ipcmon_registered) {
            int ret = ipcmon_remove_pgid(worker_pgid);
            if (ret < 0)
                pr_warn("rt_monitor: ipcmon_remove_pgid(%d) failed (err=%d)\n",
                        worker_pgid, ret);
        }

        while (!list_empty(&to_free)) {
            entry = list_first_entry(&to_free, struct pgid_entry, gc_node);
            list_del(&entry->gc_node);
            if (entry->pgid_pid)
                put_pid(entry->pgid_pid);
            kfree(entry);
        }

        pr_info("rt_monitor: Removed PGID %d via ioctl\n", pgid);
        return 0;
    }

    case RTMON_IOC_SET_THRESHOLD: {
        int new_thresh;

        if (copy_from_user(&new_thresh, (int __user *)arg, sizeof(new_thresh)))
            return -EFAULT;

        if (new_thresh <= 0)
            return -EINVAL;

        pr_info("rt_monitor: threshold %d sec -> %d sec\n",
                READ_ONCE(long_running_threshold), new_thresh);
        WRITE_ONCE(long_running_threshold, new_thresh);
        return 0;
    }

    case RTMON_IOC_SET_DATA_LOADER_PID: {
        int new_pid;

        if (copy_from_user(&new_pid, (int __user *)arg, sizeof(new_pid)))
            return -EFAULT;

        if (new_pid < 0)
            return -EINVAL;

        WRITE_ONCE(data_loader_pid, new_pid);
        pr_info("rt_monitor: data_loader_pid set to %d\n", new_pid);
        return 0;
    }

    case RTMON_IOC_REQUEST_PROFILE: {
        struct task_struct *task;
        struct pgid_entry *entry;

        if (copy_from_user(&pid, (int __user *)arg, sizeof(pid)))
            return -EFAULT;
        if (pid <= 0)
            return -EINVAL;

        task = pid_task(find_vpid(pid), PIDTYPE_PID);
        if (!task)
            return -ESRCH;

        pgid = pid_nr(task_pgrp(task));

        spin_lock_irqsave(&pgid_table_lock, flags);
        entry = lookup_entry_locked(pgid);
        if (entry)
            entry->need_send_request = 1;
        spin_unlock_irqrestore(&pgid_table_lock, flags);

        return entry ? 0 : -ENOENT;
    }

    default:
        return -ENOTTY;
    }
}

static const struct file_operations fops = {
    .owner          = THIS_MODULE,
    .unlocked_ioctl = device_ioctl,
};

/* ---------- timer callback (two-phase) ---------- */

struct pending_notify {
    pid_t pgid;
    u64 elapsed_sec;
    int global_jobid;
    struct list_head node;
};

struct pending_ipc {
    pid_t pgid;
    int global_jobid;
    int worker_num;
    bool do_add; /* true: add, false: remove */
    struct list_head node;
};

static void free_entry_list(struct list_head *to_free)
{
    struct pgid_entry *entry;

    while (!list_empty(to_free)) {
        entry = list_first_entry(to_free, struct pgid_entry, gc_node);
        list_del(&entry->gc_node);
        if (entry->pgid_pid)
            put_pid(entry->pgid_pid);
        kfree(entry);
    }
}

static void monitor_callback(struct timer_list *t)
{
    unsigned long flags;
    int bkt;
    struct pgid_entry *entry;
    struct hlist_node *tmp;

    LIST_HEAD(to_free);
    LIST_HEAD(to_notify);
    LIST_HEAD(to_ipc);

    u64 now_ns = ktime_get_ns();

    /* Phase 1: scan/update state under lock and build action lists. */
    spin_lock_irqsave(&pgid_table_lock, flags);

    hash_for_each_safe(pgid_table, bkt, tmp, entry, hnode) {
        u64 elapsed_sec;
        bool alive;

        if (!entry->pgid_pid) {
            hash_del(&entry->hnode);
            list_add(&entry->gc_node, &to_free);
            continue;
        }

        alive = pgid_has_any_task(entry->pgid_pid);
        if (!alive) {
            pr_info("rt_monitor: Auto-removed PGID %d (no tasks)\n", entry->pgid);

            /* If registered in IPC_monitor, remove it (defer to phase2). */
            if (entry->ipcmon_registered) {
                struct pending_ipc *p = kzalloc(sizeof(*p), GFP_ATOMIC);
                if (p) {
                    p->pgid = entry->pgid;
                    p->do_add = false;
                    list_add(&p->node, &to_ipc);
                }
            }

            hash_del(&entry->hnode);
            list_add(&entry->gc_node, &to_free);
            continue;
        }

        elapsed_sec = (now_ns - entry->start_time_ns) / NSEC_PER_SEC;

        /* threshold exceeded: only send request (no IPC registration yet) */
        if (!entry->is_long_running && elapsed_sec >= (u64)READ_ONCE(long_running_threshold)) {
            entry->is_long_running = 1;
            entry->need_send_request = 1;
        }

        /*
         * ACK-gated registration: Only register with IPC_monitor after
         * userspace sends ACK (profile_done=1). Actual registration
         * happens in phase 2.
         */
        if (entry->is_long_running && entry->profile_done && !entry->ipcmon_registered) {
            struct pending_ipc *p = kzalloc(sizeof(*p), GFP_ATOMIC);
            if (p) {
                p->pgid = entry->pgid;
                p->global_jobid = entry->global_jobid;
                p->worker_num = entry->worker_num;
                p->do_add = true;
                list_add(&p->node, &to_ipc);

                /* optimistic mark; rollback in phase2 on add failure */
                entry->ipcmon_registered = 1;
            }
        }

        if (entry->need_send_request) {
            struct pending_notify *n = kzalloc(sizeof(*n), GFP_ATOMIC);
            if (n) {
                n->pgid = entry->pgid;
                n->elapsed_sec = elapsed_sec;
                n->global_jobid = entry->global_jobid;
                list_add(&n->node, &to_notify);
                entry->need_send_request = 0;
            }
        }
    }

    spin_unlock_irqrestore(&pgid_table_lock, flags);

    /* Phase 2: perform actions outside lock. */

    /* IPC_monitor actions */
    while (!list_empty(&to_ipc)) {
        struct pending_ipc *p = list_first_entry(&to_ipc, struct pending_ipc, node);
        int ret;

        list_del(&p->node);

        if (p->do_add) {
            ret = ipcmon_add_pgid(p->pgid, p->global_jobid, p->worker_num);
            if (ret < 0 && ret != -EEXIST) {
                pr_warn("rt_monitor: ipcmon_add_pgid(%d) failed (err=%d)\n",
                        p->pgid, ret);

                /* Roll back optimistic mark */
                spin_lock_irqsave(&pgid_table_lock, flags);
                entry = lookup_entry_locked(p->pgid);
                if (entry)
                    entry->ipcmon_registered = 0;
                spin_unlock_irqrestore(&pgid_table_lock, flags);
            }
            /* -EEXIST: already registered, keep ipcmon_registered=1 */
        } else {
            ret = ipcmon_remove_pgid(p->pgid);
            if (ret < 0)
                pr_warn("rt_monitor: ipcmon_remove_pgid(%d) failed (err=%d)\n",
                        p->pgid, ret);
        }

        kfree(p);
    }

    /* Netlink notifications (profiling request) */
    while (!list_empty(&to_notify)) {
        struct pending_notify *n = list_first_entry(&to_notify, struct pending_notify, node);
        char buf[128];
        int ret;

        list_del(&n->node);

        snprintf(buf, sizeof(buf), "%d,%llu,%d",
                 n->pgid, (unsigned long long)n->elapsed_sec, n->global_jobid);

        ret = send_to_user(buf, READ_ONCE(data_loader_pid));
        if (ret < 0) {
            pr_debug("rt_monitor: netlink send failed (err=%d)\n", ret);

            if (ret == -EAGAIN || ret == -ENOBUFS) {
                spin_lock_irqsave(&pgid_table_lock, flags);
                entry = lookup_entry_locked(n->pgid);
                if (entry)
                    entry->need_send_request = 1;
                spin_unlock_irqrestore(&pgid_table_lock, flags);
            }
        }

        kfree(n);
    }

    /* Free removed entries */
    free_entry_list(&to_free);

    /* Reschedule timer */
    mod_timer(&monitor_timer, jiffies + msecs_to_jiffies(INTERVAL_MS));
}

/* ---------- netlink receive: userspace ACK (profiling done) ---------- */

static void nl_recv_msg(struct sk_buff *skb)
{
    struct nlmsghdr *nlh;
    pid_t pgid;
    unsigned long flags;
    struct pgid_entry *entry;

    pr_info("rt_monitor: nl_recv_msg called\n");

    if (!skb)
        return;

    nlh = (struct nlmsghdr *)skb->data;
    if (!nlh)
        return;

    if (nlmsg_len(nlh) < sizeof(int))
        return;

    pgid = *(int *)nlmsg_data(nlh);
    if (pgid <= 0)
        return;

    /*
     * ACK-gated registration: This is the profiling completion ACK.
     * We don't call ipcmon_add directly here.
     * The timer callback will see profile_done=1 and register in phase 2.
     */
    spin_lock_irqsave(&pgid_table_lock, flags);
    entry = lookup_entry_locked(pgid);
    if (entry) {
        entry->profile_done = 1;
        entry->is_long_running = 1;

        /* Debug log (can be removed if not needed) */
        pr_info("rt_monitor: profiling done ACK received for PGID %d\n", pgid);
    }
    spin_unlock_irqrestore(&pgid_table_lock, flags);
}

/* ---------- module init/exit ---------- */

static int __init monitor_init(void)
{
    struct netlink_kernel_cfg cfg = {
        .input = nl_recv_msg,
    };

    major_number = register_chrdev(0, DEVICE_NAME, &fops);
    if (major_number < 0) {
        pr_err("rt_monitor: failed to register chrdev\n");
        return major_number;
    }

    rtmon_class = class_create(CLASS_NAME);
    if (IS_ERR(rtmon_class)) {
        pr_err("rt_monitor: failed to create class\n");
        unregister_chrdev(major_number, DEVICE_NAME);
        return PTR_ERR(rtmon_class);
    }

    rtmon_device = device_create(rtmon_class, NULL, MKDEV(major_number, 0), NULL, DEVICE_NAME);
    if (IS_ERR(rtmon_device)) {
        pr_err("rt_monitor: failed to create device\n");
        class_destroy(rtmon_class);
        unregister_chrdev(major_number, DEVICE_NAME);
        return PTR_ERR(rtmon_device);
    }

    nl_sk = netlink_kernel_create(&init_net, NETLINK_USER, &cfg);
    if (!nl_sk) {
        pr_err("rt_monitor: netlink_kernel_create failed\n");
        device_destroy(rtmon_class, MKDEV(major_number, 0));
        class_destroy(rtmon_class);
        unregister_chrdev(major_number, DEVICE_NAME);
        return -ENOMEM;
    }

    timer_setup(&monitor_timer, monitor_callback, 0);
    mod_timer(&monitor_timer, jiffies + msecs_to_jiffies(INTERVAL_MS));

    pr_info("rt_monitor: loaded (/dev/%s), threshold=%d sec\n",
            DEVICE_NAME, long_running_threshold);
    return 0;
}

static void __exit monitor_exit(void)
{
    unsigned long flags;
    int bkt;
    struct pgid_entry *entry;
    struct hlist_node *tmp;
    LIST_HEAD(to_free);
    LIST_HEAD(to_ipc);

    del_timer_sync(&monitor_timer);

    if (nl_sk) {
        netlink_kernel_release(nl_sk);
        nl_sk = NULL;
    }

    /* Unlink all entries under lock, build removal/free lists. */
    spin_lock_irqsave(&pgid_table_lock, flags);
    hash_for_each_safe(pgid_table, bkt, tmp, entry, hnode) {
        if (entry->ipcmon_registered) {
            struct pending_ipc *p = kzalloc(sizeof(*p), GFP_ATOMIC);
            if (p) {
                p->pgid = entry->pgid;
                p->do_add = false;
                list_add(&p->node, &to_ipc);
            }
        }

        hash_del(&entry->hnode);
        list_add(&entry->gc_node, &to_free);
    }
    spin_unlock_irqrestore(&pgid_table_lock, flags);

    /* Remove from IPC_monitor outside lock. */
    while (!list_empty(&to_ipc)) {
        struct pending_ipc *p = list_first_entry(&to_ipc, struct pending_ipc, node);
        list_del(&p->node);

        if (ipcmon_remove_pgid(p->pgid) < 0)
            pr_debug("rt_monitor: ipcmon_remove_pgid(%d) failed during exit\n", p->pgid);

        kfree(p);
    }

    free_entry_list(&to_free);

    device_destroy(rtmon_class, MKDEV(major_number, 0));
    class_destroy(rtmon_class);
    unregister_chrdev(major_number, DEVICE_NAME);

    pr_info("rt_monitor: unloaded\n");
}

module_init(monitor_init);
module_exit(monitor_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Sanghyun Kim");
MODULE_DESCRIPTION("Long-running process detection with ACK-gated IPC_monitor registration");
MODULE_SOFTDEP("pre: IPC_monitor");