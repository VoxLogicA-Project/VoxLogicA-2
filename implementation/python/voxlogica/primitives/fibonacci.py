"""
Fibonacci primitive for VoxLogicA-2

Computes the nth Fibonacci number using an iterative algorithm.
"""

def execute(**kwargs):
    """
    Execute fibonacci computation
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': n} where n is the position in fibonacci sequence
        
    Returns:
        The nth Fibonacci number
        
    Raises:
        ValueError: If arguments are invalid or missing
    """
    try:
        # Get the first argument (the position n)
        if '0' not in kwargs:
            raise ValueError("Fibonacci requires one argument: the position n")
        
        n = kwargs['0']
        
        # Convert to int if possible
        if isinstance(n, float) and n.is_integer():
            n = int(n)
        
        if not isinstance(n, int):
            raise ValueError("Fibonacci input must be an integer")
        if n < 0:
            raise ValueError("Fibonacci input must be non-negative")
        
        if n <= 1:
            return n
        
        # Iterative computation for efficiency
        a, b = 0, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        
        return b
    except Exception as e:
        raise ValueError(f"Fibonacci computation failed: {e}") from e
