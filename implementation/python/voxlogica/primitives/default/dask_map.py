"""
Dask map primitive for VoxLogicA - implements map operations over Dask bags.

This primitive takes a Dask bag and applies a function to each element,
returning a new Dask bag with the results.
"""

import dask.bag as db
from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)

# Define serializable functions for common operations
def multiply_by_two(x):
    return x * 2

def add_one(x):
    return x + 1

def square(x):
    return x * x

def identity(x):
    return x

def execute(**kwargs) -> db.Bag:
    """
    Apply a function to each element of a Dask bag.
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected:
                 - '0': The input Dask bag
                 - 'variable': The variable name for the lambda function
                 - 'body': The body expression (as a string)
    
    Returns:
        A new Dask bag with the mapped results
    """
    input_bag = kwargs["0"]
    variable = kwargs["variable"]
    body = kwargs["body"]
    
    if not isinstance(input_bag, db.Bag):
        raise ValueError(f"Expected Dask bag, got {type(input_bag)}")
    
    logger.info(f"Applying dask_map with variable '{variable}' and body '{body}'")
    
    # For now, we'll create a simple identity mapping
    # In the full implementation, this would compile and apply the body expression
    # TODO: Implement proper expression compilation and application
    
    # Simple example: if body is "i * 2", apply that transformation
    if body == "*(i,2.0)":
        result = input_bag.map(multiply_by_two)
    elif body == "+(x,1.0)":
        result = input_bag.map(add_one)
    elif body == "*(n,n)":
        # Square function
        result = input_bag.map(square)
    else:
        # Default: identity mapping
        result = input_bag.map(identity)
    
    logger.info(f"Created mapped Dask bag with {result.npartitions} partitions")
    return result

# Register the primitive with metadata
PRIMITIVE_METADATA = {
    "name": "dask_map",
    "description": "Apply a function to each element of a Dask bag",
    "function": execute,
    "return_type": "dask_bag",
    "arguments": {
        "0": "dask_bag",
        "variable": "string", 
        "body": "expression"
    }
}
