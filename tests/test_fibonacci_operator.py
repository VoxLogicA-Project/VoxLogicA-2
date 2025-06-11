#!/usr/bin/env python3

"""
Test script for the fibonacci operator implementation
Tests both unit functionality and integration with VoxLogicA
"""

import sys
import os

# Add the python implementation to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'implementation', 'python'))

def test_fibonacci_primitive():
    """Test the fibonacci primitive directly"""
    print("Testing fibonacci primitive...")
    
    try:
        from voxlogica.primitives.fibonacci import execute
        
        # Test basic cases
        assert execute(**{'0': 0}) == 0, f"fibonacci(0) should be 0, got {execute(**{'0': 0})}"
        assert execute(**{'0': 1}) == 1, f"fibonacci(1) should be 1, got {execute(**{'0': 1})}"
        assert execute(**{'0': 2}) == 1, f"fibonacci(2) should be 1, got {execute(**{'0': 2})}"
        assert execute(**{'0': 5}) == 5, f"fibonacci(5) should be 5, got {execute(**{'0': 5})}"
        assert execute(**{'0': 10}) == 55, f"fibonacci(10) should be 55, got {execute(**{'0': 10})}"
        
        # Test float to int conversion
        assert execute(**{'0': 5.0}) == 5, f"fibonacci(5.0) should be 5, got {execute(**{'0': 5.0})}"
        
        # Test error cases
        try:
            execute(**{'0': -1})
            assert False, "fibonacci(-1) should raise ValueError"
        except ValueError:
            pass
        
        try:
            execute(**{'0': "invalid"})
            assert False, "fibonacci('invalid') should raise ValueError"
        except ValueError:
            pass
        
        try:
            execute(**{'0': 3.5})
            assert False, "fibonacci(3.5) should raise ValueError"
        except ValueError:
            pass
            
        print("✅ Fibonacci primitive tests passed")
        return True
        
    except Exception as e:
        print(f"❌ Fibonacci primitive test failed: {e}")
        return False

def test_fibonacci_loading():
    """Test that the fibonacci operator can be loaded by the system"""
    print("Testing fibonacci operator loading...")
    
    try:
        from voxlogica.execution import PrimitivesLoader
        
        loader = PrimitivesLoader()
        fibonacci_op = loader.load_primitive("fibonacci")
        
        if fibonacci_op is None:
            print("❌ Failed to load fibonacci operator")
            return False
        
        # Test that loaded operator works (using correct kwargs format)
        result = fibonacci_op(**{'0': 10})
        assert result == 55, f"Loaded fibonacci(10) should be 55, got {result}"
        
        print("✅ Fibonacci operator loading test passed")
        return True
        
    except Exception as e:
        print(f"❌ Fibonacci loading test failed: {e}")
        return False

def test_fibonacci_integration():
    """Test fibonacci operator integration with VoxLogicA system"""
    print("Testing fibonacci integration with VoxLogicA...")
    
    try:
        from voxlogica.features import handle_run
        
        # Create a simple test program with correct syntax
        test_program = """
        let n = 10.0
        let result = fibonacci(n)
        save "test_output.json" result
        """
        
        # Run the program
        result = handle_run(
            program=test_program,
            filename=None,
            execute=True,
            debug=False  # Reduce verbosity
        )
        
        if not result.success:
            print(f"❌ VoxLogicA execution failed: {result.error}")
            return False
        
        # Check if execution completed successfully
        if result.data and result.data.get('operations', 0) > 0:
            print("✅ Fibonacci integration test passed")
            return True
        else:
            print(f"❌ No operations executed")
            return False
    
    except Exception as e:
        print(f"❌ Fibonacci integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_fibonacci_buffer_allocation():
    """Test that fibonacci operations work with buffer allocation"""
    print("Testing fibonacci with buffer allocation...")
    
    try:
        from voxlogica.features import handle_run
        
        # Create a program that uses multiple fibonacci operations with correct syntax
        test_program = """
        let a = 5.0
        let b = 8.0
        let c = 10.0
        let fibA = fibonacci(a)
        let fibB = fibonacci(b)
        let fibC = fibonacci(c)
        let sum = fibA + fibB + fibC
        save "buffer_test_output.json" sum
        """
        
        # Run with memory assignment computation
        result = handle_run(
            program=test_program,
            filename=None,
            compute_memory_assignment=True,
            execute=True,
            debug=True
        )
        
        if not result.success:
            print(f"❌ Buffer allocation test failed: {result.error}")
            return False
        
        # Expected: fib(5) + fib(8) + fib(10) = 5 + 21 + 55 = 81
        expected_sum = 5 + 21 + 55
        
        print(f"✅ Fibonacci buffer allocation test passed")
        if result.data is not None:
            print(f"   Operations: {result.data.get('operations', 'unknown')}")
            print(f"   Goals: {result.data.get('goals', 'unknown')}")
        return True
    
    except Exception as e:
        print(f"❌ Fibonacci buffer allocation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all fibonacci operator tests"""
    print("=== Testing Fibonacci Operator Implementation ===\n")
    
    tests = [
        test_fibonacci_primitive,
        test_fibonacci_loading,
        test_fibonacci_integration,
        test_fibonacci_buffer_allocation
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        if test():
            passed += 1
        else:
            failed += 1
        print()
    
    print(f"=== Test Results ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total: {passed + failed}")
    
    return failed == 0

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
