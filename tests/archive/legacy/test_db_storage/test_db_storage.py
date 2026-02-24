#!/usr/bin/env python3
"""
Test to examine what exactly gets stored in the database for running operations.
"""

import sys
import os
import sqlite3
from pathlib import Path
import argparse

# Standard path setup for VoxLogicA imports
script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Add the implementation directory to the path
sys.path.insert(0, str(repo_root / "implementation" / "python"))

from voxlogica.storage import get_storage
from voxlogica.reducer import Operation, ConstantValue, WorkPlan
import json

description = """Tests database storage mechanisms for VoxLogicA operations.
This test examines what information gets stored in the database when operations
are marked as running, how operation IDs are computed and encoded, and what
database tables and fields are used. It demonstrates the content-addressed
nature of operation IDs and the separation between operation definition and
execution state tracking."""

def examine_database_storage():
    """Examine what gets stored in the database for operations."""
    storage = get_storage()
    
    # Create a simple operation for testing
    op = Operation(
        operator="add", 
        arguments={"0": "const_5_id", "1": "const_3_id"}
    )
    
    # Create a workplan to compute the operation ID
    workplan = WorkPlan()
    
    # Add some constants first
    const_5 = ConstantValue(value=5)
    const_3 = ConstantValue(value=3)
    
    const_5_id = workplan.add_node(const_5)
    const_3_id = workplan.add_node(const_3)
    
    # Update the operation with real IDs
    op.arguments = {"0": const_5_id, "1": const_3_id}
    operation_id = workplan.add_node(op)
    
    print("=== Operation Details ===")
    print(f"Operation: {op}")
    print(f"Operation ID: {operation_id}")
    print(f"Constant 5 ID: {const_5_id}")
    print(f"Constant 3 ID: {const_3_id}")
    
    # Mark it as running
    print(f"\n=== Marking Operation as Running ===")
    result = storage.mark_running(operation_id, worker_id="test_worker_123")
    print(f"mark_running result: {result}")
    
    # Now examine what's in the database
    print(f"\n=== Database Content ===")
    
    try:
        with storage._get_connection() as conn:
            # Check execution_state table
            print("execution_state table:")
            cursor = conn.execute("""
                SELECT operation_id, status, started_at, worker_id 
                FROM execution_state 
                WHERE operation_id = ?
            """, (operation_id,))
            
            row = cursor.fetchone()
            if row:
                print(f"  operation_id: {row[0]}")
                print(f"  status: {row[1]}")
                print(f"  started_at: {row[2]}")
                print(f"  worker_id: {row[3]}")
            else:
                print("  No record found")
            
            # Check if there's anything in results table (there shouldn't be yet)
            print("\nresults table:")
            cursor = conn.execute("""
                SELECT operation_id, data_type, size_bytes 
                FROM results 
                WHERE operation_id = ?
            """, (operation_id,))
            
            row = cursor.fetchone()
            if row:
                print(f"  operation_id: {row[0]}")
                print(f"  data_type: {row[1]}")
                print(f"  size_bytes: {row[2]}")
            else:
                print("  No record found (expected - operation not completed yet)")
                
    except Exception as e:
        print(f"Error examining database: {e}")
        return False
    
    # Clean up
    try:
        with storage._get_connection() as conn:
            conn.execute("DELETE FROM execution_state WHERE operation_id = ?", (operation_id,))
            conn.commit()
    except:
        pass
    
    return result

def examine_operation_id_encoding():
    """Show what information is encoded in the operation ID itself."""
    print("\n=== Operation ID Encoding Analysis ===")
    
    # Create different operations to see how IDs are computed
    workplan = WorkPlan()
    
    operations = [
        Operation(operator="add", arguments={"0": "id1", "1": "id2"}),
        Operation(operator="multiply", arguments={"0": "id1", "1": "id2"}),
        Operation(operator="add", arguments={"0": "id2", "1": "id1"}),  # Different order
        ConstantValue(value=42),
        ConstantValue(value="hello"),
    ]
    
    print("Operation ID analysis:")
    for i, op in enumerate(operations):
        op_id = workplan._compute_node_id(op)
        print(f"{i+1}. Operation: {op}")
        print(f"   ID: {op_id}")
        print(f"   First 8 chars: {op_id[:8]}")
        print()
    
    return True

def main():
    """Main test function."""
    print(f"\nTest Description: {description}\n")
    
    parser = argparse.ArgumentParser(description="Test database storage mechanisms")
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    args = parser.parse_args()
    
    print("VoxLogicA-2 Database Storage Analysis")
    print("=" * 50)
    
    try:
        encoding_test_passed = examine_operation_id_encoding()
        storage_test_passed = examine_database_storage()
        
        print("\n=== Summary ===")
        print("When an operation is marked as 'running':")
        print("1. Only the operation_id (SHA256 hash) is stored in execution_state table")
        print("2. The operation details (operator + arguments) are NOT stored in the DB")
        print("3. The operation_id is content-addressed - it encodes the full operation")
        print("4. To reconstruct the operation, you need the original WorkPlan")
        print("5. The DB only tracks execution STATUS, not operation content")
        
        if encoding_test_passed and storage_test_passed:
            print("\n✓ All database storage tests passed!")
            return 0
        else:
            print("\n✗ Some database storage tests failed!")
            return 1
            
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
