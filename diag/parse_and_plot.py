import glob
import sys
import parser

dirs = glob.glob("outputs/*")

parser_table = {
    "fp_isq": parser.queue_type_parser,
    "int_isq": parser.queue_type_parser,
    "load_isq": parser.queue_type_parser,
    "load_lsq": parser.queue_type_parser,
    "rob": parser.queue_type_parser,

    "l1_dcache": parser.cache_type_parser,
    "l1_dtlb": parser.cache_type_parser,
    "l2_cache": parser.cache_type_parser,

    "l1_dcache-intel": parser.l1_dcache_intel_parser,
    "l1_dtlb-intel": parser.l1_dtlb_intel_parser,
    "l2_cache-intel": parser.l2_cache_intel_parser,

    "l1_icache-amd": parser.l1_icache_parser,
    "l1_icache": parser.l1_icache_parser,
    "uop_cache": parser.uop_cache_perf_parser,

    "l1_itlb-intel": parser.l1_itlb_parser,
    "uop_cache-intel": parser.uop_cache_x86_parser,

    "l1_itlb-amd": parser.l1_itlb_parser,
    "uop_cache-amd": parser.uop_cache_x86_parser,
}

DEBUG = False

if DEBUG:
    dir = sys.argv[1]
    feature_name = dir.split("/")[-1]
    print(feature_name, dir)
    results = parser_table[feature_name].parse(dir, feature_name)
else:
    for dir in dirs:
        feature_name = dir.split("/")[-1]
        print(feature_name, dir)
        results = parser_table[feature_name].parse(dir, feature_name)