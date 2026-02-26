import glob
import os
import matplotlib.pyplot as plt

def extract_IPC(dir):
    with open(dir) as file:
        lines = file.read().strip().split("\n")

    for line in lines:
        line = line.split(": ")
        if line[0] == "IPC":
            return float(line[1])

def parse(dir, feature_name):
    output_files = sorted(glob.glob(f"{dir}/**/*.out", recursive=True), key = lambda x: int(x.split("/")[-1].split(".")[1]))
    plot_dict = {
        "w_smt": [],
        "wo_smt": [],
    }
    for output_file in output_files:
        is_smt = output_file.split("/")[2]
        num_entries = int(output_file.split("/")[-1].split(".")[1])
        plot_dict[is_smt].append((num_entries, extract_IPC(output_file)))
    
    plot_dir = f"outputs/{feature_name}/plots"
    os.system(f"mkdir -p {plot_dir}")

    for is_smt, IPC_list in plot_dict.items():
        plt.figure(figsize=(12, 8))

        x_values = [value[0] for value in IPC_list]
        y_values = [value[1] for value in IPC_list]

        plt.plot(x_values, y_values)
        
        plt.title(f"{feature_name} Diag Results - {is_smt}")
        plt.xlabel("Num Entries")
        plt.ylabel("IPC")
        plt.savefig(f"{plot_dir}/{is_smt}.png")
        plt.clf()