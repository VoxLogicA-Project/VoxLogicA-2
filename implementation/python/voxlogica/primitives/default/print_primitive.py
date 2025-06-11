"""
Print primitive for VoxLogicA-2

Implements print operation for displaying values.
Note: This is a simple implementation for testing.
In a full execution system, print would be handled as a goal.
"""

def execute(message, value):
    """
    Execute print operation
    
    Args:
        message: Message string to print
        value: Value to print
        
    Returns:
        The printed message (for chaining)
    """
    try:
        # Remove quotes from message if present
        if isinstance(message, str):
            if message.startswith('"') and message.endswith('"'):
                message = message[1:-1]
        
        print(f"{message}: {value}")
        return f"{message}: {value}"
    except Exception as e:
        raise ValueError(f"Print failed: {e}") from e
