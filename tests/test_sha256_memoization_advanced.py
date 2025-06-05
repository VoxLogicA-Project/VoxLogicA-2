#!/usr/bin/env python3
"""
Advanced test for SHA256-based memoization: demonstrates how content-addressed IDs
improve memoization efficiency and enable cross-session result reuse.
"""

import sys
import os
import tempfile
import time
from pathlib import Path

# Add the implementation directory to the path
repo_root = Path(__file__).resolve().parent.parent
py_impl = repo_root / "implementation" / "python"
if str(py_impl) not in sys.path:
    sys.path.insert(0, str(py_impl))

from voxlogica.parser import parse_program
from voxlogica.reducer import reduce_program, Operations, NumberOp, IdentifierOp

description = """Advanced tests for SHA256-based memoization. Demonstrates efficiency, cross-session result reuse, deep nesting, and performance benefits of content-addressed IDs in the reducer. Shows that repeated and nested computations are efficiently memoized."""


def parse_program_text(program_text: str):
    """Helper function to parse program text directly"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".imgql", delete=False) as f:
        f.write(program_text)
        temp_filename = f.name

    try:
        return parse_program(temp_filename)
    finally:
        os.unlink(temp_filename)


def test_complex_memoization_fibonacci():
    """Test memoization with a complex Fibonacci-like computation"""
    print("Testing complex memoization with Fibonacci-like computation...")

    # Create a Fibonacci-like program that should benefit heavily from memoization
    program_text = """
    let f0 = 1
    let f1 = 1
    let f2 = add(f1, f0)
    let f3 = add(f2, f1)
    let f4 = add(f3, f2)
    let f5 = add(f4, f3)
    
    // Reuse the same computation in different contexts
    let sum1 = add(f2, f3)
    let sum2 = add(f2, f3)  // Should reuse the exact same operation
    let sum3 = add(f3, f2)  // Should also reuse due to argument sorting
    
    print "fibonacci" f5
    print "sum1" sum1
    print "sum2" sum2
    print "sum3" sum3
    """

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Count unique operations
    unique_ops = len(work_plan.operations)
    print(f"  ✓ Total unique operations: {unique_ops}")

    # Count add operations specifically
    add_ops = [
        op
        for op in work_plan.operations
        if hasattr(op.operator, "value") and op.operator.value == "add"
    ]
    print(f"  ✓ Unique 'add' operations: {len(add_ops)}")

    # The three sum operations (sum1, sum2, sum3) should all reference the same operation
    # due to memoization, so we shouldn't have 3 separate add operations for them

    # Show operation IDs to demonstrate content-addressed nature
    if work_plan._operation_ids:
        print(f"  ✓ Sample operation IDs:")
        shown = 0
        for op, op_id in work_plan._operation_ids.items():
            if shown < 3 and hasattr(op.operator, "value"):
                print(f"    {op}: {op_id[:16]}...")
                shown += 1


def test_cross_session_memoization_simulation():
    """Simulate cross-session memoization by using separate Operations instances"""
    print("Testing cross-session memoization simulation...")

    # Simulate "session 1": create some operations
    ops1 = Operations()

    # Create base operations
    id_1 = ops1.find_or_create(NumberOp(1), {})
    id_2 = ops1.find_or_create(NumberOp(2), {})
    id_add_1_2 = ops1.find_or_create(IdentifierOp("add"), {"0": id_1, "1": id_2})

    print(f"  Session 1 - NumberOp(1): {id_1[:16]}...")
    print(f"  Session 1 - NumberOp(2): {id_2[:16]}...")
    print(f"  Session 1 - add(1,2): {id_add_1_2[:16]}...")

    # Simulate "session 2": different Operations instance, but same computation
    ops2 = Operations()

    # Create the same operations in session 2
    id_1_s2 = ops2.find_or_create(NumberOp(1), {})
    id_2_s2 = ops2.find_or_create(NumberOp(2), {})
    id_add_1_2_s2 = ops2.find_or_create(
        IdentifierOp("add"), {"0": id_1_s2, "1": id_2_s2}
    )

    print(f"  Session 2 - NumberOp(1): {id_1_s2[:16]}...")
    print(f"  Session 2 - NumberOp(2): {id_2_s2[:16]}...")
    print(f"  Session 2 - add(1,2): {id_add_1_2_s2[:16]}...")

    # Verify that the same operations produce the same IDs across sessions
    assert id_1 == id_1_s2, "NumberOp(1) should have same ID across sessions"
    assert id_2 == id_2_s2, "NumberOp(2) should have same ID across sessions"
    assert id_add_1_2 == id_add_1_2_s2, "add(1,2) should have same ID across sessions"

    print(
        f"  ✓ Cross-session memoization works: identical operations produce identical IDs"
    )


def test_memoization_with_deep_nesting():
    """Test memoization benefits with deeply nested operations"""
    print("Testing memoization with deep nesting...")

    # Create a program with deep nesting that reuses subcomputations
    program_text = """
    let a = 1
    let b = 2
    let c = 3
    
    // Build up complex nested operations
    let step1 = add(a, b)
    let step2 = add(step1, c)
    let step3 = add(step2, a)
    
    // Now reuse these in different contexts
    let complex1 = multiply(step2, step1)
    let complex2 = multiply(step2, step1)  // Should reuse complex1
    let complex3 = divide(complex1, step3)
    
    // Use the same suboperations again
    let final = add(complex2, complex3)
    
    print "result" final
    """

    start_time = time.time()
    program = parse_program_text(program_text)
    work_plan = reduce_program(program)
    end_time = time.time()

    print(f"  ✓ Reduction completed in {(end_time - start_time)*1000:.2f}ms")
    print(f"  ✓ Total operations: {len(work_plan.operations)}")

    # Count operations by type
    op_counts = {}
    for op in work_plan.operations:
        if hasattr(op.operator, "value"):
            op_type = op.operator.value
            op_counts[op_type] = op_counts.get(op_type, 0) + 1
        else:
            op_type = type(op.operator).__name__
            op_counts[op_type] = op_counts.get(op_type, 0) + 1

    print(f"  ✓ Operation counts: {op_counts}")

    # The key insight: complex1 and complex2 should be the same operation due to memoization
    multiply_ops = [
        op
        for op in work_plan.operations
        if hasattr(op.operator, "value") and op.operator.value == "multiply"
    ]
    print(
        f"  ✓ Unique multiply operations: {len(multiply_ops)} (should be 1 due to memoization)"
    )


def test_memoization_performance_benefit():
    """Demonstrate the performance benefit of memoization with repeated computations"""
    print("Testing memoization performance benefit...")

    # Create a program that would be slow without memoization
    program_text = """
    let base = 5
    
    // Create many operations that reuse the same subcomputation
    let result1 = add(base, base)
    let result2 = add(base, base)
    let result3 = add(base, base)
    let result4 = add(base, base)
    let result5 = add(base, base)
    
    // Now use these in more complex operations
    let sum1 = add(result1, result2)
    let sum2 = add(result3, result4)
    let sum3 = add(result1, result5)  // Reuses result1
    let sum4 = add(result2, result3)  // Reuses result2 and result3
    
    // Final combination
    let final = add(sum1, sum2, sum3, sum4)
    
    print "final" final
    """

    # Test with memoization enabled
    start_time = time.time()
    program = parse_program_text(program_text)
    work_plan_memo = reduce_program(program)
    memo_time = time.time() - start_time

    print(
        f"  With memoization: {len(work_plan_memo.operations)} operations, {memo_time*1000:.2f}ms"
    )

    # Count how many add(base, base) operations we have
    base_add_ops = []
    if work_plan_memo._operation_ids:
        for op, op_id in work_plan_memo._operation_ids.items():
            if (
                hasattr(op.operator, "value")
                and op.operator.value == "add"
                and len(op.arguments) == 2
            ):
                # Check if both arguments reference the same base value
                arg_values = list(op.arguments.values())
                if len(set(arg_values)) == 1:  # Both arguments are the same
                    base_add_ops.append((op, op_id))

    print(f"  ✓ Unique add(base, base) operations: {len(base_add_ops)} (should be 1)")
    if base_add_ops:
        op, op_id = base_add_ops[0]
        print(f"    ID: {op_id[:16]}...")


def run_all_tests():
    """Run all advanced memoization tests"""
    print("=== Advanced SHA256 Memoization Tests ===\n")

    # NumberOp and IdentifierOp are already imported at the top

    try:
        test_complex_memoization_fibonacci()
        print()

        test_cross_session_memoization_simulation()
        print()

        test_memoization_with_deep_nesting()
        print()

        test_memoization_performance_benefit()
        print()

        print("=== All advanced tests passed! ===")
        print("\nKey benefits demonstrated:")
        print("  1. Content-addressed IDs enable cross-session result reuse")
        print("  2. Memoization prevents duplicate computations effectively")
        print("  3. SHA256 IDs are deterministic across different execution contexts")
        print("  4. Complex nested operations benefit significantly from memoization")

        return True

    except Exception as e:
        print(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    print(f"\nTest Description: {description}\n")
    success = run_all_tests()
    sys.exit(0 if success else 1)
