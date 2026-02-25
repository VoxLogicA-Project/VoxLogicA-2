"""
Advanced Enqueue primitive for VoxLogicA-2

This primitive demonstrates more sophisticated enqueueing capabilities,
including the ability to dynamically generate new computation tasks
that could potentially be integrated with the execution system.

This version shows how a primitive could return special instructions
that the execution engine could interpret to schedule additional work.
"""

from typing import Dict, Any
import logging
import time

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory

logger = logging.getLogger(__name__)

class EnqueueInstruction:
    """
    Represents an instruction to enqueue a primitive operation.
    
    This class encapsulates all the information needed to schedule
    a primitive for execution, including metadata about when and
    how it should be executed.
    """
    
    def __init__(self, primitive_name: str, arguments: Dict[str, Any], 
                 priority: int = 0, delay: float = 0.0):
        """
        Initialize an enqueue instruction.
        
        Args:
            primitive_name: Name of the primitive to execute
            arguments: Arguments to pass to the primitive
            priority: Execution priority (higher = more urgent)
            delay: Delay in seconds before execution
        """
        self.primitive_name = primitive_name
        self.arguments = arguments
        self.priority = priority
        self.delay = delay
        self.created_at = time.time()
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "primitive_name": self.primitive_name,
            "arguments": self.arguments,
            "priority": self.priority,
            "delay": self.delay,
            "created_at": self.created_at
        }

def execute(**kwargs) -> Dict[str, Any]:
    """
    Execute advanced enqueue primitive with sophisticated scheduling capabilities.
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected:
                 - '0': primitive_name (string) - name of primitive to enqueue
                 - '1': priority (optional, number) - execution priority (default: 0)
                 - '2': delay (optional, number) - delay in seconds (default: 0.0)
                 - remaining args: arguments for the enqueued primitive
        
    Returns:
        A dictionary containing the result and enqueue instructions
        
    Raises:
        ValueError: If arguments are invalid or missing
    """
    try:
        # Get the primitive name to enqueue
        if '0' not in kwargs:
            raise ValueError("Advanced enqueue requires at least one argument: the primitive name")
        
        primitive_name = kwargs['0']
        if not isinstance(primitive_name, str):
            raise ValueError("Primitive name must be a string")
        
        # Get optional priority (default: 0)
        priority = 0
        if '1' in kwargs:
            try:
                priority = int(float(kwargs['1']))
            except (ValueError, TypeError):
                logger.warning(f"Invalid priority value: {kwargs['1']}, using default: 0")
        
        # Get optional delay (default: 0.0)
        delay = 0.0
        if '2' in kwargs:
            try:
                delay = float(kwargs['2'])
            except (ValueError, TypeError):
                logger.warning(f"Invalid delay value: {kwargs['2']}, using default: 0.0")
        
        # Collect arguments for the enqueued primitive (skip control arguments)
        enqueued_args = {}
        arg_index = 0
        for key in sorted(kwargs.keys()):
            if key not in ['0', '1', '2']:  # Skip control arguments
                enqueued_args[str(arg_index)] = kwargs[key]
                arg_index += 1
        
        # Create enqueue instruction
        instruction = EnqueueInstruction(
            primitive_name=primitive_name,
            arguments=enqueued_args,
            priority=priority,
            delay=delay
        )
        
        # Log the enqueueing action
        logger.info(f"Advanced enqueue: scheduling '{primitive_name}' with priority {priority}, delay {delay}s")
        logger.debug(f"Enqueue instruction: {instruction.to_dict()}")
        
        # Calculate scheduled execution time
        scheduled_time = time.time() + delay
        
        # Return comprehensive result
        result = {
            "status": "enqueued",
            "primitive_scheduled": primitive_name,
            "arguments_count": len(enqueued_args),
            "priority": priority,
            "delay_seconds": delay,
            "scheduled_for": scheduled_time,
            "enqueue_instruction": instruction.to_dict(),
            "metadata": {
                "enqueue_primitive_version": "2.0",
                "timestamp": instruction.created_at,
                "capabilities": [
                    "priority_scheduling",
                    "delayed_execution",
                    "argument_forwarding",
                    "metadata_tracking"
                ]
            }
        }
        
        # Special case: if we're enqueueing a fibonacci, show what the result would be
        if primitive_name == "fibonacci" and "0" in enqueued_args:
            try:
                n = int(float(enqueued_args["0"]))
                if 0 <= n <= 20:  # Only for small values
                    result["preview"] = f"fibonacci({n}) will compute the {n}th Fibonacci number"
            except (ValueError, TypeError):
                pass
        
        logger.info(f"Advanced enqueue completed: {result['status']}")
        return result
        
    except Exception as e:
        logger.error(f"Advanced enqueue primitive failed: {e}")
        raise ValueError(f"Advanced enqueue computation failed: {e}") from e


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="enqueue_advanced",
    namespace="test",
    kind="effect",
    arity=AritySpec.variadic(1),
    attrs_schema={},
    planner=default_planner_factory("test.enqueue_advanced", kind="effect"),
    kernel_name="test.enqueue_advanced",
    description="Return advanced enqueue instructions with metadata",
)
