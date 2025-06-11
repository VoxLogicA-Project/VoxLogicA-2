"""
Tuple indexing primitive for VoxLogicA-2 default library
"""

def execute(**kwargs):
    """Return the element at position idx from the tuple_value.
    Args:
        **kwargs: expects {'0': tuple_value, '1': idx}
    """
    tuple_value = kwargs['0']
    idx = kwargs['1']
    # Auto-convert idx to int if possible (for float or string indices)
    if isinstance(idx, float):
        idx = int(idx)
    elif isinstance(idx, str):
        try:
            idx = int(idx)
        except Exception:
            raise ValueError(f"Index argument must be convertible to int, got: {idx}")
    return tuple_value[idx]
