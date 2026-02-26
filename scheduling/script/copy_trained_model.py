"""
Trained Model Copier

This script copies the latest trained prediction model from the profiling server
to the scheduling module's trained_model directory with a unique timestamp.

Usage:
    python copy_trained_model.py

Source:
    ../profiling/live_server/outputs/prediction_model.json

Destination:
    trained_model/prediction_model_<TIMESTAMP>.json

Behavior:
    - Compares the source model with the latest model in the destination
    - Only copies if the model content is different
    - Uses Unix timestamp in filename for versioning

Output:
    - Prints success message with destination path if copied
    - Prints info message if model is identical (no copy needed)
    - Prints error message if source file doesn't exist
"""

import time
import os
import glob


def is_identical_to_latest_model(source_path: str, destination_dir: str) -> bool:
    """Check if source model is identical to the latest model in destination.
    
    Args:
        source_path: Path to the source model file
        destination_dir: Directory containing existing model files
        
    Returns:
        True if the source model is identical to the latest existing model
    """
    with open(source_path, "r") as f:
        source_model_content = f.read()
    
    existing_models = glob.glob(f"{destination_dir}/prediction_model_*.json")
    if existing_models:
        # Find the model with the highest timestamp
        latest_model_path = max(
            existing_models,
            key=lambda x: int(x.split("_")[-1].split(".")[0]) 
        )
        with open(latest_model_path, "r") as f:
            latest_model_content = f.read()
        return source_model_content == latest_model_content
    
    return False


if __name__ == "__main__":
    timestamp = int(time.time())
    source_path = "../profiling/live_server/outputs/prediction_model.json"
    destination_dir = "trained_model"
    destination_path = f"{destination_dir}/prediction_model_{timestamp}.json"

    # Ensure destination directory exists
    os.makedirs(destination_dir, exist_ok=True)
    
    if os.path.isfile(source_path):
        if is_identical_to_latest_model(source_path, destination_dir):
            print("[INFO] The trained model is identical to the latest one. No copy made.")
            exit(0)
        else:
            os.system(f"cp {source_path} {destination_path}")
            print(f"[INFO] Copied trained model to {destination_path}")
    else:
        print(f"[ERROR] Source model file {source_path} does not exist.")