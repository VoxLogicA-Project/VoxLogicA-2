"""
Test namespace for VoxLogicA-2 primitives

Contains non-basic primitives moved from the original primitives directory.
"""

def register_primitives():
    """Register static primitives in this namespace"""
    # Static primitives are loaded by file-based discovery  
    return {}

def list_primitives():
    """List all primitives available in this namespace"""
    return {
        'fibonacci': 'Compute the nth Fibonacci number',
        'timewaste': 'Perform expensive computations for testing',
    }
