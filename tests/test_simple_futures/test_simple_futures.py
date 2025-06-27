#!/usr/bin/env python3
"""
Simple test to verify the new futures-based execution works.
"""

import sys
from pathlib import Path

# Add the implementation to Python path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / "implementation" / "python"))

from voxlogica.reducer import WorkPlan, Operation, ConstantValue
from voxlogica.execution import ExecutionEngine
from voxlogica.storage import set_storage, NoCacheStorageBackend

def test_simple_execution():
    """Test a simple workplan execution."""
    print("=== Testing Simple Execution ===")
    
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
    
    print(f"Workplan has {len(workplan.operations)} operations")
    print(f"Addition operation ID: {add_op_id[:8]}...")
    
    # Execute
    engine = ExecutionEngine()
    result = engine.execute_workplan(workplan)
    
    print(f"Execution result: success={result.success}")
    print(f"Completed operations: {len(result.completed_operations)}")
    print(f"Failed operations: {len(result.failed_operations)}")
    
    if result.failed_operations:
        print("Failed operations:")
        for op_id, error in result.failed_operations.items():
            print(f"  {op_id[:8]}...: {error}")
    
    return result.success

def main():
    """Run the test."""
    print("Testing Simple Futures-Based Execution")
    print("=" * 50)
    
    try:
        success = test_simple_execution()
        
        if success:
            print("\n✓ Simple execution test passed!")
            return 0
        else:
            print("\n❌ Simple execution test failed!")
            return 1
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
