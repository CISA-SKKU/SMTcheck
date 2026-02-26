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
        if line[0] == "IPC":
            return float(line[1])

def extract_miss_rate(dir):
    with open(dir) as file:
        lines = file.read().strip().split("\n")

    for line in lines:
        line = line.split(": ")
        if line[0] == "L1_MISS_RATE":
            return float(line[1])

class OutputStruct:
    is_smt: int
    stride: int
    num_ways: int
    IPC: float
    L1_MISS_RATE: float
    file_dir: str

    def __init__(self, is_smt, stride, num_ways, IPC, L1_MISS_RATE):
        self.is_smt = is_smt
        self.stride = stride
        self.num_ways = num_ways
        self.IPC = IPC
        self.L1_MISS_RATE = L1_MISS_RATE

    def __lt__(self, other):
        return (self.is_smt, self.stride, self.num_ways) < (other.is_smt, other.stride, other.num_ways)

            
def split_dir(dir):
    temp = dir.split("/")

    is_smt = int(1 if temp[2] == "w_smt" else 0)
    stride = int(temp[-1].split(".")[1].replace("stride", ""))
    num_ways = int(temp[-1].split(".")[2].replace("ways", ""))

    return OutputStruct(is_smt=is_smt, stride=stride, num_ways=num_ways, IPC=extract_IPC(dir), L1_MISS_RATE=extract_miss_rate(dir))

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
        plot_dict[output.is_smt][output.stride].append((output.num_ways, output.IPC, output.L1_MISS_RATE))
    
    plot_dir = f"outputs/{feature_name}/plots"
    os.system(f"mkdir -p {plot_dir}")

    for is_smt, IPC_dict in plot_dict.items():
        is_smt = "wo_smt" if is_smt == 0 else "w_smt"

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)

        for stride, IPC_list in IPC_dict.items():
            x_values = [value[0] for value in IPC_list]
            y1_values = [value[1] for value in IPC_list]
            y2_values = [value[2] for value in IPC_list]

            ax1.plot(x_values, y1_values, label=int(math.log2(stride)), marker="o")
            ax2.plot(x_values, y2_values, label=int(math.log2(stride)), marker="s")

        ax1.set_title(f"{feature_name} Diag Results - {is_smt}")
        ax1.set_ylabel("IPC (Metric 1)")
        ax1.grid(True)
        ax1.legend(title="Stride(Byte) Log2")

        ax2.set_xlabel("Num Ways")
        ax2.set_ylabel("Miss rate (Metric 2)")
        ax2.grid(True)

        plt.tight_layout()
        plt.savefig(f"{plot_dir}/{is_smt}.png")
        plt.close(fig)