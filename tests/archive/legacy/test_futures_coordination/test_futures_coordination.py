#!/usr/bin/env python3
"""
Test the new global futures table for lock-free operation coordination.
"""

import sys
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add the implementation to Python path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / "implementation" / "python"))

from voxlogica.reducer import WorkPlan, Operation, ConstantValue
from voxlogica.execution import ExecutionEngine, get_operation_future, set_operation_future, remove_operation_future
from voxlogica.storage import get_storage, set_storage, NoCacheStorageBackend

def test_futures_coordination():
    """Test that multiple threads can coordinate via the global futures table."""
    print("=== Testing Futures Coordination ===")
    
    # Use in-memory storage for clean test
    test_storage = NoCacheStorageBackend()
    set_storage(test_storage)
    
    # Create a simple workplan: 5 + 3
    workplan = WorkPlan()
    
    # Add constants
    const_5 = ConstantValue(value=5)
    const_3 = ConstantValue(value=3)
    const_5_id = workplan.add_node(const_5)
    const_3_id = workplan.add_node(const_3)
    
    # Add operation
    add_op = Operation(operator="addition", arguments={"0": const_5_id, "1": const_3_id})
    add_op_id = workplan.add_node(add_op)
    
    # Add print goal
    workplan.add_goal("print", add_op_id, "result")
    
    # Results collector
    results = []
    errors = []
    futures_seen = []
    
    def execute_workplan_worker(worker_id):
        """Worker function that executes the workplan."""
        try:
            print(f"Worker {worker_id} starting...")
            
            # Check for global future
            future = get_operation_future(add_op_id)
            if future:
                futures_seen.append(f"Worker {worker_id} saw future")
            
            engine = ExecutionEngine()
            result = engine.execute_workplan(workplan)
            
            results.append((worker_id, result.success, len(result.completed_operations)))
            print(f"Worker {worker_id} completed: success={result.success}, ops={len(result.completed_operations)}")
            
        except Exception as e:
            errors.append((worker_id, str(e)))
            print(f"Worker {worker_id} error: {e}")
    
    # Start multiple workers simultaneously
    print("Starting 3 workers simultaneously...")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(execute_workplan_worker, i) for i in range(3)]
        
        # Wait for all to complete
        for future in futures:
            future.result()
    
    # Check results
    print(f"\nResults: {results}")
    print(f"Errors: {errors}")
    print(f"Futures seen: {futures_seen}")
    
    # Verify all workers succeeded
    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    assert len(errors) == 0, f"Expected no errors, got {errors}"
    
    for worker_id, success, ops_count in results:
        assert success, f"Worker {worker_id} failed"
        assert ops_count > 0, f"Worker {worker_id} completed no operations"
    
    print("✓ Futures coordination test passed!")

def test_futures_table_operations():
    """Test the basic futures table operations."""
    print("\n=== Testing Futures Table Operations ===")
    
    op_id = "test_operation_" + str(int(time.time()))
    
    # Test that operation has no future initially
    future = get_operation_future(op_id)
    assert future is None, "Expected no future initially"
    
    # Test setting a future (create a mock Future-like object)
    from concurrent.futures import Future as ConcurrentFuture
    mock_future = ConcurrentFuture()
    success = set_operation_future(op_id, mock_future)
    assert success, "Expected to successfully set future"
    
    # Test getting the future
    retrieved = get_operation_future(op_id)
    assert retrieved == mock_future, "Expected to retrieve the same future"
    
    # Test that setting duplicate fails
    another_future = ConcurrentFuture()
    duplicate_success = set_operation_future(op_id, another_future)
    assert not duplicate_success, "Expected duplicate future setting to fail"
    
    # Test removal
    remove_operation_future(op_id)
    after_removal = get_operation_future(op_id)
    assert after_removal is None, "Expected no future after removal"
    
    print("✓ Futures table operations test passed!")

def main():
    """Run all tests."""
    print("Testing Global Futures Table for Lock-Free Operation Coordination")
    print("=" * 70)
    
    try:
        test_futures_table_operations()
        test_futures_coordination()
        
        print("\n" + "=" * 70)
        print("🎉 All futures coordination tests passed!")
        return 0
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
