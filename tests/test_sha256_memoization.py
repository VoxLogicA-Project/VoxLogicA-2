#!/usr/bin/env python3
"""
Test for ISSUE_SHA256_IDS: Verify SHA256-based content-addressed IDs and memoization
"""

import sys
import os
import tempfile
import hashlib
from pathlib import Path

# Add the implementation directory to the path
repo_root = Path(__file__).resolve().parent.parent
py_impl = repo_root / "implementation" / "python"
if str(py_impl) not in sys.path:
    sys.path.insert(0, str(py_impl))

from voxlogica.parser import parse_program
from voxlogica.reducer import (
    reduce_program,
    Operations,
    NumberOp,
    StringOp,
    IdentifierOp,
)


def parse_program_text(program_text: str):
    """Helper function to parse program text directly"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".imgql", delete=False) as f:
        f.write(program_text)
        temp_filename = f.name

    try:
        return parse_program(temp_filename)
    finally:
        os.unlink(temp_filename)


def test_sha256_ids_are_deterministic():
    """Test that equivalent operations produce the same SHA256 ID"""
    print("Testing SHA256 ID determinism...")

    # Create two operations instances with the same content
    ops1 = Operations()
    ops2 = Operations()

    # Create the same operation in both instances
    id1 = ops1.find_or_create(NumberOp(42), {})
    id2 = ops2.find_or_create(NumberOp(42), {})

    assert id1 == id2, f"Same operation should produce same ID: {id1} != {id2}"
    print(f"  ✓ Same operations produce same ID: {id1[:16]}...")

    # Test with arguments
    id3 = ops1.find_or_create(IdentifierOp("add"), {"0": id1, "1": id1})
    id4 = ops2.find_or_create(IdentifierOp("add"), {"0": id2, "1": id2})

    assert (
        id3 == id4
    ), f"Same operation with args should produce same ID: {id3} != {id4}"
    print(f"  ✓ Same operations with args produce same ID: {id3[:16]}...")


def test_different_operations_produce_different_ids():
    """Test that different operations produce different SHA256 IDs"""
    print("Testing SHA256 ID uniqueness...")

    ops = Operations()

    id1 = ops.find_or_create(NumberOp(42), {})
    id2 = ops.find_or_create(NumberOp(43), {})
    id3 = ops.find_or_create(StringOp("42"), {})

    assert id1 != id2, f"Different numbers should produce different IDs: {id1} == {id2}"
    assert id1 != id3, f"Number and string should produce different IDs: {id1} == {id3}"
    assert id2 != id3, f"Different values should produce different IDs: {id2} == {id3}"

    print(f"  ✓ Different operations produce different IDs")
    print(f"    NumberOp(42): {id1[:16]}...")
    print(f"    NumberOp(43): {id2[:16]}...")
    print(f"    StringOp('42'): {id3[:16]}...")


def test_memoization_works():
    """Test that memoization prevents duplicate operations"""
    print("Testing memoization...")

    ops = Operations()

    # Create the same operation multiple times
    id1 = ops.find_or_create(NumberOp(42), {})
    id2 = ops.find_or_create(NumberOp(42), {})
    id3 = ops.find_or_create(NumberOp(42), {})

    # All should return the same ID
    assert id1 == id2 == id3, f"Memoization failed: {id1}, {id2}, {id3}"

    # Should only have one operation in the collection
    assert len(ops.by_id) == 1, f"Expected 1 operation, got {len(ops.by_id)}"

    print(f"  ✓ Memoization works: {len(ops.by_id)} operation(s) for 3 create calls")


def test_program_memoization():
    """Test memoization in a complete program"""
    print("Testing program-level memoization...")

    # Program that should create the same constant multiple times
    program_text = """
    let a = 1
    let b = 1  
    let c = 1
    let sum = add(a, b, c)
    print "result" sum
    """

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Count operations of different types
    number_ops = []
    identifier_ops = []

    for op in work_plan.operations:
        if hasattr(op.operator, "value"):
            if op.operator.value == 1:
                number_ops.append(op)
            elif op.operator.value == "add":
                identifier_ops.append(op)

    # Should only have one NumberOp(1) due to memoization
    assert len(number_ops) == 1, f"Expected 1 NumberOp(1), got {len(number_ops)}"

    print(
        f"  ✓ Program memoization works: {len(number_ops)} NumberOp(1) for 3 declarations"
    )
    print(f"  ✓ Total operations: {len(work_plan.operations)}")

    # Test that the IDs are present
    if work_plan._operation_ids:
        print(f"  ✓ Operation IDs tracked: {len(work_plan._operation_ids)} mappings")
        # Show some IDs
        for i, (op, op_id) in enumerate(work_plan._operation_ids.items()):
            if i < 3:  # Show first 3
                print(f"    {op}: {op_id[:16]}...")


def test_argument_order_consistency():
    """Test that argument order doesn't affect ID computation"""
    print("Testing argument order consistency...")

    ops = Operations()

    # Create base operations
    id_a = ops.find_or_create(NumberOp(1), {})
    id_b = ops.find_or_create(NumberOp(2), {})

    # Create operation with arguments in different order
    # Note: Our implementation sorts arguments, so order shouldn't matter
    id1 = ops.find_or_create(IdentifierOp("add"), {"0": id_a, "1": id_b})
    id2 = ops.find_or_create(IdentifierOp("add"), {"1": id_b, "0": id_a})

    assert id1 == id2, f"Argument order should not affect ID: {id1} != {id2}"
    print(f"  ✓ Argument order doesn't affect ID: {id1[:16]}...")


def test_sha256_properties():
    """Test basic SHA256 properties of generated IDs"""
    print("Testing SHA256 properties...")

    ops = Operations()

    # Create a few operations
    id1 = ops.find_or_create(NumberOp(42), {})
    id2 = ops.find_or_create(StringOp("hello"), {})

    # Check that IDs are valid hex strings of correct length
    assert len(id1) == 64, f"SHA256 should be 64 hex chars, got {len(id1)}"
    assert len(id2) == 64, f"SHA256 should be 64 hex chars, got {len(id2)}"

    # Check that they're valid hex
    try:
        int(id1, 16)
        int(id2, 16)
    except ValueError:
        assert False, "IDs should be valid hexadecimal"

    print(f"  ✓ IDs are valid 64-character hex strings")
    print(f"    ID1: {id1}")
    print(f"    ID2: {id2}")


def run_all_tests():
    """Run all tests"""
    print("=== Testing SHA256-based Content-Addressed IDs ===\n")

    try:
        test_sha256_ids_are_deterministic()
        print()

        test_different_operations_produce_different_ids()
        print()

        test_memoization_works()
        print()

        test_program_memoization()
        print()

        test_argument_order_consistency()
        print()

        test_sha256_properties()
        print()

        print("=== All tests passed! ===")
        return True

    except Exception as e:
        print(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
