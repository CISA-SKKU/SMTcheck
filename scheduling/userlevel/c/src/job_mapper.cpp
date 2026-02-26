// =============================================================================
// Job Mapper - SMT-aware thread scheduling and CPU affinity management
// =============================================================================

// =============================================================================
// Standard Library Headers
// =============================================================================
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <errno.h>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <queue>
#include <random>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <thread>
#include <unistd.h>
#include <unordered_map>
#include <unordered_set>
#include <vector>

// =============================================================================
// Third-party Headers
// =============================================================================
#include <linux/types.h>
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// =============================================================================
// Local Headers
// =============================================================================
#include "job_mapper.h"

// =============================================================================
// Namespace Aliases
// =============================================================================
namespace py = pybind11;
namespace fs = std::filesystem;

// =============================================================================
// Constants and Macros
// =============================================================================
#define MAX_SLOTS 4096
#define PAGE_SIZE 4096
#define LOCKUP_LENGTH 5
#define BITS_PER_LONG (sizeof(long) * CHAR_BIT)
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define BITS_TO_LONGS(nr) DIV_ROUND_UP(nr, BITS_PER_LONG)

#define IPC_IOC_MAGIC 'I'
#define IPC_IOC_RESET_COUNTERS _IO(IPC_IOC_MAGIC, 0)

#ifndef LOGICAL_CORE_NUM
    #define LOGICAL_CORE_NUM 16
#endif

#ifndef PHYSICAL_CORE_NUM
    #define PHYSICAL_CORE_NUM 8
#endif

// Debug print macros for conditional compilation
#ifdef DEBUG
    #define DEBUG_PRINT(x) do { std::cout << x << std::endl; } while(0)
    #define DEBUG_PRINT_INLINE(x) do { std::cout << x; } while(0)
#else
    #define DEBUG_PRINT(x) do {} while(0)
    #define DEBUG_PRINT_INLINE(x) do {} while(0)
#endif

// =============================================================================
// Type Definitions and Structures
// =============================================================================

// Tuple representing a process group with its global job identifier
struct PgidTuple {
    int pgid;
    int global_jobid;
    int worker_num;  // Number of workers for this specific pgid
};

// Process group structure with constructors
struct PgidStruct {
    int pgid;
    int global_jobid;
    int worker_num;
    
    PgidStruct() = default;
    PgidStruct(int p, int g, int w) : pgid(p), global_jobid(g), worker_num(w) {}
};

// Represents a pair of process groups with their compatibility score
struct Pair {
    struct PgidTuple first;
    struct PgidTuple second;
    double score;
    
    Pair(const PgidTuple& f, const PgidTuple& s, double sc)
        : first(f), second(s), score(sc) {}

    bool operator<(const Pair& other) const {
        return score < other.score;
    }

    bool operator==(const Pair& other) const {
        return first.global_jobid == other.first.global_jobid 
            && second.global_jobid == other.second.global_jobid;
    }
};

// Represents a CPU core with scheduling metadata
struct CoreTuple {
    int core_id;
    int thread_num;
    double total_score;

    bool operator<(const CoreTuple& other) const {
        if (thread_num != other.thread_num) {
            return thread_num < other.thread_num; 
        }
        return total_score < other.total_score;
    }

    bool operator>(const CoreTuple& other) const {
        if (thread_num != other.thread_num) {
            return thread_num > other.thread_num;
        }
        return total_score > other.total_score;
    }
};

// Wrapper for cpu_set_t with automatic initialization
struct CpuSet {
    cpu_set_t set;

    CpuSet() { CPU_ZERO(&set); }
};

// Shared memory slot for IPC monitoring
struct pgid_slot {
    uint32_t seq;
    int32_t pgid;
    int32_t global_jobid;
    int32_t worker_num;
    uint64_t cycles;
    uint64_t instructions;
} __attribute__((aligned(16)));

// Shared memory structure for IPC (Instructions Per Cycle) monitoring
struct ipc_shared {
    int32_t count;
    unsigned long active_mask[BITS_TO_LONGS(MAX_SLOTS)];
    struct pgid_slot slots[MAX_SLOTS];
};

// =============================================================================
// Global Variables
// =============================================================================

// Shared memory state
static struct ipc_shared *shared = NULL;
static size_t mmap_size = 0;
static size_t base_size = 0;
static int fd_ipc = -1;

static constexpr int ACTIVE_MASK_SIZE = (MAX_SLOTS + BITS_PER_LONG - 1) / BITS_PER_LONG;

// Core topology and scoring maps
static std::unordered_map<int, std::pair<int, int>> sibling_core_map;
static std::unordered_map<uint64_t, double> score_map;
static std::unordered_map<int, double> single_IPC_map;

// Placeholder pair for empty slots
static Pair holder = {{-1, -1}, {-1, -1}, 0};

// =============================================================================
// Utility Functions
// =============================================================================

// Create a unique 64-bit key from two 32-bit job IDs
inline static uint64_t make_key(uint32_t i, uint32_t j) noexcept {
    if (i > j) {
        std::swap(i, j);
    }
    return (static_cast<uint64_t>(i) << 32) | static_cast<uint64_t>(j);
}

// Compare two floating-point numbers with epsilon tolerance
bool nearly_equal(double a, double b, double eps = 1e-8) {
    return std::fabs(a - b) < eps;
}

static inline int ctz_ulong(unsigned long x) {
#if defined(__GNUC__) || defined(__clang__)
    return __builtin_ctzl(x);
#else
    // fallback: slower but safe
    int n = 0;
    while ((x & 1UL) == 0) { x >>= 1; n++; }
    return n;
#endif
}

int reset_ipc_counters() {
    if (fd_ipc < 0) {
        errno = EBADF;
        return -1;
    }
    return ioctl(fd_ipc, IPC_IOC_RESET_COUNTERS);
}

// =============================================================================
// Process and Thread Management
// =============================================================================

// Get all thread IDs for a given process ID
std::vector<int> get_threads(int pid) {
    std::vector<int> tids;
    fs::path task_dir = "/proc/" + std::to_string(pid) + "/task";

    for (auto& entry : fs::directory_iterator(task_dir)) {
        if (entry.is_directory()) {
            try {
                int tid = std::stoi(entry.path().filename().string());
                tids.push_back(tid);
            } catch (...) {
                continue;
            }
        }
    }
    return tids;
}

// Get child process IDs for a given process
std::vector<int> get_children(int pid) {
    std::vector<int> children;
    std::ifstream f("/proc/" + std::to_string(pid) + "/task/" 
                    + std::to_string(pid) + "/children");
    int child_pid;
    while (f >> child_pid) {
        children.push_back(child_pid);
    }
    return children;
}

// Recursively set CPU affinity for a process group and all its children
void set_pgid_affinity(int pgid, cpu_set_t cpu_set) {
    auto tids = get_threads(pgid);
    for (int tid : tids) {
        if (sched_setaffinity(tid, sizeof(cpu_set_t), &cpu_set) == -1) {
            std::cerr << "Failed to set CPU affinity for TID " << tid << "\n";
        }
    }

    auto children = get_children(pgid);
    for (int child : children) {
        set_pgid_affinity(child, cpu_set);
    }
}

// =============================================================================
// Pair Selection Algorithm
// =============================================================================

// Find optimal pair combinations using greedy algorithm with local search
static std::vector<Pair> get_best_combinations(const std::deque<Pair>& pairs, 
                                               std::unordered_map<int, int> counter, 
                                               int thread_num) {
    // Lambda to find index of maximum value among three
    auto argmax3 = [](double a, double b, double c) -> int {
        if (a >= b && a >= c) return 0;
        else if (b >= a && b >= c) return 1;
        else return 2;
    };

    const size_t threshold = thread_num >> 1;
    DEBUG_PRINT("thread_num: " << thread_num);
    DEBUG_PRINT("Selecting up to " << threshold << " pairs from " << pairs.size() << " candidates.");
    std::vector<Pair> best_pairs;
    
    // Per-pgid counter to track how many workers of each pgid have been assigned
    // This prevents assigning more workers than a pgid actually has
    std::unordered_map<int, int> pgid_counter;

    #ifdef TIME
        auto start_time = std::chrono::high_resolution_clock::now();
    #endif

    // Greedy selection phase: pick pairs based on score and availability
    for (const auto& pair : pairs) {
        const struct PgidTuple first = pair.first;
        const struct PgidTuple second = pair.second;

        if (first.global_jobid == second.global_jobid) {
            // Same job pairing (co-locate threads of same process)
            // Check both global jobid counter and per-pgid counter
            if (counter[first.global_jobid] < 2) continue;
            
            // Check per-pgid limit: need at least 2 workers from this pgid
            int pgid_remaining = first.worker_num - pgid_counter[first.pgid];
            if (pgid_remaining < 2) continue;
            
            // Can only pair up to min(global available / 2, pgid remaining / 2)
            int num_available = std::min(counter[first.global_jobid] / 2, pgid_remaining / 2);
            if (num_available < 1) continue;
            
            counter[first.global_jobid] -= (num_available * 2);
            pgid_counter[first.pgid] += (num_available * 2);
            
            for (int i = 0; i < num_available; i++) {
                best_pairs.push_back(pair);
            }
            DEBUG_PRINT("Same-job Pair: (" << first.global_jobid << "[pgid=" << first.pgid << "]), "
                        << "Score: " << pair.score << ", Available: " << num_available
                        << ", Counter: " << counter[first.global_jobid]
                        << ", PgidCounter: " << pgid_counter[first.pgid] << "/" << first.worker_num);
        } else {
            // Different job pairing
            if (counter[first.global_jobid] < 1 || counter[second.global_jobid] < 1) continue;
            
            // Check per-pgid limits for both pgids
            int first_pgid_remaining = first.worker_num - pgid_counter[first.pgid];
            int second_pgid_remaining = second.worker_num - pgid_counter[second.pgid];
            if (first_pgid_remaining < 1 || second_pgid_remaining < 1) continue;
            
            // Can only pair up to min of all constraints
            int num_available = std::min({
                counter[first.global_jobid],
                counter[second.global_jobid],
                first_pgid_remaining,
                second_pgid_remaining
            });
            if (num_available < 1) continue;
            
            DEBUG_PRINT("Diff-job Pair: (" << first.global_jobid << "[pgid=" << first.pgid << "], "
                        << second.global_jobid << "[pgid=" << second.pgid << "]), "
                        << "Score: " << pair.score << ", Available: " << num_available
                        << ", Counters: (" << counter[first.global_jobid] << ", " << counter[second.global_jobid] << ")"
                        << ", PgidCounters: (" << pgid_counter[first.pgid] << "/" << first.worker_num
                        << ", " << pgid_counter[second.pgid] << "/" << second.worker_num << ")");
            
            counter[first.global_jobid] -= num_available;
            counter[second.global_jobid] -= num_available;
            pgid_counter[first.pgid] += num_available;
            pgid_counter[second.pgid] += num_available;
            
            for (int i = 0; i < num_available; i++) {
                best_pairs.push_back(pair);
            }
        }

        if (best_pairs.size() >= threshold) {
            best_pairs.erase(best_pairs.begin() + threshold, best_pairs.end());
            DEBUG_PRINT("Reached threshold: " << threshold);
            break;
        }
    }

    #ifdef TIME
        auto end_time = std::chrono::high_resolution_clock::now();
        std::cout << "Time:greedy1," 
                  << std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count() 
                  << std::endl;
    #endif

    // Local search phase: try to improve pairs by swapping
    #ifdef TIME
        start_time = std::chrono::high_resolution_clock::now();
    #endif

    std::unordered_set<uint64_t> no_swaps;

    for (size_t iter_count = 0; iter_count < 2; ++iter_count) {
        for (size_t i = 0; i < best_pairs.size(); ++i) {
            for (size_t j = i + 1; j < best_pairs.size(); ++j) {
                Pair& old_pair1 = best_pairs[i];
                Pair& old_pair2 = best_pairs[j];
                double old_score = old_pair1.score + old_pair2.score;
                uint64_t key = std::bit_cast<uint64_t>(old_score);

                // Skip if we already know this configuration cannot be improved
                if (no_swaps.find(key) != no_swaps.end()) {
                    continue;
                }

                // Calculate alternative pairing scores
                double pair1_score = score_map.at(make_key(old_pair1.first.global_jobid, 
                                                            old_pair2.first.global_jobid));
                double pair2_score = score_map.at(make_key(old_pair1.second.global_jobid, 
                                                            old_pair2.second.global_jobid));
                double new_score1 = pair1_score + pair2_score;

                double pair3_score = score_map.at(make_key(old_pair1.first.global_jobid, 
                                                            old_pair2.second.global_jobid));
                double pair4_score = score_map.at(make_key(old_pair1.second.global_jobid, 
                                                            old_pair2.first.global_jobid));
                double new_score2 = pair3_score + pair4_score;

                int max_index = argmax3(old_score, new_score1, new_score2);

                switch (max_index) {
                    case 0:
                        // Keep old pairs - mark as not worth swapping
                        no_swaps.insert(key);
                        break;
                    case 1: {
                        // Replace with new_pair1 and new_pair2
                        auto temp_second = old_pair1.second;
                        old_pair1 = {old_pair1.first, old_pair2.first, pair1_score};
                        old_pair2 = {temp_second, old_pair2.second, pair2_score};
                        break;
                    }
                    case 2: {
                        // Replace with new_pair3 and new_pair4
                        auto temp_first = old_pair1.first;
                        auto temp_second = old_pair1.second;
                        old_pair1 = {temp_first, old_pair2.second, pair3_score};
                        old_pair2 = {temp_second, old_pair2.first, pair4_score};
                        break;
                    }
                }
            }
        }
    }

    #ifdef TIME
        end_time = std::chrono::high_resolution_clock::now();
        std::cout << "Time:greedy2," 
                  << std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count() 
                  << std::endl;
    #endif

    // Sort pairs by score in descending order
    std::sort(best_pairs.begin(), best_pairs.end(), [](const Pair& a, const Pair& b) {
        return a.score > b.score;
    });

    return best_pairs;
}

// =============================================================================
// Score Calculation Functions
// =============================================================================

// Calculate total score for a list of pairs
static double sum_scores(const std::vector<Pair>& pairs) {
    double total_score = 0.0;
    for (const auto& pair : pairs) {
        total_score += pair.score;
    }
    return total_score;
}

// =============================================================================
// Target PGID Acquisition
// =============================================================================

// Get target process groups from shared memory
static std::vector<struct PgidStruct> get_target_pgids(int& thread_num, int& remain) {
    std::vector<struct PgidStruct> target_pgids;
    int n = 0;

    // Scan only active slot indices based on active_mask
    for (int word = 0; word < ACTIVE_MASK_SIZE; word++) {
        volatile unsigned long bits = shared->active_mask[word];
        DEBUG_PRINT_INLINE("Word " << word << ": bits = " << std::hex << bits << std::dec << "\n");
        while (bits) {
            int bit = ctz_ulong(bits);               // rightmost set bit index [0..BITS_PER_LONG-1]
            int idx = word * BITS_PER_LONG + bit;    // slot index

            // Move to next set bit
            bits &= (bits - 1);

            if (idx >= MAX_SLOTS) continue;

            const auto& s = shared->slots[idx];

            if (s.worker_num <= 0) continue;

            n += s.worker_num;
            target_pgids.push_back({ s.pgid, s.global_jobid, s.worker_num });
        }
    }

    remain = (LOGICAL_CORE_NUM - (n % LOGICAL_CORE_NUM)) % LOGICAL_CORE_NUM;
    thread_num = n + remain;

    target_pgids.push_back({-1, -1, remain});  // Add empty slot placeholder
    return target_pgids;
}

// Generate test process groups for benchmarking
static std::vector<struct PgidStruct> gen_test_pgids(int n, int& remain) {
    const int average_thread_count = 2;
    std::vector<struct PgidStruct> target_pgids;

    int num_full_pgids = n / average_thread_count;
    int remaining_threads = n % average_thread_count;

    // Generate full PGIDs
    for (int i = 0; i < num_full_pgids; i++) {
        target_pgids.push_back({i, i, average_thread_count});
    }
    
    // Add PGID for remaining threads
    if (remaining_threads > 0) {
        target_pgids.push_back({num_full_pgids, num_full_pgids, remaining_threads});
    }

    remain = (LOGICAL_CORE_NUM - (n % LOGICAL_CORE_NUM)) % LOGICAL_CORE_NUM;
    target_pgids.push_back({-1, -1, remain}); // Add empty slot placeholder
    return target_pgids;
}

// =============================================================================
// Runqueue Evaluation
// =============================================================================

// Evaluate the compatibility score of adding a job to an existing runqueue
static double evaluate_runqueue(const std::vector<PgidTuple>& runqueue, 
                                 int new_jobid) {
    double score = 0.0;
    int count = 0;
    static constexpr int MAX_EVAL_COUNT = 5;
    for (const auto& pgid : runqueue) {
        score += score_map.at(make_key(new_jobid, pgid.global_jobid));
        count++;
        if(count >= MAX_EVAL_COUNT) {
            break;
        }
    }
    return score;
}

// =============================================================================
// CPU Affinity Assignment
// =============================================================================

// Assign pairs to physical cores and generate CPU affinity masks
static std::unordered_map<int, CpuSet> set_cpu_mask(const std::vector<Pair>& pairs) {
    std::priority_queue<CoreTuple, std::vector<CoreTuple>, std::greater<CoreTuple>> pq;
    std::unordered_map<int, CpuSet> cpu_sets;
    std::vector<std::vector<PgidTuple>> runqueues(LOGICAL_CORE_NUM);

    // Initialize priority queue with all physical cores
    for (int i = 0; i < PHYSICAL_CORE_NUM; ++i) {
        pq.push({i, 0, 0.0});
    }

    // Assign pairs to cores
    for (int i = 0; i < (int)pairs.size(); ++i) {
        DEBUG_PRINT("Processing pair " << i + 1 << "/" << pairs.size());

        const Pair& pair = pairs[i];
        CoreTuple core = pq.top();
        pq.pop();

        int physical_core_id = core.core_id;
        int logical_core_id0 = sibling_core_map[physical_core_id].first;
        int logical_core_id1 = sibling_core_map[physical_core_id].second;

        DEBUG_PRINT(logical_core_id0 << " - " << logical_core_id1 << " : " 
                    << pair.first.global_jobid << " - " << pair.second.global_jobid 
                    << " : " << pair.score);

        core.thread_num++;

        std::vector<PgidTuple>& runqueue0 = runqueues[logical_core_id0];
        std::vector<PgidTuple>& runqueue1 = runqueues[logical_core_id1];

        // Evaluate both possible assignments to minimize interference
        double score0 = evaluate_runqueue(runqueue0, pair.first.global_jobid) 
                      + evaluate_runqueue(runqueue1, pair.second.global_jobid);
        double score1 = evaluate_runqueue(runqueue0, pair.second.global_jobid) 
                      + evaluate_runqueue(runqueue1, pair.first.global_jobid);
        
        // Choose assignment with higher compatibility score
        if (score0 >= score1) {
            runqueue1.push_back(pair.first);
            runqueue0.push_back(pair.second);
        } else {
            runqueue0.push_back(pair.first);
            runqueue1.push_back(pair.second);
        }
        core.total_score += pair.score;

        pq.push(core);
    }

    // Build CPU sets from runqueue assignments
    for (int i = 0; i < LOGICAL_CORE_NUM; ++i) {
        DEBUG_PRINT("Core " << i << " runqueue size: " << runqueues[i].size());
        for (auto& pgid_tuple : runqueues[i]) {
            DEBUG_PRINT("Core " << i << ": PGID = " << pgid_tuple.pgid 
                        << ", JobID = " << pgid_tuple.global_jobid);
            if (pgid_tuple.global_jobid == -1) continue;
            DEBUG_PRINT("Core " << i << ": JobID = " << pgid_tuple.global_jobid);
            CPU_SET(i, &cpu_sets[pgid_tuple.pgid].set);
        }
    }
    
    #ifdef DEBUG
    std::cout << "[SET_CPU_MASK] Generated CPU Affinity Masks:\n";
    for (const auto& [pgid, cpu_set] : cpu_sets) {
        std::cout << "  pgid=" << pgid << " -> CPUs: ";
        for (int cpu = 0; cpu < LOGICAL_CORE_NUM; ++cpu) {
            if (CPU_ISSET(cpu, &cpu_set.set)) {
                std::cout << cpu << " ";
            }
        }
        std::cout << std::endl;
    }
    #endif

    #ifdef DEBUG
    for (int i = 0; i < PHYSICAL_CORE_NUM; i++) {
        CoreTuple core = pq.top();
        pq.pop();
        std::cout << "Core " << core.core_id << ": Threads = " << core.thread_num 
                  << ", Total Score = " << core.total_score << std::endl;
    }
    #endif

    return cpu_sets;
}

static inline bool read_slot_consistent(
    const pgid_slot& slot,
    int& pgid,
    int& global_jobid,
    uint64_t& cycles,
    uint64_t& insts
) {
    uint32_t s1, s2;
    do {
        s1 = slot.seq;
        if (s1 & 1)
            continue;

        // acquire semantics required
        std::atomic_thread_fence(std::memory_order_acquire);

        pgid = slot.pgid;
        global_jobid = slot.global_jobid;
        cycles = slot.cycles;
        insts = slot.instructions;

        std::atomic_thread_fence(std::memory_order_acquire);
        s2 = slot.seq;
    } while (s1 != s2 || (s2 & 1));

    return true;
}

// =============================================================================
// Scheduling Functions
// =============================================================================

// Main scheduling function with runtime evaluation
static void schedule() {
    DEBUG_PRINT("Scheduling started.");
    int remain, thread_num;
    std::unordered_map<int, int> counter;       
    std::deque<Pair> pairs;
    
    // Find target workloads
    #ifdef TIME
    int64_t total_time = 0;
    auto start_time = std::chrono::high_resolution_clock::now();
    auto end_time = std::chrono::high_resolution_clock::now();
    start_time = std::chrono::high_resolution_clock::now();
    #endif
    std::vector<struct PgidStruct> target_pgids = get_target_pgids(thread_num, remain);

    if(thread_num == 0) {
        DEBUG_PRINT("No workloads to schedule.");
        return;
    }

    DEBUG_PRINT("Total workloads (including empty): " << thread_num << ", remain: " << remain);
    #ifdef TIME
        end_time = std::chrono::high_resolution_clock::now();
        total_time += std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count();
        std::cout << "Time:find_target," << std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count() << std::endl;
    #endif

    // Build workload pairs buffer
    #ifdef TIME
        start_time = std::chrono::high_resolution_clock::now();
    #endif
    for(int i=0; i< (int)target_pgids.size(); ++i) {
        counter[target_pgids[i].global_jobid] += target_pgids[i].worker_num;
        const struct PgidStruct& pgid_struct0 = target_pgids[i];
        // Include worker_num in PgidTuple for per-pgid tracking in get_best_combinations
        struct PgidTuple workload0 = {pgid_struct0.pgid, pgid_struct0.global_jobid, pgid_struct0.worker_num};
        if(pgid_struct0.worker_num >= 2) {
            const double score = score_map.at(make_key(workload0.global_jobid, workload0.global_jobid));
            pairs.push_back(Pair{workload0, workload0, score});
        }
            
        for(int j=i+1; j < (int)target_pgids.size(); ++j) {
            const struct PgidStruct& pgid_struct1 = target_pgids[j];
            struct PgidTuple workload1 = {pgid_struct1.pgid, pgid_struct1.global_jobid, pgid_struct1.worker_num};
            const double score = score_map.at(make_key(workload0.global_jobid, workload1.global_jobid));
            pairs.push_back(Pair{workload0, workload1, score});
        }
    }

    #ifdef TIME
        end_time = std::chrono::high_resolution_clock::now();
        total_time += std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count();
        std::cout << "Time:gen_pair_list," << std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count() << std::endl;
    #endif

    #ifdef DEBUG
    for(auto i: counter) {
        DEBUG_PRINT("Workload " << i.first << ": count = " << i.second);
    }
    #endif
    #ifdef TIME
        start_time = std::chrono::high_resolution_clock::now();
    #endif
    std::sort(pairs.begin(), pairs.end(),[](const Pair& a, const Pair& b) {
        return a.score > b.score; // descending order
    });
    #ifdef TIME
        end_time = std::chrono::high_resolution_clock::now();
        total_time += std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count();
        std::cout << "Time:score_sort," << std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count() << std::endl;
    #endif

    int try_count = 0;
    int max_tries = 100;
    int entry_count = 0;
    const int max_entries = 3;
    double prev_scores[max_entries+1] = {0.0};
    std::vector<std::unordered_map<int, CpuSet>> try_cpu_masks(max_entries+1);
    int threshold = thread_num >> 1;
    while(try_count < max_tries){
        try_count++;
        DEBUG_PRINT("try_count: " << try_count);
        // greedy
        #ifdef TIME
            start_time = std::chrono::high_resolution_clock::now();
        #endif
        std::vector<Pair> best_pairs = get_best_combinations(pairs, counter, thread_num);

        auto it = std::find_if(pairs.begin() + 1, pairs.end(),
                       [&](const auto& entry) {
                           return entry != pairs.front();
                       });
        std::rotate(pairs.begin(), it, pairs.end());
        auto dist = std::distance(pairs.begin(), it);
        DEBUG_PRINT("rotate by " << dist << " positions");

        if (best_pairs.size() != (size_t)threshold) {
            DEBUG_PRINT("Warning: best_pairs size (" << best_pairs.size() 
                        << ") does not match threshold (" << threshold << ")");
            continue;
        }
        
        DEBUG_PRINT("Best pairs size: " << best_pairs.size());
        #ifdef TIME
            end_time = std::chrono::high_resolution_clock::now();
            total_time += std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count();
            std::cout << "Time:greedy," << std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count() << std::endl;
        #endif
        if(entry_count == 0) { // Initial random baseline
            for(int i=1; i<=max_entries; i++) {
                std::vector<Pair> random_pairs = best_pairs;
                std::shuffle(random_pairs.begin(), random_pairs.end(), std::mt19937{std::random_device{}()});
                auto cpu_mask = set_cpu_mask(random_pairs);
                try_cpu_masks[i] = cpu_mask;
            }
        }

        double total_score = sum_scores(best_pairs);
        bool is_new_score = true;

        for(int i=0; i<entry_count; ++i) {
            if(nearly_equal(total_score, prev_scores[i])) {
                is_new_score = false;
                DEBUG_PRINT("Same score as previous try: (" << entry_count << ", " << i << ")" << total_score << ", " << prev_scores[i]);
                break;
            }
        }
        if(is_new_score) prev_scores[entry_count] = total_score;
        else continue;
        #ifdef TIME
            start_time = std::chrono::high_resolution_clock::now();
        #endif
        auto cpu_mask = set_cpu_mask(best_pairs);
        #ifdef TIME
            end_time = std::chrono::high_resolution_clock::now();
            total_time += std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count();
            std::cout << "Time:cpu_mask," << std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time).count() << std::endl;
        #endif
        
        #ifdef DEBUG
        std::cout << "New score found: " << total_score << " (entry_count: " << entry_count << ")" << std::endl;
        for(auto& pair : best_pairs) {
            std::cout << "Pair: (" << pair.first.global_jobid << "[" << pair.first.pgid << "], " << pair.second.global_jobid << "[" << pair.second.pgid << "]), Score: " << pair.score << std::endl;
        }
        #endif
        
        try_cpu_masks[entry_count++] = cpu_mask;

        if(entry_count == max_entries) break;
    }

    #ifdef TIME
        for(int i=0; i<max_entries+1; i++) {
            std::cout << i << ": " << prev_scores[i] << std::endl;
        }
    #endif

    #ifdef TIME
    std::cout << "Time:total_time," << total_time << std::endl;
    #endif

    #ifdef DEBUG
    std::cout << "=== Dumping try_cpu_masks ===" << std::endl;
    for (int i = 0; i < (int)try_cpu_masks.size(); ++i) {
        std::cout << "[try_cpu_masks[" << i << "]]" << std::endl;
        for (const auto& [pgid, cpu_set] : try_cpu_masks[i]) {
            std::cout << "  pgid=" << pgid << " -> CPUs: ";
            for (int cpu = 0; cpu < LOGICAL_CORE_NUM; ++cpu) {
                if (CPU_ISSET(cpu, &cpu_set.set)) {
                    std::cout << cpu << " ";
                }
            }
            std::cout << std::endl;
        }
    }
    #endif

    // Evaluate each configuration and select the best
    int max_index = -1;
    double max_score = 0.0;
    static constexpr auto sleep_time_sec = 20; // seconds

    for (int i = 0; i <= max_entries; ++i) {
        for (auto& [pgid, cpu_set] : try_cpu_masks[i]) {
            set_pgid_affinity(pgid, cpu_set.set);
        }
        std::cout << "Evaluating configuration " << i << "..." << "sleeping for " << sleep_time_sec << " seconds" << std::endl;
        reset_ipc_counters();
        std::this_thread::sleep_for(std::chrono::seconds(sleep_time_sec));

        // Calculate System Throughput (STP)
        double STP = 0.0;

        for (int word = 0; word < ACTIVE_MASK_SIZE; ++word) {
            unsigned long bits = shared->active_mask[word];
            while (bits) {
                int bit = ctz_ulong(bits);
                int idx = word * BITS_PER_LONG + bit;
                bits &= (bits - 1);            // clear lowest set bit

                if (idx >= MAX_SLOTS) continue;

                int pgid, global_jobid;
                uint64_t cycles, insts;

                read_slot_consistent(shared->slots[idx],
                                    pgid, global_jobid,
                                    cycles, insts);

                // (optional) Guard against stale/cleared slots
                if (global_jobid < 0 || pgid <= 0) continue;
                if (cycles == 0) {
                    DEBUG_PRINT("Warning: cycles is zero for pgid " << pgid
                                << ", global_jobid " << global_jobid
                                << " (slot=" << idx << ")");
                    continue;
                }

                auto it = single_IPC_map.find(global_jobid);
                if (it == single_IPC_map.end() || it->second == 0.0) continue;
                
                double ipc = static_cast<double>(insts) / static_cast<double>(cycles);
                STP += ipc / it->second;
                DEBUG_PRINT("IPC: " << ipc << ", Normalized IPC: " << ipc / it->second);
            }
        }

        DEBUG_PRINT("Configuration " << i << ": STP = " << STP);

        if (STP > max_score) {
            max_score = STP;
            max_index = i;
        }
    }
    DEBUG_PRINT("Best configuration: " << max_index << " with STP = " << max_score);

    // Apply the best configuration
    if (max_index != -1) {
        for (auto& [pgid, cpu_set] : try_cpu_masks[max_index]) {
            set_pgid_affinity(pgid, cpu_set.set);
        }
    }
    DEBUG_PRINT("Scheduling complete.");
}

// =============================================================================
// Configuration and Initialization
// =============================================================================

// Set the sibling core map from Python dictionary
void set_sibling_core_map(py::dict py_map) {
    sibling_core_map.clear();

    for (auto item : py_map) {
        int key = item.first.cast<int>();
        auto value_tuple = item.second.cast<std::pair<int, int>>();
        sibling_core_map[key] = value_tuple;
    }
}

// Open and map shared memory for IPC monitoring
int open_mmap() {
    fd_ipc = open("/dev/IPC_monitor", O_RDWR);
    if (fd_ipc < 0) {
        perror("open /dev/IPC_monitor");
        return 1;
    }

    base_size = sizeof(struct ipc_shared);
    mmap_size = ((base_size + PAGE_SIZE - 1) / PAGE_SIZE) * PAGE_SIZE;
    printf("base size: %zu bytes\n", base_size);
    printf("mmap size: %zu bytes\n", mmap_size);

    shared = (struct ipc_shared*)mmap(NULL, mmap_size, PROT_READ | PROT_WRITE, 
                                       MAP_SHARED, fd_ipc, 0);
    if (shared == MAP_FAILED) {
        perror("mmap");
        close(fd_ipc);
        return 1;
    }
    return 0;
}

// =============================================================================
// Score Map Management
// =============================================================================

// Update score map with a pair of job IDs and their compatibility score
void update_score_map(int32_t jobid1, int32_t jobid2, double score) {
    uint64_t key = make_key(jobid1, jobid2);
    score_map[key] = score;
}

// Update single IPC map with a job's standalone IPC value
void update_single_IPC_map(uint32_t jobid, double ipc) {
    single_IPC_map[jobid] = ipc;
}

// Return all entries in the score map as a Python dict
py::dict get_score_map_py() {
    py::dict d;

    for (const auto& entry : score_map) {
        uint64_t key = entry.first;
        double score = entry.second;

        uint32_t jobid1 = static_cast<uint32_t>(key >> 32);
        uint32_t jobid2 = static_cast<uint32_t>(key & 0xFFFFFFFF);

        // Python tuple as key
        d[py::make_tuple(jobid1, jobid2)] = score;
    }
    return d;
}

// =============================================================================
// Python Bindings
// =============================================================================

void bind_job_mapper(py::module& m) {
    m.def("schedule", &schedule, "Run the greedy scheduler");
    m.def("schedule_test", &schedule_test, "Run the greedy scheduler test");
    m.def("set_sibling_core_map", &set_sibling_core_map, "Generate sibling core map");
    m.def("open_mmap", &open_mmap, "Open memory map");
    m.def("update_score_map", &update_score_map, "Update score map");
    m.def("update_single_IPC_map", &update_single_IPC_map, "Update single IPC map");
    m.def("get_score_map_py", &get_score_map_py, "Get score map as Python dict");
}