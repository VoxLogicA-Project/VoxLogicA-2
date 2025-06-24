#!/usr/bin/env python3
"""
Test script to verify Dask logging appears in debug mode
"""

import sys
import logging
from pathlib import Path

# Add the implementation to the Python path
sys.path.insert(0, str(Path(__file__).parent / "implementation" / "python"))

from voxlogica.main import setup_logging
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

def test_debug_mode():
    """Test that Dask logging messages appear in debug mode"""
    
    # Set up logging with debug enabled
    setup_logging(debug=True, verbose=False)
    
    print("Testing debug mode - you should see more detailed Dask logs...")
    print("")
    
    # Create a simple for loop
    content = '''let result = for i in range(3) do i + 1
print "incremented" result'''

    try:
        program = parse_program_content(content)
        work_plan = reduce_program(program)
        ops = work_plan.operations
        
        print(f"✓ Created {len(ops)} operations with debug logging enabled")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_debug_mode()
