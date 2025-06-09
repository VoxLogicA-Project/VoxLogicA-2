"""
Multiplication primitive for VoxLogica-2

Implements multiplication operation for numeric types.
"""

def execute(left, right):
    """
    Execute multiplication operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Product of left and right
    """
    try:
        result = left * right
        return result
    except Exception as e:
        raise ValueError(f"Multiplication failed: {e}") from e
