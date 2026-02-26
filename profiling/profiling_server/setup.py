from injector_generator import injector_generator
from tools import measure_injector_single
from tools import machine_data
from tools import DBManager
from tools import measure_combination
import argparse
import glob
import os

injector_exec_dir = "tools/injector_exec_dir.txt"

def gen_injector_list():
    injectors = [injector for injector in sorted(glob.glob("injector/**/*.injector", recursive=True)) if "low" not in injector and "high" not in injector]
    
    return injectors

def push_single_output_metadata(single_output_metadata):
    db_manager = DBManager.DBManager()
    for feature, global_jobid, pressure, IPC, _ in single_output_metadata: 
        data = DBManager.wrap_data_for_db(feature, global_jobid, pressure, "injector", IPC)
        db_manager.send_data(data)
    db_manager.close()

if __name__ == "__main__":
    core_ids = machine_data.sibling_core_dict[0]
    if len(core_ids) == 1:
        print("[WARNING] Only one logical core found on the first physical core.")
        exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--node_name", type=str, required=True,
                        help="Name of the node for database entry")
    parser.add_argument("--isa", type=str, default="x86",
                        help="Instruction set architecture (default: x86)")
    args = parser.parse_args()

    machine_data.NODE_NAME = args.node_name
    fail_features = injector_generator.run_generator(args.isa)

    if fail_features:
        print(f"[ERROR] Failed to generate injectors for: {', '.join(fail_features)}")
    else:
        injectors = gen_injector_list()
        print("[INFO] Injector list generated successfully.")
        print(f"[INFO] Injector execution directory: {injector_exec_dir}")
    
    output_root_dir = "profile_results"
    os.makedirs(output_root_dir, exist_ok=True)

    single_output_metadata = measure_injector_single.run_injectors(injectors, output_root_dir, core_ids)
    push_single_output_metadata(single_output_metadata)


    with open(injector_exec_dir, "w") as file:
        for feature, global_jobid, pressure, IPC, injector_dir in single_output_metadata:
            if global_jobid != -1 or feature == "l3_cache":
                continue
            print(f"[INFO] Feature: {feature}, Run Type: {global_jobid}, Pressure: {pressure}, IPC: {IPC}, Injector Dir: {injector_dir}")
            file.write(f"{feature},{pressure},{injector_dir}\n")
    
    measure_combination.measure()
    measure_combination.push_results()