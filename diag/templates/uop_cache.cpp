#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <cstring>
#include <csignal>
#include <sys/ioctl.h>
#include <linux/perf_event.h>
#include <perfmon/pfmlib.h>
#include <perfmon/pfmlib_perf_event.h>

// Constants
#define EVENT_COUNT 4

static const char* event_list[EVENT_COUNT] = {
    "cycles",
    "instructions",
    "PERF_COUNT_HW_BRANCH_INSTRUCTIONS",
    "PERF_COUNT_HW_CACHE_L1I:READ:ACCESS"
};

constexpr size_t sz_cacheline = 64;
typedef int64_t* ADDR;

// Global variables
pfm_pmu_info_t pinfo;
long long count_arr[EVENT_COUNT];
perf_event_attr pe_arr[EVENT_COUNT];
pfm_perf_encode_arg_t encodes[EVENT_COUNT];
int fd_arr[EVENT_COUNT];
char* name[EVENT_COUNT];

extern "C" void diag_start();

// Signal handler for termination signals
void sigint_handler(int signal) {
    // Disable and read performance counters
    for (int i = 0; i < EVENT_COUNT; i++) {
        ioctl(fd_arr[i], PERF_EVENT_IOC_DISABLE, 0);
        ssize_t res = read(fd_arr[i], &count_arr[i], sizeof(count_arr[i]));
    }
    
    printf("\n[%d] Measuring instruction count for this printf\n", signal);
    
    long long cycles = count_arr[0];
    long long insts = count_arr[1];
    long long branch = count_arr[2];
    long long ic_access = count_arr[3];

    // Print performance counter results
    for (int i = 0; i < EVENT_COUNT; i++) {
        printf("%s: %lld\n", event_list[i], count_arr[i]);
    }
    
    printf("-----\nIPC: %.6lf\n-----\n", (double)(insts) / cycles);
    printf("-----\nic_access_per_branch: %.6lf\n-----\n", (double)(ic_access) / branch);

    // Close file descriptors
    for (int i = 0; i < EVENT_COUNT; i++) {
        close(fd_arr[i]);
    }

    exit(0);
}

// Run diagnostic function
void run_diag() {
    for (int i = 0; i < EVENT_COUNT; i++) {
        ioctl(fd_arr[i], PERF_EVENT_IOC_RESET, 0);
        ioctl(fd_arr[i], PERF_EVENT_IOC_ENABLE, 0);
    }

    diag_start();
}

int main(int argc, char *argv[]) {
    // Register signal handlers
    signal(SIGINT, sigint_handler);
    signal(SIGTERM, sigint_handler);
    signal(SIGSEGV, sigint_handler);

    // Initialize performance monitoring library
    int ret = pfm_initialize();
    if (ret != PFM_SUCCESS) {
        fprintf(stderr, "pfm_initialize failed: %s\n", pfm_strerror(ret));
        return EXIT_FAILURE;
    }
    
    // Set up performance event attributes
    for (int i = 0; i < EVENT_COUNT; i++) {
        memset(&pe_arr[i], 0, sizeof(pe_arr[i]));
        pe_arr[i].size = sizeof(pe_arr[i]);
        pe_arr[i].disabled = 1;
        pe_arr[i].exclude_kernel = 1;
        pe_arr[i].exclude_hv = 1;

        encodes[i].attr = &pe_arr[i];
        encodes[i].fstr = &name[i];
        encodes[i].size = sizeof(encodes[i]);
    }

    memset(&pinfo, 0, sizeof(pinfo));

    // Get encoding for events and open performance counters
    for (int i = 0; i < EVENT_COUNT; i++) {   
        ret = pfm_get_os_event_encoding(event_list[i], PFM_PLM3 | PFM_PLM0, 
                                        PFM_OS_PERF_EVENT_EXT, &encodes[i]);
        if (ret != PFM_SUCCESS) {
            fprintf(stderr, "Failed to get encoding for event %d(%d): %s\n",
                    i, ret, pfm_strerror(ret));
            exit(1);
        }
        
        fd_arr[i] = perf_event_open(encodes[i].attr, 0, -1, -1, 0);
        if (fd_arr[i] == -1) {
            fprintf(stderr, "Error opening leader[%s] %llx\n", 
                    event_list[i], pe_arr[i].config);
            exit(EXIT_FAILURE);
        }
    }

    // Run diagnostic and print results
    run_diag();
    sigint_handler(0);
    
    return 0;
}