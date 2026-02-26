"""
Prediction Model Generator

This script trains a linear regression model to predict SMT interference
between co-running workloads based on their resource usage characteristics.

The model predicts slowdown when two workloads share CPU resources like
caches, issue queues, and execution ports.
"""

import os
import json
import itertools
import sklearn.linear_model as LinearRegression

from tools import calculate_workload_characteristics as characteristics
from tools.global_variable_generator import *

# =============================================================================
# Activation Functions
# =============================================================================
def compute_activation(usage_base, usage_col, feature):
    """
    Compute activation value for resource contention.
    
    The activation function reflects different contention behaviors for each
    resource type:
    
    For sequential-type resources (e.g., ISQs):
        Act(U_A, U_B) = max(0, U_A + U_B - 1)
        
        Contention occurs only when combined usage exceeds total capacity,
        so we apply a ReLU-style function.
        
    For other resources (parallel-type):
        Act(U_A, U_B) = (U_A × U_B) × (U_A + U_B) / 2
        
        Contention increases gradually and superlinearly with usage.
        This models the product of contention probability (U_A × U_B) 
        and average usage ((U_A + U_B) / 2).
    
    Args:
        usage_base: Resource usage of base workload U_A (0-1)
        usage_col: Resource usage of co-located workload U_B (0-1)
        feature: Resource feature name
        
    Returns:
        Activation value representing contention intensity
        
    Examples:
        >>> # Sequential-type: no contention when total usage < 1
        >>> compute_activation(0.3, 0.4, "int_isq")
        0  # 0.3 + 0.4 - 1 = -0.3 → max(0, -0.3) = 0
        
        >>> # Sequential-type: contention when total usage > 1
        >>> compute_activation(0.7, 0.5, "int_isq")
        0.2  # 0.7 + 0.5 - 1 = 0.2
        
        >>> # Parallel-type: gradual contention based on usage product
        >>> compute_activation(0.5, 0.5, "l1_dcache")
        0.125  # (0.5 × 0.5) × (0.5 + 0.5) / 2 = 0.25 × 0.5 = 0.125
        
        >>> # Parallel-type: higher contention with higher usage
        >>> compute_activation(0.8, 0.8, "l2_cache")
        0.512  # (0.8 × 0.8) × (0.8 + 0.8) / 2 = 0.64 × 0.8 = 0.512
    """
    if feature in SEQUENTIAL_TYPE:
        # Sequential-type contention: only matters when total usage > 1 (overflow)
        return max(0, (usage_base + usage_col) - 1)
    else:
        # Parallel-type contention: multiplicative model
        return usage_base * usage_col * (usage_base + usage_col) / 2


# =============================================================================
# Model Training
# =============================================================================
def train_prediction_model(workload_chars, combination_ipc):
    """
    Train a linear regression model to predict workload slowdown.
    
    The model uses workload characteristics (sensitivity, intensity, usage)
    to predict the slowdown when two workloads run together.
    
    Args:
        workload_chars: Dict of workload characteristics by job_id
        combination_ipc: Dict of measured IPC for workload pairs
        
    Returns:
        Trained sklearn LinearRegression model
    """
    job_ids = list(workload_chars.keys())
    
    # Generate all workload pairs (including self-pairs)
    training_pairs = (
        list(itertools.permutations(job_ids, 2)) + 
        list(zip(job_ids, job_ids))
    )
    
    feature_vectors = []
    target_slowdowns = []
    valid_pairs = []
    
    for base_id, col_id in training_pairs:
        # Skip if combination data is missing
        if col_id not in combination_ipc[base_id]:
            print(f"[WARNING] Missing combination data for base={base_id}, col={col_id}")
            continue
        
        # Calculate actual slowdown (ground truth)
        solo_ipc = combination_ipc[base_id]["single"]
        corun_ipc = combination_ipc[base_id][col_id]
        actual_slowdown = 1 - (corun_ipc / solo_ipc)
        
        # Build feature vector: [base_slowdown, feature1_term, feature2_term, ...]
        # Each feature term = sensitivity * intensity * activation(usage)
        num_features = len(TARGET_FEATURE)
        feature_vector = [0.0] * (num_features + 1)
        
        # First feature: minimum base slowdown across all features
        base_slowdowns = [
            workload_chars[base_id][i].base_slowdown 
            for i in range(num_features)
        ]
        feature_vector[0] = min(base_slowdowns)
        
        # Remaining features: contention terms for each resource
        for feature in TARGET_FEATURE:
            idx = FEATURE_TO_INDEX[feature]
            base_char = workload_chars[base_id][idx]
            col_char = workload_chars[col_id][idx]
            
            activation = compute_activation(base_char.usage, col_char.usage, feature)
            contention_term = base_char.sensitivity * col_char.intensity * activation
            
            feature_vector[1 + idx] = contention_term
        
        feature_vectors.append(feature_vector)
        target_slowdowns.append(actual_slowdown)
        valid_pairs.append((base_id, col_id))
    
    # Train linear regression with non-negative coefficients
    model = LinearRegression.LinearRegression(positive=True)
    model.fit(feature_vectors, target_slowdowns)
    
    # Evaluate training results
    predictions = model.predict(feature_vectors)
    
    print("\n" + "=" * 70)
    print("Training Results")
    print("=" * 70)
    
    for i, (base_id, col_id) in enumerate(valid_pairs):
        predicted = min(max(predictions[i], 0.0), 1.0)  # Clamp to [0, 1]
        actual = min(max(target_slowdowns[i], 0.0), 1.0)
        
        relative_error = abs(predicted - actual) / actual if actual != 0 else 0.0
        
        print(f"[{base_id:2d}, {col_id:2d}] Actual: {actual:.4f}, "
              f"Predicted: {predicted:.4f}, Error: {relative_error * 100:.1f}%")
    
    print(f"\nModel Coefficients: {model.coef_}")
    print(f"Model Intercept: {model.intercept_}")
    print(f"Training samples: {len(feature_vectors)}")
    
    return model


def save_model(model, output_path):
    """
    Save trained model to JSON file.
    
    Args:
        model: Trained sklearn model
        output_path: Path to save JSON file
    """
    model_data = {
        "feature_list": ["base"] + TARGET_FEATURE,
        "coefficients": model.coef_.tolist(),
        "intercept": model.intercept_.tolist() if hasattr(model.intercept_, 'tolist') 
                     else float(model.intercept_)
    }
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(model_data, f, indent=4)
    
    print(f"\nModel saved to: {output_path}")


def print_workload_summary(workload_chars, job_ids):
    """
    Print summary of workload characteristics.
    
    Args:
        workload_chars: Dict of workload characteristics
        job_ids: List of job IDs to summarize
    """
    print("\n" + "=" * 70)
    print("Workload Characteristics Summary")
    print("=" * 70)
    
    for job_id in job_ids:
        print(f"\n[Job {job_id:2d}]")
        
        for feature in TARGET_FEATURE:
            idx = FEATURE_TO_INDEX[feature]
            char = workload_chars[job_id][idx]
            
            print(f"  {feature:<12} sens={char.sensitivity:.4f}, "
                  f"int={char.intensity:.4f}, usage={char.usage:.4f}, "
                  f"base={char.base_slowdown:.4f}")


# =============================================================================
# Main Entry Point
# =============================================================================
def main():
    """Main function to train and save the prediction model."""
    
    print("Initializing workload characteristics calculator...")
    characteristics.initialize()
    
    if not TRAINING_JOB_IDS:
        print("[ERROR] No training job IDs specified. Please fill TRAINING_JOB_IDS in global_variable_generator.py")
        return
    print(f"Loading {len(TRAINING_JOB_IDS)} training workloads...")
    for job_id in TRAINING_JOB_IDS:
        characteristics.add_workload(job_id)
    
    print("Calculating workload characteristics...")
    workload_chars, combination_ipc = characteristics.calculate_all_characteristics()
    
    print("Training prediction model...")
    model = train_prediction_model(workload_chars, combination_ipc)
    
    # Save model
    save_model(model, "outputs/prediction_model.json")
    
    # Print summary
    print_workload_summary(workload_chars, TRAINING_JOB_IDS)


if __name__ == "__main__":
    main()