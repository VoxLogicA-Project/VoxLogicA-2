"""
Range primitive for VoxLogicA-2

Implements range operation that returns a Dask bag for lazy for loop support.
"""

import dask.bag as db
from typing import Union

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs) -> db.Bag:
    """
    Execute range operation that returns a Dask bag
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': stop} for range(stop) or {'0': start, '1': stop} for range(start, stop)
        
    Returns:
        Dask bag containing integers in the specified range
        
    Raises:
        ValueError: If arguments are invalid or not integers
    """
    try:
        # Check if we have start and stop arguments or just stop
        if '0' not in kwargs:
            raise ValueError("Range requires at least one argument")
        
        # Helper function to convert and validate argument
        def validate_int_arg(arg, name):
            if isinstance(arg, float):
                if arg.is_integer():
                    arg = int(arg)
                else:
                    raise ValueError(f"Range {name} must be an integer, got float: {arg}")
            
            if not isinstance(arg, int):
                raise ValueError(f"Range {name} must be an integer, got: {type(arg).__name__}")
            
            return arg
        
        # Handle both range(stop) and range(start, stop) cases
        if '1' in kwargs:
            # Two arguments: range(start, stop)
            start = validate_int_arg(kwargs['0'], "start")
            stop = validate_int_arg(kwargs['1'], "stop")
        else:
            # One argument: range(stop) - implicitly start from 0
            start = 0
            stop = validate_int_arg(kwargs['0'], "stop")
        
        # Validate range
        if stop < start:
            # Empty range is valid (like Python's range)
            range_list = []
        else:
            range_list = list(range(start, stop))
        
        # Create Dask bag from range
        if len(range_list) == 0:
            # Handle empty range
            dask_bag = db.from_sequence([], npartitions=1)
        else:
            # Use reasonable number of partitions
            npartitions = max(1, min(len(range_list), 10))
            dask_bag = db.from_sequence(range_list, npartitions=npartitions)
        
        return dask_bag
        
    except Exception as e:
        raise ValueError(f"Range operation failed: {e}") from e


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="range",
    namespace="default",
    kind="sequence",
    arity=AritySpec(min_args=1, max_args=2),
    attrs_schema={},
    planner=default_planner_factory("default.range", kind="sequence"),
    kernel_name="default.range",
    description="Create a sequence from integer range bounds",
)
