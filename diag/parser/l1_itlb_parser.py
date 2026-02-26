import glob
from collections import defaultdict
import matplotlib.pyplot as plt
import os
import math

def extract_IPC(dir):
    with open(dir) as file:
        lines = file.read().strip().split("\n")

    for line in lines:
        line = line.split(": ")
        if line[0] == "itlb_miss_per_branch":
            return float(line[1])

class OutputStruct:
    is_smt: int
    window_size: int
    num_ways: int
    IPC: float
    file_dir: str

    def __init__(self, is_smt, window_size, num_ways, IPC):
        self.is_smt = is_smt
        self.window_size = window_size
        self.num_ways = num_ways
        self.IPC = IPC

    def __lt__(self, other):
        return (self.is_smt, self.window_size, self.num_ways) < (other.is_smt, other.window_size, other.num_ways)

            
def split_dir(dir):
    temp = dir.split("/")

    is_smt = int(1 if temp[2] == "w_smt" else 0)
    window_size = int(temp[-1].split(".")[1])
    num_ways = int(temp[-1].split(".")[2])

    return OutputStruct(is_smt=is_smt, window_size=window_size, num_ways=num_ways, IPC=extract_IPC(dir))

def parse(dir, feature_name):
    output_files = sorted(glob.glob(f"{dir}/**/*.out", recursive=True))
    outputs: list[OutputStruct] = []
    for output_file in output_files:
        outputs.append(split_dir(output_file))
    
    outputs.sort()

    plot_dict = {
        0: defaultdict(list),
        1: defaultdict(list)
    }

    for output in outputs:
        plot_dict[output.is_smt][output.window_size].append((output.num_ways, output.IPC))
    
    plot_dir = f"outputs/{feature_name}/plots"
    os.system(f"mkdir -p {plot_dir}")

    for is_smt, IPC_dict in plot_dict.items():
        is_smt = "wo_smt" if is_smt == 0 else "w_smt"

        plt.figure(figsize=(12, 8))
        for window_size, IPC_list in IPC_dict.items():
            x_values = [value[0] for value in IPC_list]
            y_values = [value[1] for value in IPC_list]

            plt.plot(x_values, y_values, label=int(math.log2(window_size)), marker="o")
        
        plt.legend(title="Window_size(Byte) log 2")
        plt.grid(True)
        plt.title(f"{feature_name} Diag Results - {is_smt}")
        plt.xlabel("Num Entries")
        plt.ylabel("L1 ITLB Miss Rate")
        plt.xscale("log", base=2)
        plt.savefig(f"{plot_dir}/{is_smt}.png")
        plt.clf()