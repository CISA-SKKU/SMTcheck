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

// Constants
#define EVENT_COUNT 2
#define MAP_HUGE_1GB (30 << MAP_HUGE_SHIFT)

// Event names
static const char* event_list[EVENT_COUNT] = {
    "cycles",
    "instructions",
};

// Global variables
pfm_pmu_info_t pinfo;
struct timespec start, end;
long long count_arr[EVENT_COUNT];
perf_event_attr pe_arr[EVENT_COUNT];
pfm_perf_encode_arg_t encodes[EVENT_COUNT];
int fd_arr[EVENT_COUNT];
char* name[EVENT_COUNT];

uint64_t* RandomArray0;

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

// Initialize array with random chain
static uint64_t* init_array(uint64_t* array, int num_sets, int num_ways, int stride) {
    std::vector<int> set_chain(num_sets);
    std::vector<int> way_chain(num_ways);
    int offset_bits = (int)log2(stride);
    int shift_bits = offset_bits - (int)log2(sizeof(void*));
    
    // Initialize sequential chain
    for (int i = 0; i < num_sets; i++) {set_chain[i] = i;}
    for (int i = 0; i < num_ways; i++) {way_chain[i] = i;}
    
    // Shuffle chain randomly
    std::random_device rd;
    std::shuffle(set_chain.begin(), set_chain.end(), std::mt19937(rd()));
    std::shuffle(way_chain.begin(), way_chain.end(), std::mt19937(rd()));
    
    int last_set = set_chain[num_sets - 1];
    int last_way = way_chain[num_ways - 1];

    // Create pointer chain
    for(int s=0; s<num_sets; s++) {
        for(int w=0; w<num_ways-1; w++) {
            array[((way_chain[w]*num_sets + set_chain[s])<<shift_bits)] = (uint64_t)&array[((way_chain[w+1]*num_sets + set_chain[s])<<shift_bits)];
        }
        array[((way_chain[num_ways-1]*num_sets  + set_chain[s])<<shift_bits)] = (uint64_t)&array[((way_chain[0]*num_sets  + set_chain[(s+1)%num_sets])<<shift_bits)];
    }

    return (uint64_t*)&array[((way_chain[0]*num_sets + set_chain[0])<<shift_bits)];
}

// Diagnostic function
static void diag(uint64_t* arr0){
    clock_gettime(CLOCK_MONOTONIC, &start);
    for(int i=0; i<EVENT_COUNT; i++)
    {
        ioctl(fd_arr[i], PERF_EVENT_IOC_RESET, 0);
        ioctl(fd_arr[i], PERF_EVENT_IOC_ENABLE, 0);
    }

    asm volatile(
        "movq %[RandomArray0], %%r13"
        :
        : [RandomArray0] "m" (arr0)
        : "%r13" 
        );
    MainLoop:
        asm volatile("movq (%r13), %r13");
    goto MainLoop;
}

static bool is_power_of_two(int x) {
    // Check if positive and only one bit is set
    return x > 0 && (x & (x - 1)) == 0;
}

int main(int argc, char **argv) {
    int ret;
    
    int use_hugepage = atoi(argv[1]); // 0: no hugepage, 1: hugepage
    int stride = atoi(argv[2]); // stride in bytes
    int num_sets = atoi(argv[3]);
    int num_ways = atoi(argv[4]);

    if(!is_power_of_two(num_sets) || !is_power_of_two(stride)) {
        std::cerr << "Error: num_sets and stride must be powers of two." << std::endl;
        return EXIT_FAILURE;
    }
    if(int(log2(stride)) < (int)log2(sizeof(void*))) {
        std::cerr << "Error: stride must be at least " << sizeof(void*) << " bytes." << std::endl;
        return EXIT_FAILURE;
    }

    if (use_hugepage) 
        RandomArray0 = (uint64_t*)mmap(NULL, num_sets * num_ways * stride, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB | MAP_HUGE_1GB, -1, 0);
    else
        RandomArray0 = (uint64_t*)mmap(NULL, num_sets * num_ways * stride, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

    // Register signal handlers
    signal(SIGINT, sigint_handler);
    signal(SIGSEGV, sigint_handler);

    // Initialize performance monitoring library
    ret = pfm_initialize();
    if (ret != PFM_SUCCESS) {
        fprintf(stderr, "pfm_initialize failed: %s\n", pfm_strerror(ret));
        return EXIT_FAILURE;
    }

    // Initialize random array
    uint64_t* start_ptr = init_array(RandomArray0, num_sets, num_ways, stride);
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
    
    // Run diagnostic loop
    diag(start_ptr);
    
    return 0;
}