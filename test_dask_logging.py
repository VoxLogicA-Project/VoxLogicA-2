#!/usr/bin/env python3
"""
Test script to verify Dask logging suppression works correctly
"""

import sys
import logging
from pathlib import Path

# Add the implementation to the Python path
sys.path.insert(0, str(Path(__file__).parent / "implementation" / "python"))

from voxlogica.main import setup_logging
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

def test_dask_logging_suppression():
    """Test that Dask logging messages are suppressed"""
    
    # Set up logging with our custom configuration
    setup_logging(debug=False, verbose=False)  # Normal mode - should suppress Dask
    
    print("Testing Dask logging suppression...")
    print("You should see VoxLogicA output but minimal Dask noise.")
    print("")
    
    # Create a for loop that will trigger Dask operations
    content = '''let result = for i in range(10) do i * 2
print "doubled_numbers" result

let squared = for x in range(5) do x * x  
print "squares" squared'''

    try:
        print("Parsing program...")
        program = parse_program_content(content)
        
        print("Reducing to work plan...")
        work_plan = reduce_program(program)
        
        print("Accessing operations (triggers lazy compilation)...")
        ops = work_plan.operations
        
        print(f"✓ Created {len(ops)} operations successfully")
        print(f"✓ {len(work_plan.goals)} goals defined")
        
        # Show operations
        print("\nOperations created:")
        for op_id, op in ops.items():
            if op.operator in ['range', 'dask_map']:
                print(f"  - {op.operator}")
        
        print("\n✓ Test completed - check that you don't see Dask connection/worker messages")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_dask_logging_suppression()
