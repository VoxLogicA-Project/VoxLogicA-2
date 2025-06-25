"""
Print primitive for VoxLogicA-2

Implements print operation for displaying values, including Dask bags.
Note: This is a simple implementation for testing.
In a full execution system, print would be handled as a goal.
"""

import dask.bag as db

def execute(**kwargs):
    """
    Execute print operation
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': message, '1': value} where message is the label and value is the data to print
        
    Returns:
        The printed message (for chaining)
    """
    try:
        # Get arguments
        if '0' not in kwargs:
            raise ValueError("Print requires two arguments: message and value")
        if '1' not in kwargs:
            raise ValueError("Print requires two arguments: message and value")
        
        message = kwargs['0']
        value = kwargs['1']
        
        # Remove quotes from message if present
        if isinstance(message, str):
            if message.startswith('"') and message.endswith('"'):
                message = message[1:-1]
        
        # Handle Dask bags by computing their values
        if isinstance(value, db.Bag):
            # Compute the Dask bag to get actual values
            computed_values = value.compute()
            
            # If the computed values are operation IDs, try to resolve them
            resolved_values = []
            for val in computed_values:
                if isinstance(val, str) and len(val) == 64:  # SHA256 operation ID
                    try:
                        from voxlogica.execution import get_execution_engine
                        engine = get_execution_engine()
                        if engine.storage.exists(val):
                            resolved_val = engine.storage.retrieve(val)
                            resolved_values.append(resolved_val)
                        else:
                            # If operation not in storage, keep the operation ID
                            resolved_values.append(val)
                    except Exception:
                        resolved_values.append(val)
                else:
                    resolved_values.append(val)
            
            value_str = str(resolved_values)
        else:
            value_str = str(value)
        
        output = f"{message}={value_str}"
        print(output)
        return output
    except Exception as e:
        raise ValueError(f"Print failed: {e}") from e
