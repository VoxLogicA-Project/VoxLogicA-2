"""
Range primitive for VoxLogicA-2

Implements range operation that returns a Dask bag for lazy for loop support.
"""

import dask.bag as db
from typing import Union

def execute(n: Union[int, float]) -> db.Bag:
    """
    Execute range operation that returns a Dask bag
    
    Args:
        n: Upper limit for range (exclusive), must be a non-negative integer
        
    Returns:
        Dask bag containing integers from 0 to n-1
        
    Raises:
        ValueError: If n is negative or not an integer
    """
    try:
        # Convert to int if it's a float representing an integer
        if isinstance(n, float):
            if n.is_integer():
                n = int(n)
            else:
                raise ValueError(f"Range requires an integer, got float: {n}")
        
        # Validate input
        if not isinstance(n, int):
            raise ValueError(f"Range requires an integer, got: {type(n).__name__}")
        
        if n < 0:
            raise ValueError(f"Range requires a non-negative integer, got: {n}")
        
        # Create Dask bag from range
        # Use list(range(n)) to create the sequence, then convert to Dask bag
        range_list = list(range(n))
        dask_bag = db.from_sequence(range_list, npartitions=max(1, min(n, 10)))
        
        return dask_bag
        
    except Exception as e:
        raise ValueError(f"Range operation failed: {e}") from e
