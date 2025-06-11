"""
Division primitive for VoxLogica-2

Implements division operation for numeric types.
"""

def execute(left, right):
    """
    Execute division operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Quotient of left and right
    """
    try:
        if right == 0:
            raise ValueError("Division by zero")
        result = left / right
        return result
    except Exception as e:
        raise ValueError(f"Division failed: {e}") from e
