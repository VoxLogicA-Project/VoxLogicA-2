"""
Timewaste primitive for VoxLogicA-2

Computes the nth Fibonacci number using inefficient recursive algorithm.
This is intentionally slow and is used for testing purposes where you want
to avoid memoization by providing a dummy second argument.
"""

def execute(**kwargs):
    """
    Execute timewaste computation (inefficient recursive fibonacci)
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': n, '1': dummy} where n is the fibonacci position
                          and dummy is any value to avoid memoization
        
    Returns:
        The nth Fibonacci number (computed inefficiently)
        
    Raises:
        ValueError: If arguments are invalid or missing
    """
    try:
        # Get the first argument (the position n)
        if '0' not in kwargs:
            raise ValueError("Timewaste requires two arguments: position n and dummy value")
        if '1' not in kwargs:
            raise ValueError("Timewaste requires two arguments: position n and dummy value")
        
        n = kwargs['0']
        dummy = kwargs['1']  # Second arg is ignored but required to avoid memoization
        
        # Convert to int if possible
        if isinstance(n, float) and n.is_integer():
            n = int(n)
        
        if not isinstance(n, int):
            raise ValueError("Timewaste input must be an integer")
        if n < 0:
            raise ValueError("Timewaste input must be non-negative")
        
        # Inefficient recursive computation (the whole point!)
        def recursive_fib(x):
            if x <= 1:
                return x
            return recursive_fib(x - 1) + recursive_fib(x - 2)
        
        result = recursive_fib(n)
        return result
        
    except Exception as e:
        raise ValueError(f"Timewaste computation failed: {e}") from e
