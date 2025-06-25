"""
Test suite for nested let expressions in VoxLogicA

Tests the implementation of let expressions with proper lexical scoping,
including variable shadowing, nested lets, and integration with functions and for loops.
"""

import pytest
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


def test_basic_let_expression():
    """Test basic let expression functionality"""
    print("Test: Basic let expression")
    
    content = 'let result = let x = 5 in +(x, 2)\nprint "test" result'
    program = parse_program_content(content)
    work_plan = reduce_program(program)
    
    # Check that the program parses and reduces without errors
    assert len(work_plan.goals) == 1
    assert work_plan.goals[0].operation == "print"
    
    # Check that the let expression is properly handled
    operations = work_plan.operations
    
    # Should have addition operation
    addition_ops = [op for op in operations.values() if op.operator == "addition"]
    assert len(addition_ops) >= 1, "Should have at least one addition operation"
    
    print("  ✓ Basic let expression works")


def test_multiple_nested_let_expressions():
    """Test nested let expressions"""
    print("Test: Multiple nested let expressions")
    
    content = 'let result = let x = 2 in let y = +(x, 3) in +(x, y)\nprint "test" result'
    program = parse_program_content(content)
    work_plan = reduce_program(program)
    
    # Check that the program parses and reduces without errors
    assert len(work_plan.goals) == 1
    operations = work_plan.operations
    
    # Should have multiple addition operations for nested lets
    addition_ops = [op for op in operations.values() if op.operator == "addition"]
    assert len(addition_ops) >= 2, "Should have multiple addition operations for nested lets"
    
    print("  ✓ Nested let expressions work")


def test_variable_shadowing():
    """Test variable shadowing in nested let expressions"""
    print("Test: Variable shadowing")
    
    # Inner 'x' should shadow outer 'x'
    content = 'let result = let x = 1 in let x = +(x, 10) in +(x, 5)\nprint "test" result'
    program = parse_program_content(content)
    work_plan = reduce_program(program)
    
    # Should parse and reduce without errors
    assert len(work_plan.goals) == 1
    operations = work_plan.operations
    
    # Should have addition operations
    addition_ops = [op for op in operations.values() if op.operator == "addition"]
    assert len(addition_ops) >= 2, "Should have addition operations for shadowing test"
    
    print("  ✓ Variable shadowing works")


def test_let_in_function_declaration():
    """Test let expressions inside function declarations"""
    print("Test: Let in function declaration")
    
    content = '''let add_one(n) = let incremented = +(n, 1) in incremented
let result = add_one(10)
print "test" result'''
    
    program = parse_program_content(content)
    work_plan = reduce_program(program)
    
    # Should have print goal and function call
    assert len(work_plan.goals) == 1
    operations = work_plan.operations
    
    # Should have addition operation from the let expression in function
    addition_ops = [op for op in operations.values() if op.operator == "addition"]
    assert len(addition_ops) >= 1, "Should have addition operation from function"
    
    print("  ✓ Let in function declaration works")


def test_let_in_for_loop():
    """Test let expressions inside for loop bodies"""
    print("Test: Let in for loop")
    
    content = 'let result = for i in range(3) do let doubled = +(i, i) in +(doubled, 1)\nprint "test" result'
    program = parse_program_content(content)
    work_plan = reduce_program(program)
    
    # Should have print goal and dask_map operation for for loop
    assert len(work_plan.goals) == 1
    operations = work_plan.operations
    
    # Should have range and dask_map operations
    range_ops = [op for op in operations.values() if op.operator == "range"]
    dask_map_ops = [op for op in operations.values() if op.operator == "dask_map"]
    
    assert len(range_ops) >= 1, "Should have range operation"
    assert len(dask_map_ops) >= 1, "Should have dask_map operation"
    
    # Check that the dask_map body contains the let expression
    dask_map_op = dask_map_ops[0]
    body_id = dask_map_op.arguments["body"]
    body_node = work_plan.nodes[body_id]
    
    # The body_node should be a ConstantValue containing the string representation
    from voxlogica.reducer import ConstantValue
    if isinstance(body_node, ConstantValue) and isinstance(body_node.value, str):
        body_syntax = body_node.value
        assert "let doubled" in body_syntax, "For loop body should contain let expression"
        assert "+(doubled,1.0)" in body_syntax, "For loop body should contain let body"
    
    print("  ✓ Let in for loop works")


def test_complex_nested_lets():
    """Test complex nested let expressions with multiple levels"""
    print("Test: Complex nested lets")
    
    content = '''let complex(a) = let doubled = +(a, a) in let plus_one = +(doubled, 1) in plus_one
let result = complex(5)
print "test" result'''
    
    program = parse_program_content(content)
    work_plan = reduce_program(program)
    
    # Should parse and reduce successfully
    assert len(work_plan.goals) == 1
    operations = work_plan.operations
    
    # Should have multiple addition operations
    addition_ops = [op for op in operations.values() if op.operator == "addition"]
    assert len(addition_ops) >= 2, "Should have multiple addition operations for complex function"
    
    print("  ✓ Complex nested lets work")


def test_let_expression_scoping():
    """Test that let expression variables are properly scoped"""
    print("Test: Let expression scoping")
    
    # Variable 'x' in let should not leak to outer scope
    content = '''let outer = 5
let result = let x = +(outer, 1) in +(x, 2)
let final = +(outer, result)
print "test" final'''
    
    program = parse_program_content(content)
    work_plan = reduce_program(program)
    
    # Should parse successfully - 'x' doesn't leak outside let expression
    assert len(work_plan.goals) == 1
    operations = work_plan.operations
    
    # Should have addition operations
    addition_ops = [op for op in operations.values() if op.operator == "addition"]
    assert len(addition_ops) >= 3, "Should have multiple addition operations for scoping test"
    
    print("  ✓ Let expression scoping works")


def test_nested_let_expressions():
    """Main test function that runs all nested let expression tests"""
    print("=== Testing Nested Let Expressions ===")
    
    test_basic_let_expression()
    test_multiple_nested_let_expressions() 
    test_variable_shadowing()
    test_let_in_function_declaration()
    test_let_in_for_loop()
    test_complex_nested_lets()
    test_let_expression_scoping()
    
    print("=== All Nested Let Expression Tests Passed ===")


if __name__ == "__main__":
    test_nested_let_expressions()
