"""
Enqueue primitive for VoxLogicA-2

This primitive demonstrates the capability to enqueue another primitive operation
from within a primitive execution. It returns a special result that signals the
execution engine to schedule additional work.

This is a proof-of-concept showing that primitives can dynamically create
new computation tasks.
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

def execute(**kwargs) -> Dict[str, Any]:
    """
    Execute enqueue primitive - schedules another primitive for execution
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: 
                 - '0': primitive_name (string) - name of primitive to enqueue
                 - '1': arg1 (optional) - first argument for enqueued primitive
                 - '2': arg2 (optional) - second argument for enqueued primitive
                 - ...
        
    Returns:
        A dictionary containing both the immediate result and enqueue instruction
        
    Raises:
        ValueError: If arguments are invalid or missing
    """
    try:
        # Get the primitive name to enqueue
        if '0' not in kwargs:
            raise ValueError("Enqueue requires at least one argument: the primitive name to enqueue")
        
        primitive_name = kwargs['0']
        
        if not isinstance(primitive_name, str):
            raise ValueError("Primitive name must be a string")
        
        # Collect arguments for the enqueued primitive (skip the first argument which is the primitive name)
        enqueued_args = {}
        arg_index = 0
        for key in sorted(kwargs.keys()):
            if key != '0':  # Skip the primitive name argument
                enqueued_args[str(arg_index)] = kwargs[key]
                arg_index += 1
        
        # Log the enqueueing action
        logger.info(f"Enqueue primitive: scheduling '{primitive_name}' with args: {enqueued_args}")
        
        # For now, we'll return a special structured result that indicates 
        # what should be enqueued. In a full implementation, this could be
        # processed by the execution engine to actually schedule the work.
        result = {
            "immediate_result": f"Scheduled {primitive_name} for execution",
            "enqueue_instruction": {
                "primitive": primitive_name,
                "arguments": enqueued_args,
                "timestamp": __import__('time').time()
            }
        }
        
        logger.info(f"Enqueue result: {result}")
        return result
        
    except Exception as e:
        logger.error(f"Enqueue primitive failed: {e}")
        raise ValueError(f"Enqueue computation failed: {e}") from e
