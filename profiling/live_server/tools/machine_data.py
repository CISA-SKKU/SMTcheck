import os

SEQUENTIAL_TYPE = ["int_isq", "fp_isq", "load_isq", "uop_cache"]
PARALLEL_TYPE = ["l1_dcache", "l2_cache", "l1_dtlb", "l3_cache"]
PORT_TYPE  = ["int_port", "fp_port"]

TARGET_FEATURE = ['int_port', 'int_isq', 'fp_port', 'load_isq', 'l1_dcache', 'l2_cache', "l1_dtlb"]

SAMPLING_INTERVAL = 2
UOP_CACHE_WINDOW_SIZE = 64
UOP_CACHE_NUM_SETS = 64
MEDIUM_RATIO = 0.8
NODE_NAME = "intel-gen11"

WATERMARK = {
    "int_isq":     6,   # num_entries
    "fp_isq":      6,   # num_entries
    "load_isq":    8,   # num_entries
    "load_lsq":    64,  # num_entries
    "rob":         176, # num_entries
    "l1_dcache":   0,   # num_entries
    "l2_cache":    0,   # num_entries
    "l3_cache":    0,   # num_entries
    "l1_dtlb":     0,   # num_entries
    "uop_cache":   4,   # num_ways
}

SIZE = {
    "int_isq":     75,
    "fp_isq":      75,
    "load_isq":    46,
    "load_lsq":    128,
    "rob":         352,
    "l1_dcache":   64*12,     # num_entries
    "l2_cache":    1024*8,    # num_entries
    "l3_cache":    16384*16,  # num_entries
    "l1_dtlb":     16*4,      # num_entries
    "uop_cache":   8,         # num_ways
}

def gen_sibling_core_dict():
    sibling_core_dict = dict()
    lines = list(map(lambda x: x.strip(), os.popen("lscpu --parse=CPU,Core").read().strip().split("\n")))
    for idx in range(len(lines)):
        line = lines[idx]
        if(line[0] != "#"):
            lines = lines[idx:]
            break
    for line in lines:
        logical_core, physical_core = map(int, line.split(","))
        if physical_core not in sibling_core_dict:
            sibling_core_dict[physical_core] = []
        sibling_core_dict[physical_core].append(logical_core)

    return sibling_core_dict
sibling_core_dict = gen_sibling_core_dict()