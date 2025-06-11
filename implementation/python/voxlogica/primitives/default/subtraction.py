"""
Subtraction primitive for VoxLogica-2

Implements subtraction operation for numeric types.
"""

def execute(left, right):
    """
    Execute subtraction operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Difference of left and right
    """
    try:
        result = left - right
        return result
    except Exception as e:
        raise ValueError(f"Subtraction failed: {e}") from e
