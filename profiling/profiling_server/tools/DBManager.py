"""
Database Manager Module

This module provides a MongoDB interface for storing and retrieving
profiling measurement data. It handles:

- Storing IPC measurements with pressure levels and feature types
- Tracking profiling completion timestamps
- Querying historical profiling data

The data is organized by node name to support multi-node deployments.
Each measurement record includes the workload ID, resource feature,
pressure level, and measured IPC value.
"""

from pymongo import MongoClient
import time
from .config import *
from .global_variable_generator import *
from .machine_data import TARGET_FEATURE


def wrap_data_for_db(feature, global_jobid, pressure, run_type, IPC):
    """
    Create a standardized document for database storage.
    
    Args:
        feature: Resource feature name (e.g., 'int_isq', 'l1_dcache')
        global_jobid: Unique identifier for the workload
        pressure: Pressure level (0=LOW, 1=MEDIUM, 2=HIGH for queues; way count for caches)
        run_type: Type of measurement run ('workload' or 'injector')
        IPC: Instructions Per Cycle measurement value
        
    Returns:
        Dictionary formatted for MongoDB insertion with all required fields
    """
    output = {
        "timestamp":    int(time.time()),
        "node_name":    NODE_NAME,
        "feature":      feature,
        "feature_id":   feature_to_featureID[feature] if feature in TARGET_FEATURE else -1,
        "feature_type": feature_type_table[feature] if feature in TARGET_FEATURE else -1,
        "global_jobid": global_jobid,
        "pressure":     pressure,
        "run_type":     run_type,
        "IPC":          round(IPC, 6),
    }

    return output


class DBManager:
    """
    MongoDB connection manager for profiling data storage.
    
    Handles connections to the profile_data database with two collections:
    - measurement: Stores individual IPC measurements
    - timestamp: Tracks when profiling was completed for each job
    
    Uses upsert operations to avoid duplicate entries when re-profiling.
    """
    
    def __init__(self):
        """Initialize MongoDB connection and select collections."""
        self.client = MongoClient(DB_SERVER)
        self.db = self.client["profile_data"]
        self.collection = self.db["measurement"]
        self.timestamp_db = self.db["timestamp"]

    def make_filter_query(self, data):
        """
        Create a filter query to find existing measurement records.
        
        Uses all identifying fields except timestamp and IPC to find
        records that should be updated rather than duplicated.
        
        Args:
            data: Measurement document with all fields
            
        Returns:
            Dictionary with filter criteria for update operations
        """
        filter_query = {
            "node_name":    NODE_NAME,
            "feature":      data["feature"],
            "feature_id":   data["feature_id"],
            "feature_type": data["feature_type"],
            "global_jobid": data["global_jobid"],
            "pressure":     data["pressure"],
            "run_type":     data["run_type"],
        }
        return filter_query

    def send_data(self, data):
        """
        Store or update a measurement record in the database.
        
        Uses upsert to insert new records or update existing ones
        with the same filter criteria.
        
        Args:
            data: Measurement document from wrap_data_for_db()
        """
        filter_query = self.make_filter_query(data)
        update_doc = {"$set": data}
        self.collection.update_one(filter_query, update_doc, upsert=True)

    def send_done(self, global_jobid):
        """
        Record profiling completion timestamp for a workload.
        
        Used to track which workloads have completed profiling
        and when, enabling incremental updates.
        
        Args:
            global_jobid: Unique identifier for the completed workload
        """
        timestamp = int(time.time())
        data = {
            "global_jobid": global_jobid,
            "timestamp": timestamp
        }
        filter_query = {"global_jobid": global_jobid}
        self.timestamp_db.update_one(filter_query, {"$set": data}, upsert=True)

    def read_all(self):
        """
        Retrieve all measurement records for the current node.
        
        Returns:
            List of measurement documents sorted by timestamp (oldest first)
        """
        cursor = self.collection.find({"node_name": NODE_NAME}).sort("timestamp", 1)
        return list(cursor)
    
    def clear_db(self):
        """
        Delete all profiling data for the current node.
        
        Removes both measurement records and completion timestamps.
        Use with caution as this operation is irreversible.
        """
        self.collection.delete_many({"node_name": NODE_NAME})
        self.timestamp_db.delete_many({"node_name": NODE_NAME})

    def close(self):
        """Close the MongoDB connection."""
        self.client.close()


if __name__ == "__main__":
    # Example usage: store a sample measurement
    db_manager = DBManager()
    sample_data = wrap_data_for_db("int_port", 0, 0, "workload", 1.23)
    db_manager.send_data(sample_data)
    db_manager.send_done(0)
    db_manager.close()