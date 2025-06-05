"""
Test that SHA256 IDs are correctly included in JSON exports
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


def parse_program_text(program_text: str):
    """Helper function to parse program text directly"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".imgql", delete=False) as f:
        f.write(program_text)
        temp_filename = f.name

    try:
        return parse_program(temp_filename)
    finally:
        os.unlink(temp_filename)


def test_json_export_includes_sha256_ids():
    """Test that JSON export includes SHA256 ID field for each operation"""

    program_text = """
    let a = 1
    let b = 2
    let c = add(a, b)
    print "result" c
    """

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Convert to JSON
    json_data = work_plan.to_json()

    # Verify structure
    assert "operations" in json_data
    assert "goals" in json_data
    assert isinstance(json_data["operations"], list)

    # Verify that each operation has an ID field
    for i, operation in enumerate(json_data["operations"]):
        assert "id" in operation, f"Operation {i} missing 'id' field"
        assert "operator" in operation, f"Operation {i} missing 'operator' field"
        assert "arguments" in operation, f"Operation {i} missing 'arguments' field"

        # Verify ID is a non-empty string (should be SHA256 hash)
        op_id = operation["id"]
        assert isinstance(
            op_id, str
        ), f"Operation {i} ID should be string, got {type(op_id)}"
        assert (
            len(op_id) == 64
        ), f"Operation {i} ID should be 64 chars (SHA256), got {len(op_id)}"
        assert (
            op_id.isalnum()
        ), f"Operation {i} ID should be alphanumeric (hex), got {op_id}"


def test_json_export_id_matches_arguments():
    """Test that operation IDs match the argument references"""

    program_text = """
    let x = 5
    let y = 10
    let sum = add(x, y)
    print "sum" sum
    """

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Convert to JSON
    json_data = work_plan.to_json()

    # Build mapping from ID to operation
    id_to_operation = {op["id"]: op for op in json_data["operations"]}

    # Find the add operation
    add_operation = None
    for op in json_data["operations"]:
        if op["operator"] == "add":
            add_operation = op
            break

    assert add_operation is not None, "Should find add operation"

    # Verify that argument IDs reference actual operations
    for arg_key, arg_id in add_operation["arguments"].items():
        assert (
            arg_id in id_to_operation
        ), f"Argument {arg_key} references non-existent ID {arg_id}"

        # Verify the referenced operation exists
        referenced_op = id_to_operation[arg_id]
        assert "operator" in referenced_op

        # For this test, arguments should reference the constant operations
        assert referenced_op["operator"] in [
            5,
            10,
        ], f"Expected constant, got {referenced_op['operator']}"


def test_json_export_goal_references_valid_id():
    """Test that goals reference valid operation IDs"""

    program_text = """
    let result = multiply(3, 4)
    save "output" result
    """

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Convert to JSON
    json_data = work_plan.to_json()

    # Build set of all operation IDs
    operation_ids = {op["id"] for op in json_data["operations"]}

    # Verify that goals reference valid operation IDs
    for goal in json_data["goals"]:
        assert "operation_id" in goal, "Goal missing operation_id field"
        goal_op_id = goal["operation_id"]
        assert (
            goal_op_id in operation_ids
        ), f"Goal references non-existent operation ID {goal_op_id}"


def test_json_export_deterministic_ids():
    """Test that same program produces same IDs across multiple runs"""

    program_text = """
    let a = 42
    let b = 24
    let result = subtract(a, b)
    print "diff" result
    """

    # Run the same program twice
    program1 = parse_program_text(program_text)
    work_plan1 = reduce_program(program1)
    json_data1 = work_plan1.to_json()

    program2 = parse_program_text(program_text)
    work_plan2 = reduce_program(program2)
    json_data2 = work_plan2.to_json()

    # Operation IDs should be identical
    ids1 = [op["id"] for op in json_data1["operations"]]
    ids2 = [op["id"] for op in json_data2["operations"]]

    assert ids1 == ids2, "Same program should produce same operation IDs"

    # Verify specific operations match
    for op1, op2 in zip(json_data1["operations"], json_data2["operations"]):
        assert (
            op1["id"] == op2["id"]
        ), f"Operation ID mismatch: {op1['id']} != {op2['id']}"
        assert (
            op1["operator"] == op2["operator"]
        ), f"Operator mismatch: {op1['operator']} != {op2['operator']}"
        assert (
            op1["arguments"] == op2["arguments"]
        ), f"Arguments mismatch: {op1['arguments']} != {op2['arguments']}"


def test_json_export_consistent_with_internal_ids():
    """Test that JSON export IDs match internal operation IDs"""

    program_text = """
    let value = 100
    save "hundred" value
    """

    program = parse_program_text(program_text)
    work_plan = reduce_program(program)

    # Get internal operation IDs
    internal_ids = set()
    if work_plan._operation_ids:
        internal_ids = set(work_plan._operation_ids.values())

    # Get JSON export IDs
    json_data = work_plan.to_json()
    export_ids = {op["id"] for op in json_data["operations"]}

    # Should be the same set of IDs
    assert (
        export_ids == internal_ids
    ), "JSON export IDs should match internal operation IDs"

    # Verify goal references use the same ID space
    for goal in json_data["goals"]:
        goal_id = goal["operation_id"]
        assert goal_id in export_ids, f"Goal references ID {goal_id} not in export"
        assert (
            goal_id in internal_ids
        ), f"Goal references ID {goal_id} not in internal IDs"
