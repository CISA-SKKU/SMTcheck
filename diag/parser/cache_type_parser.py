import glob
from collections import defaultdict
import matplotlib.pyplot as plt
import os

def extract_IPC(dir):
    with open(dir) as file:
        lines = file.read().strip().split("\n")

    for line in lines:
        line = line.split(": ")
        if line[0] == "IPC":
            return float(line[1])

class OutputStruct:
    is_smt: int
    stride: int
    num_ways: int
    IPC: float
    file_dir: str

    def __init__(self, is_smt, stride, num_ways, IPC):
        self.is_smt = is_smt
        self.stride = stride
        self.num_ways = num_ways
        self.IPC = IPC

    def __lt__(self, other):
        return (self.is_smt, self.stride, self.num_ways) < (other.is_smt, other.stride, other.num_ways)

            
def split_dir(dir):
    temp = dir.split("/")

    is_smt = int(1 if temp[2] == "w_smt" else 0)
    stride = int(temp[-1].split(".")[1].replace("stride", ""))
    num_ways = int(temp[-1].split(".")[2].replace("ways", ""))

    return OutputStruct(is_smt=is_smt, stride=stride, num_ways=num_ways, IPC=extract_IPC(dir))

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
        plot_dict[output.is_smt][output.stride].append((output.num_ways, output.IPC))
    
    plot_dir = f"outputs/{feature_name}/plots"
    os.system(f"mkdir -p {plot_dir}")

    for is_smt, IPC_dict in plot_dict.items():
        is_smt = "wo_smt" if is_smt == 0 else "w_smt"

        plt.figure(figsize=(12, 8))
        for stride, IPC_list in IPC_dict.items():
            x_values = [value[0] for value in IPC_list]
            y_values = [value[1] for value in IPC_list]

            plt.plot(x_values, y_values, label=stride, marker="o")
        
        plt.legend(title="Stride(Byte)")
        plt.grid(True)
        plt.title(f"{feature_name} Diag Results - {is_smt}")
        plt.xlabel("Num Ways")
        plt.ylabel("IPC")
        plt.savefig(f"{plot_dir}/{is_smt}.png")
        plt.clf()