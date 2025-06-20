#!/usr/bin/env python3
"""
Test the auto-cleanup feature on ExecutionEngine startup.
"""

import sys
import os
from pathlib import Path
import argparse

# Standard path setup for VoxLogicA imports
script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Add the implementation directory to the path
sys.path.insert(0, str(repo_root / "implementation" / "python"))

from voxlogica.execution import ExecutionEngine
from voxlogica.storage import get_storage

description = """Tests the ExecutionEngine auto-cleanup functionality for stale operations.
This test verifies that the ExecutionEngine properly cleans up stale 'running' operations
on startup when auto_cleanup_stale_operations=True, and that it doesn't clean up when
the feature is disabled. The test creates fake stale operations and verifies cleanup behavior."""

def create_fake_stale_operations():
    """Create some fake stale operations to test cleanup."""
    storage = get_storage()
    
    fake_operations = [
        ("stale_op_001", "worker_old_1"),
        ("stale_op_002", "worker_old_1"), 
        ("stale_op_003", "worker_old_2")
    ]
    
    try:
        with storage._get_connection() as conn:
            # Insert fake running operations from 2 hours ago
            for op_id, worker_id in fake_operations:
                conn.execute("""
                    INSERT OR REPLACE INTO execution_state 
                    (operation_id, status, worker_id, started_at)
                    VALUES (?, 'running', ?, datetime('now', '-2 hours'))
                """, (op_id, worker_id))
            conn.commit()
            
        print(f"Created {len(fake_operations)} fake stale 'running' operations")
        return len(fake_operations)
        
    except Exception as e:
        print(f"Error creating fake operations: {e}")
        return 0

def count_running_operations():
    """Count operations currently marked as running."""
    storage = get_storage()
    
    try:
        with storage._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM execution_state WHERE status = 'running'")
            count = cursor.fetchone()[0]
            return count
    except Exception as e:
        print(f"Error counting operations: {e}")
        return -1

def run_auto_cleanup_test():
    """Run the main auto-cleanup test."""
    print("Testing Auto-Cleanup on ExecutionEngine Startup")
    print("=" * 50)
    
    # Show initial state
    initial_running = count_running_operations()
    print(f"Initial running operations: {initial_running}")
    
    # Create some fake stale operations
    created_count = create_fake_stale_operations()
    after_create_running = count_running_operations()
    print(f"Running operations after creating fake stale ones: {after_create_running}")
    
    # Now create an ExecutionEngine - this should trigger auto-cleanup
    print("\nCreating ExecutionEngine (should trigger auto-cleanup)...")
    engine = ExecutionEngine(auto_cleanup_stale_operations=True)
    
    # Check final state
    final_running = count_running_operations()
    print(f"Final running operations after ExecutionEngine init: {final_running}")
    
    cleaned_up_count = after_create_running - final_running
    print(f"Operations cleaned up automatically: {cleaned_up_count}")
    
    return cleaned_up_count > 0

def run_no_cleanup_test():
    """Run the test with auto-cleanup disabled."""
    print("\n=== Test Auto-Cleanup Disabled ===")
    
    # Create more fake operations
    create_fake_stale_operations()
    before_no_cleanup = count_running_operations()
    print(f"Running operations before creating engine with no cleanup: {before_no_cleanup}")
    
    # Create engine with auto-cleanup disabled
    engine2 = ExecutionEngine(auto_cleanup_stale_operations=False)
    after_no_cleanup = count_running_operations()
    print(f"Running operations after creating engine with no cleanup: {after_no_cleanup}")
    
    operations_cleaned = before_no_cleanup - after_no_cleanup
    print(f"Operations cleaned up (should be 0): {operations_cleaned}")
    
    return operations_cleaned == 0

def main():
    """Main test function."""
    print(f"\nTest Description: {description}\n")
    
    parser = argparse.ArgumentParser(description="Test ExecutionEngine auto-cleanup functionality")
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    args = parser.parse_args()
    
    try:
        # Run the auto-cleanup test
        cleanup_worked = run_auto_cleanup_test()
        
        # Run the no-cleanup test
        no_cleanup_worked = run_no_cleanup_test()
        
        if cleanup_worked and no_cleanup_worked:
            print("\n✓ All auto-cleanup tests passed!")
            return 0
        else:
            print("\n✗ Some auto-cleanup tests failed!")
            return 1
            
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
