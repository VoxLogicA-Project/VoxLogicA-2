"""
Impure primitive that prints its argument every time it's invoked.
This is useful for testing memoization - if memoization works correctly,
this primitive should only be called once for identical subexpressions.
"""


def execute(**kwargs):
    """
    Execute impure operation - prints the input and returns it unchanged.
    
    Args:
        **kwargs: Arguments passed as keyword arguments with numeric string keys
                 Expected: {'0': input_value} where input_value is any value
        
    Returns:
        The input value unchanged
    """
    if '0' not in kwargs:
        raise ValueError("impure primitive expects exactly one argument")
    
    input_value = kwargs['0']
    import logging
    import os
    logger = logging.getLogger("voxlogica.primitives.test.impure")
    logger.info(f"IMPURE CALLED WITH: {input_value}")
    print(f"IMPURE DEBUG: file={__file__} version=return3 input={input_value}")
    return input_value
