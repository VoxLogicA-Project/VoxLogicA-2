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
    
    # Create a closure wrapper that uses the storage system to resolve operation IDs
    def storage_resolving_closure(value):
        # Execute the closure to get an operation ID
        op_id = closure(value)
        
        # If it's not an operation ID (i.e., a direct value), return it
        if not isinstance(op_id, str) or len(op_id) != 64:  # SHA256 is 64 chars
            return op_id
            
        # It's an operation ID - check storage system
        try:
            from voxlogica.execution import get_execution_engine
            engine = get_execution_engine()
            
            # Check if result is available in storage
            if engine.storage.exists(op_id):
                result = engine.storage.retrieve(op_id)
                logger.debug(f"Retrieved result for {op_id[:8]}... from storage")
                return result
            else:
                # Operation not yet computed - we should execute it
                logger.debug(f"Operation {op_id[:8]}... not in storage, need to compute")
                
                # For now, return the operation ID and let the system handle it
                # In a fully lazy system, this would trigger computation
                return op_id
                
        except Exception as e:
            logger.warning(f"Failed to resolve operation {op_id[:8]}...: {e}")
            return op_id
    
    # Apply the storage-resolving closure to the Dask bag
    result = input_bag.map(storage_resolving_closure)
    
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
