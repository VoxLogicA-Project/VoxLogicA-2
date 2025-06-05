"""
Test for ISSUE_DAG_DICT_ARGS: Ensure DAG operations use dict arguments with string numeric keys
"""

import sys
import os
import tempfile
from pathlib import Path

# Add the implementation directory to the path
repo_root = Path(__file__).resolve().parent.parent
py_impl = repo_root / "implementation" / "python"
if str(py_impl) not in sys.path:
    sys.path.insert(0, str(py_impl))

from voxlogica.parser import parse_program
from voxlogica.reducer import reduce_program

description = """Ensures DAG operations use dict arguments with string numeric keys. Tests that argument keys and values are correct, nested and zero-argument operations are handled, and JSON serialization is correct for the new argument structure."""


def parse_program_text(program_text: str):
    """Helper function to parse program text directly"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".imgql", delete=False) as f:
        f.write(program_text)
        temp_filename = f.name

    try:
        return parse_program(temp_filename)
    finally:
        os.unlink(temp_filename)


def test_basic_operation_with_arguments():
    """Test that operations with arguments use dict with string numeric keys"""

    # Simple program with function calls
    program_text = """let a = 1
let b = 2
let c = add(a, b)
print "result" c"""

    # Parse and reduce the program
    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Find the 'add' operation (should have arguments)
    add_operation = None
    for op in work_plan.operations:
        if hasattr(op.operator, "value") and op.operator.value == "add":
            add_operation = op
            break

    assert add_operation is not None, "Should find the 'add' operation"

    # Check that arguments is a dict
    assert isinstance(add_operation.arguments, dict), "Arguments should be a dict"

    # Check that keys are string representations of numbers
    expected_keys = {"0", "1"}
    actual_keys = set(add_operation.arguments.keys())
    assert (
        actual_keys == expected_keys
    ), f"Expected keys {expected_keys}, got {actual_keys}"

    # Check that values are operation IDs (now strings in SHA256 implementation)
    for key, value in add_operation.arguments.items():
        assert isinstance(key, str), f"Key {key} should be a string"
        assert isinstance(value, str), f"Value {value} should be a str (OperationId)"


def test_nested_operation_arguments():
    """Test nested function calls with multiple arguments"""

    program_text = """let x = 1
let y = 2
let z = 3
let result = multiply(add(x, y), z)
print "nested" result"""

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Find multiply operation
    multiply_op = None
    for op in work_plan.operations:
        if hasattr(op.operator, "value") and op.operator.value == "multiply":
            multiply_op = op
            break

    assert multiply_op is not None, "Should find the 'multiply' operation"
    assert isinstance(multiply_op.arguments, dict), "Arguments should be a dict"
    assert set(multiply_op.arguments.keys()) == {
        "0",
        "1",
    }, "Should have keys '0' and '1'"


def test_zero_arguments():
    """Test that operations with no arguments have empty dict"""

    program_text = """let a = 42
print "answer" a"""

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Find the constant operation (42)
    const_op = None
    for op in work_plan.operations:
        if hasattr(op.operator, "value") and op.operator.value == 42:
            const_op = op
            break

    assert const_op is not None, "Should find the constant operation"
    assert isinstance(const_op.arguments, dict), "Arguments should be a dict"
    assert (
        len(const_op.arguments) == 0
    ), "Should have empty arguments dict for constants"


def test_json_serialization():
    """Test that the work plan can be serialized to JSON with dict arguments"""

    program_text = """let result = max(1, 2, 3)
save "output" result"""

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Convert to JSON
    json_data = work_plan.to_json()

    # Check the structure
    assert "operations" in json_data
    assert "goals" in json_data

    # Find the max operation in JSON
    max_op_json = None
    for op_json in json_data["operations"]:
        if op_json["operator"] == "max":
            max_op_json = op_json
            break

    assert max_op_json is not None, "Should find the 'max' operation in JSON"
    assert isinstance(max_op_json["arguments"], dict), "JSON arguments should be a dict"
    expected_keys = {"0", "1", "2"}
    actual_keys = set(max_op_json["arguments"].keys())
    assert (
        actual_keys == expected_keys
    ), f"Expected keys {expected_keys}, got {actual_keys}"


if __name__ == "__main__":
    print(f"\nTest Description: {description}\n")
    test_basic_operation_with_arguments()
    test_nested_operation_arguments()
    test_zero_arguments()
    test_json_serialization()
    print("All tests passed!")
