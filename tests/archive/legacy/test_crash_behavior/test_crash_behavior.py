#!/usr/bin/env python3
"""
Test to demonstrate what happens when the app crashes while operations are running.
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

description = """Tests crash behavior analysis and recovery mechanisms.
This test demonstrates what happens when the application crashes while operations 
are running, how 'dangling' operations are created in the database, and how the 
cleanup mechanisms work to recover from such situations. It also tests the 
mark_running behavior with existing operations."""

def show_execution_state():
    """Show current execution state in the database."""
    storage = get_storage()
    
    try:
        with storage._get_connection() as conn:
            cursor = conn.execute("""
                SELECT operation_id, status, worker_id, started_at, completed_at, error_message
                FROM execution_state
                ORDER BY started_at DESC
                LIMIT 10
            """)
            
            rows = cursor.fetchall()
            
            print("\n=== Current Execution State ===")
            print(f"{'Operation ID':<16} {'Status':<10} {'Worker':<15} {'Started':<20} {'Error':<30}")
            print("-" * 100)
            
            for row in rows:
                op_id = row[0][:8] + "..." if row[0] else "None"
                status = row[1] or "None"
                worker = row[2] or "None"
                started = row[3] or "None"
                error = (row[5][:30] + "...") if row[5] and len(row[5]) > 30 else (row[5] or "None")
                
                print(f"{op_id:<16} {status:<10} {worker:<15} {started:<20} {error:<30}")
                
            if not rows:
                print("No execution state records found.")
                
    except Exception as e:
        print(f"Error reading execution state: {e}")

def simulate_crash_scenario():
    """Simulate what would happen if the app crashed while operations were running."""
    storage = get_storage()
    
    # First, let's create some fake "running" operations to simulate a crash
    print("\n=== Simulating App Crash Scenario ===")
    
    fake_operations = [
        ("abc123def456", "worker_12345"),
        ("def456ghi789", "worker_12345"), 
        ("ghi789jkl012", "worker_67890")
    ]
    
    try:
        with storage._get_connection() as conn:
            # Insert fake running operations
            for op_id, worker_id in fake_operations:
                conn.execute("""
                    INSERT OR REPLACE INTO execution_state 
                    (operation_id, status, worker_id, started_at)
                    VALUES (?, 'running', ?, datetime('now', '-2 hours'))
                """, (op_id, worker_id))
            conn.commit()
            
        print(f"Created {len(fake_operations)} fake 'running' operations from 2 hours ago")
        show_execution_state()
        
        # Now show what cleanup would do
        print(f"\n=== Running Cleanup (max_age_hours=1) ===")
        cleaned_count = storage.cleanup_failed_executions(max_age_hours=1)
        print(f"Cleaned up {cleaned_count} stale operations")
        
        show_execution_state()
        
        return cleaned_count > 0
        
    except Exception as e:
        print(f"Error in simulation: {e}")
        return False

def test_mark_running_behavior():
    """Test how mark_running behaves with existing operations."""
    storage = get_storage()
    
    print("\n=== Testing mark_running Behavior ===")
    
    test_op_id = "test_operation_123"
    
    # Try to mark as running
    print(f"1. Trying to mark {test_op_id} as running...")
    result1 = storage.mark_running(test_op_id)
    print(f"   Result: {result1} (should be True)")
    
    # Try again - should return False since already running
    print(f"2. Trying to mark {test_op_id} as running again...")
    result2 = storage.mark_running(test_op_id)
    print(f"   Result: {result2} (should be False - already running)")
    
    # Mark as failed and try again
    print(f"3. Marking {test_op_id} as failed...")
    storage.mark_failed(test_op_id, "Test failure")
    
    print(f"4. Trying to mark failed {test_op_id} as running...")
    result3 = storage.mark_running(test_op_id)
    print(f"   Result: {result3} (should be True - can retry failed operations)")
    
    # Clean up
    try:
        with storage._get_connection() as conn:
            conn.execute("DELETE FROM execution_state WHERE operation_id = ?", (test_op_id,))
            conn.commit()
    except:
        pass
    
    return result1 and not result2 and result3

def main():
    """Main test function."""
    print(f"\nTest Description: {description}\n")
    
    parser = argparse.ArgumentParser(description="Test crash behavior analysis and recovery")
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Implementation language to test (default: all)",
    )
    args = parser.parse_args()
    
    print("VoxLogicA-2 Crash Behavior Analysis")
    print("=" * 50)
    
    try:
        show_execution_state()
        mark_running_test_passed = test_mark_running_behavior()
        crash_simulation_passed = simulate_crash_scenario()
        
        print("\n=== Summary ===")
        print("When the app crashes while operations are running:")
        print("1. Operations remain marked as 'running' in the database")
        print("2. These become 'dangling' operations that will never complete")
        print("3. The cleanup_failed_executions() method can clean them up")
        print("4. Cleanup is currently MANUAL - not automatic on startup")
        print("5. Failed operations can be retried (mark_running allows retry)")
        
        if mark_running_test_passed and crash_simulation_passed:
            print("\n✓ All crash behavior tests passed!")
            return 0
        else:
            print("\n✗ Some crash behavior tests failed!")
            return 1
            
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
