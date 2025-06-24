"""
Test For Loops

This test module verifies the for loop functionality in VoxLogicA-2, including:
- Basic for loop syntax parsing
- For loop reduction to Dask operations
- Multiple for loops in the same program
- Nested expressions in for loop bodies

The tests ensure that for loops are properly converted to Dask bag operations 
using the `dask_map` primitive.
"""
