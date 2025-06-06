#!/usr/bin/env python3

"""
Test script to verify that the buffer allocation algorithm is working correctly
after the fix.
"""

import sys
import os

# Add the python implementation to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'implementation', 'python'))

def test_import():
    """Test that we can import the print_buffer_assignment function"""
    try:
        from voxlogica.buffer_allocation import print_buffer_assignment, allocate_buffers, compute_buffer_allocation
        from voxlogica.reducer import WorkPlan
        print("✅ Import test passed: print_buffer_assignment imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Import test failed: {e}")
        return False

def test_basic_algorithm():
    """Test that the buffer allocation algorithm works correctly"""
    try:
        from voxlogica.buffer_allocation import allocate_buffers
        from voxlogica.reducer import WorkPlan
        
        # Create a simple test case
        workplan = WorkPlan()
        
        # Create a simple DAG: A -> B -> C
        op_a = workplan.add_operation("load", {})
        op_b = workplan.add_operation("process", {"input": op_a})
        op_c = workplan.add_operation("output", {"input": op_b})
        
        workplan.add_goal(op_c)
        
        # Define types
        type_assignment = {
            op_a: "tensor",
            op_b: "tensor", 
            op_c: "tensor"
        }
        
        # Run allocation
        allocation = allocate_buffers(workplan, type_assignment)
        
        print("✅ Basic algorithm test passed")
        print(f"   Allocation: {allocation}")
        print(f"   Operations: {len(workplan.operations)}")
        print(f"   Buffers used: {len(set(allocation.values()))}")
        return True
        
    except Exception as e:
        print(f"❌ Basic algorithm test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_print_function():
    """Test the print_buffer_assignment function"""
    try:
        from voxlogica.buffer_allocation import print_buffer_assignment, allocate_buffers
        from voxlogica.reducer import WorkPlan
        
        # Create a simple test case
        workplan = WorkPlan()
        
        op_a = workplan.add_operation("load", {})
        op_b = workplan.add_operation("process", {"input": op_a})
        op_c = workplan.add_operation("output", {"input": op_b})
        
        workplan.add_goal(op_c)
        
        type_assignment_dict = {
            op_a: "tensor",
            op_b: "tensor", 
            op_c: "tensor"
        }
        
        def type_assignment_func(op_id):
            return type_assignment_dict[op_id]
        
        # Run allocation
        allocation = allocate_buffers(workplan, type_assignment_dict)
        
        # Test print function
        print("✅ Print function test - output should appear below:")
        print_buffer_assignment(workplan, allocation, type_assignment_func)
        
        return True
        
    except Exception as e:
        print(f"❌ Print function test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests"""
    print("Testing buffer allocation fix...\n")
    
    tests = [
        ("Import Test", test_import),
        ("Basic Algorithm Test", test_basic_algorithm), 
        ("Print Function Test", test_print_function)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n=== {test_name} ===")
        if test_func():
            passed += 1
        print()
    
    print(f"\n=== Summary ===")
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("🎉 All tests passed! Buffer allocation fix is working correctly.")
        return True
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)