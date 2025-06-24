"""
Test module for for loop functionality with Dask bags.

This module tests the for loop syntax, parsing, reduction, and execution
using Dask bags as the underlying dataset abstraction.
"""

import sys
import logging
from pathlib import Path

# Add the implementation to the Python path
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir / "implementation" / "python"))

from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


def test_for_loops():
    """Test for loop functionality"""
    
    # Test 1: Basic for loop with range
    print("Test 1: Basic for loop with range")
    content1 = '''let result = for i in range(5) do i * 2
print "doubled" result'''
    
    program1 = parse_program_content(content1)
    work_plan1 = reduce_program(program1)
    ops1 = work_plan1.operations
    
    assert len(ops1) == 2, f"Expected 2 operations, got {len(ops1)}"
    assert any(op.operator == "range" for op in ops1.values()), "Expected range operation"
    assert any(op.operator == "dask_map" for op in ops1.values()), "Expected dask_map operation"
    assert len(work_plan1.goals) == 1, f"Expected 1 goal, got {len(work_plan1.goals)}"
    
    print("  ✓ Basic for loop works")
    
    # Test 2: Nested expressions in for loop body
    print("Test 2: Nested expressions in for loop body")
    content2 = '''let result = for x in range(3) do x + 1
save "incremented" result'''
    
    program2 = parse_program_content(content2)
    work_plan2 = reduce_program(program2)
    ops2 = work_plan2.operations
    
    # Find the dask_map operation and check its body
    dask_map_op = next((op for op in ops2.values() if op.operator == "dask_map"), None)
    assert dask_map_op is not None, "Expected dask_map operation"
    assert dask_map_op.arguments["variable"] == "x", f"Expected variable 'x', got {dask_map_op.arguments['variable']}"
    
    print("  ✓ Nested expressions work")
    
    # Test 3: Multiple for loops
    print("Test 3: Multiple for loops")
    content3 = '''let first = for i in range(3) do i * 2
let second = for j in range(2) do j + 1
print "first" first
print "second" second'''
    
    program3 = parse_program_content(content3)
    work_plan3 = reduce_program(program3)
    ops3 = work_plan3.operations
    
    # Should have multiple range and dask_map operations
    range_ops = [op for op in ops3.values() if op.operator == "range"]
    map_ops = [op for op in ops3.values() if op.operator == "dask_map"]
    
    assert len(range_ops) == 2, f"Expected 2 range operations, got {len(range_ops)}"
    assert len(map_ops) == 2, f"Expected 2 dask_map operations, got {len(map_ops)}"
    assert len(work_plan3.goals) == 2, f"Expected 2 goals, got {len(work_plan3.goals)}"
    
    print("  ✓ Multiple for loops work")
    
    print("\nAll for loop tests passed!")


if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.WARNING)  # Reduce noise for tests
    
    try:
        test_for_loops()
        print("SUCCESS: All for loop tests passed")
        sys.exit(0)
    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
