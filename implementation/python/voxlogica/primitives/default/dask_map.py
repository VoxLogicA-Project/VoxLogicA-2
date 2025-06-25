"""
Dask map primitive for VoxLogicA - implements map operations over Dask bags.

This primitive takes a Dask bag and applies a closure to each element,
returning a new Dask bag with the results.
"""

import dask.bag as db
from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)

def execute(**kwargs) -> db.Bag:
    """
    Apply a closure to each element of a Dask bag.
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected:
                 - '0': The input Dask bag
                 - 'closure': The closure to apply to each element
    
    Returns:
        A new Dask bag with the mapped results
    """
    input_bag = kwargs["0"]
    closure = kwargs["closure"]
    
    if not isinstance(input_bag, db.Bag):
        raise ValueError(f"Expected Dask bag, got {type(input_bag)}")
    
    logger.info(f"Applying dask_map with closure for variable '{closure.variable}'")
    
    # Apply the closure directly to the Dask bag
    # The closure is callable and will handle evaluation properly
    result = input_bag.map(closure)
    
    logger.info(f"Created mapped Dask bag with {result.npartitions} partitions")
    return result

# Register the primitive with metadata
PRIMITIVE_METADATA = {
    "name": "dask_map",
    "description": "Apply a closure to each element of a Dask bag",
    "function": execute,
    "return_type": "dask_bag",
    "arguments": {
        "0": "dask_bag",
        "closure": "closure"
    }
}
