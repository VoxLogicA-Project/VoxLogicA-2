"""
Default namespace for VoxLogicA-2 primitives

Contains basic arithmetic and logic primitives that are available
as unqualified operators for backward compatibility.
"""

def register_primitives():
    """Register static primitives in this namespace"""
    # Static primitives are loaded by file-based discovery
    # This function is for consistency but not used for static namespaces
    return {}

def list_primitives():
    """List all primitives available in this namespace"""
    return {
        'addition': 'Add two numbers together',
        'subtraction': 'Subtract the second number from the first',
        'multiplication': 'Multiply two numbers together',
        'division': 'Divide the first number by the second',
        'print_primitive': 'Print a message and value',
        'index': 'Return the element at position idx from the tuple_value',
    }
