"""Dataset processing primitives with dynamic VoxLogicA compilation"""

def register_primitives():
    """Static primitive discovery - readdir.py and map.py auto-discovered"""
    return {}

def list_primitives():
    """List all primitives available in this namespace"""
    #TODO: here and everywhere, this should be a dynamic primitive
    return {        
        'readdir': 'Load directory contents as Dask bag of file paths',
        'map': 'Apply VoxLogicA function to each dataset element with dynamic compilation'
    }
