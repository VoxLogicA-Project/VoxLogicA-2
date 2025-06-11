"""
Addition primitive for VoxLogica-2

Implements addition operation for numeric types.
"""

def execute(left, right):
    """
    Execute addition operation
    
    Args:
        left: Left operand (number)
        right: Right operand (number)
        
    Returns:
        Sum of left and right
    """
    try:
        result = left + right
        return result
    except Exception as e:
        raise ValueError(f"Addition failed: {e}") from e
