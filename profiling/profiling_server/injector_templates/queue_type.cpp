#include <time.h>
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

// Constants
#define ACCESS_CACHELINES (1LL * (1ULL << 20))  // 64MB
#define ARRAY_SIZE (ACCESS_CACHELINES << 6)
#define EVENT_COUNT 2

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

uint64_t RandomArray0[ARRAY_SIZE];
uint64_t RandomArray1[ARRAY_SIZE];

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

static int init_array(uint64_t array[]) {
    std::vector<long long> chain(ACCESS_CACHELINES);
    
    // Initialize sequential chain
    for (long long i = 0; i < ACCESS_CACHELINES; i++) {
        chain[i] = i;
    }
    
    // Shuffle chain randomly
    std::random_device rd;
    std::shuffle(chain.begin(), chain.end(), std::mt19937(rd()));
    
    // Create pointer chain
    for (long long s = 0; s < ACCESS_CACHELINES - 1; s++) {
        array[(chain[s] << 3)] = (uint64_t)&array[(chain[s + 1] << 3)];
    }
    array[((chain[ACCESS_CACHELINES - 1]) << 3)] = array[(chain[0] << 3)];  // Make it circular

    return chain[0];
}

// Diagnostic function
static void diag(uint64_t* arr0, uint64_t* arr1){
    clock_gettime(CLOCK_MONOTONIC, &start);
    for(int i=0; i<EVENT_COUNT; i++)
    {
        ioctl(fd_arr[i], PERF_EVENT_IOC_RESET, 0);
        ioctl(fd_arr[i], PERF_EVENT_IOC_ENABLE, 0);
    }

//Insert point
}



int main(int argc, char **argv) {
    int ret;

    // Register signal handlers
    signal(SIGINT, sigint_handler);
    signal(SIGSEGV, sigint_handler);

    // Initialize performance monitoring library
    ret = pfm_initialize();
    if (ret != PFM_SUCCESS) {
        fprintf(stderr, "pfm_initialize failed: %s\n", pfm_strerror(ret));
        return EXIT_FAILURE;
    }

    // Initialize random arrays
    int start_idx0 = init_array(RandomArray0);
    int start_idx1 = init_array(RandomArray1);
    printf("Array initialization is done.\n");

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

    printf("perf ok\n");

    diag((uint64_t *)RandomArray0[start_idx0<<3],
         (uint64_t *)RandomArray1[start_idx1<<3]);   
}