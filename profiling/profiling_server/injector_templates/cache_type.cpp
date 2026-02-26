#include <iostream>
#include <cstdlib>
#include <math.h>
#include <time.h>
#include <sys/mman.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <cstring>
#include <vector>
#include <random>
#include <algorithm>
#include <csignal>
#include <sys/ioctl.h>
#include <linux/perf_event.h>
#include <perfmon/pfmlib.h>
#include <perfmon/pfmlib_perf_event.h>

#ifndef NUM_ENTRIES
#define NUM_ENTRIES     0
#endif
#ifndef NUM_REGISTERS
#define NUM_REGISTERS  1
#endif
#ifndef USE_HUGEPAGE
#define USE_HUGEPAGE    0
#endif
#ifndef SHIFT_BITS
#define SHIFT_BITS     6
#endif

#define EVENT_COUNT     2
#define ARRAY_SIZE      ((NUM_ENTRIES << SHIFT_BITS))
#define MAP_HUGE_2MB    (21 << MAP_HUGE_SHIFT)
#define MAP_HUGE_1GB    (30 << MAP_HUGE_SHIFT)

// Event names
static const char* event_list[EVENT_COUNT] = {
    "cycles",
    "instructions"
};

// Global variables
pfm_pmu_info_t pinfo;
struct timespec start, end;
long long count_arr[EVENT_COUNT];
perf_event_attr pe_arr[EVENT_COUNT];
pfm_perf_encode_arg_t encodes[EVENT_COUNT];
int fd_arr[EVENT_COUNT];
char* name[EVENT_COUNT];

uint64_t*   ptr_arr[NUM_REGISTERS];
uint64_t    set_index[NUM_ENTRIES];

void cache_init ();
void run_diag();
void setup_perf();
void sigint_handler(int signal);

int main(int argc, char *argv[]){
    printf("%d, %d, %d\n", NUM_ENTRIES, NUM_REGISTERS, SHIFT_BITS);
    cache_init();
    setup_perf();
    run_diag();
	return 0;
}

void cache_init() {
    for(int i=0; i<NUM_REGISTERS; i++) {
        printf("%d: ", i);
        if (USE_HUGEPAGE)
            ptr_arr[i] = (uint64_t*)mmap(NULL, ARRAY_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB | MAP_HUGE_2MB, -1, 0);
        else
            ptr_arr[i] = (uint64_t*)mmap(NULL, ARRAY_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if(ptr_arr[i] == (uint64_t*)-1) {
            printf("fail\n");
            exit(-1);
        }
        else {
            printf("%lx\n", (uint64_t)ptr_arr[i]);
        }
    }
}

// Signal handler for SIGINT and SIGSEGV
void sigint_handler(int signal) {
    // Disable and read performance counters
    clock_gettime(CLOCK_MONOTONIC, &end);
    for (int i = 0; i < EVENT_COUNT; i++) {
        ioctl(fd_arr[i], PERF_EVENT_IOC_DISABLE, 0);
        ssize_t res = read(fd_arr[i], &count_arr[i], sizeof(count_arr[i]));
    }

    printf("\n[%d] Measuring instruction count for this printf\n", signal);
    
    long long cycles = count_arr[0];
    long long insts = count_arr[1];
    
    for (int i = 0; i < EVENT_COUNT; i++) {
        printf("%s: %lld\n", event_list[i], count_arr[i]);
    }
    
    double elapsed_time = (end.tv_sec - start.tv_sec) + (end.tv_nsec - start.tv_nsec) / 1e9;
    printf("Elapsed_time: %.6f seconds\n", elapsed_time);
    printf("IPC: %.4f\n", (double)insts / (double)cycles);
    printf("Average_Frequency: %.4lf GHz\n", (double)(cycles)/elapsed_time/pow(10, 9));
    
    // Close file descriptors
    for (int i = 0; i < EVENT_COUNT; i++) {
        close(fd_arr[i]);
    }

    exit(0);
}

void setup_perf() {
    int ret;

    // Register signal handlers
    signal(SIGINT, sigint_handler);
    signal(SIGSEGV, sigint_handler);

    // Initialize performance monitoring library
    ret = pfm_initialize();
    if (ret != PFM_SUCCESS) {
        fprintf(stderr, "pfm_initialize failed: %s\n", pfm_strerror(ret));
        exit(EXIT_FAILURE);
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
            exit(EXIT_FAILURE);
        }
        
        fd_arr[i] = perf_event_open(encodes[i].attr, 0, -1, -1, 0);
        if (fd_arr[i] == -1) {
            fprintf(stderr, "Error opening leader[%s] %llx\n", 
                    event_list[i], pe_arr[i].config);
            exit(EXIT_FAILURE);
        }
    }
}

void run_diag() {
    clock_gettime(CLOCK_MONOTONIC, &start);
    for(int i=0; i<EVENT_COUNT; i++)
    {
        ioctl(fd_arr[i], PERF_EVENT_IOC_RESET, 0);
        ioctl(fd_arr[i], PERF_EVENT_IOC_ENABLE, 0);
    }
// Insert point
}