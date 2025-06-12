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

def get_serializers():
    """Return serializers provided by dataset primitives"""
    import dask.bag as db
    import json
    from typing import Dict, Type, Callable, Any
    from pathlib import Path
    
    def write_dask_bag_json(bag, filepath: Path) -> None:
        """Serialize Dask bag to JSON as a simple array of values"""
        try:
            # Compute the bag to get concrete values
            computed_values = bag.compute()
            
            # Convert complex objects to simple JSON-serializable format
            json_values = []
            for value in computed_values:
                try:
                    # Try to convert to JSON-safe format
                    if hasattr(value, '__dict__'):
                        # For objects with attributes, store as dict
                        json_values.append(str(value))
                    elif isinstance(value, (list, tuple)):
                        json_values.append([str(item) for item in value])
                    elif isinstance(value, dict):
                        json_values.append({str(k): str(v) for k, v in value.items()})
                    else:
                        # For simple types, use as-is or convert to string
                        json_values.append(value if isinstance(value, (int, float, str, bool, type(None))) else str(value))
                except Exception as e:
                    # Fallback to string representation for problematic objects
                    json_values.append(f"<object: {type(value).__name__}({str(value)[:100]})>")
            
            # Write directly as JSON array (simpler format)
            with open(filepath, 'w') as f:
                json.dump(json_values, f, indent=2, default=str)
                
        except Exception as e:
            raise RuntimeError(f"Failed to write Dask bag to JSON {filepath}: {e}")
    
    def is_dask_bag(obj):
        """Check if object is a Dask bag"""
        return hasattr(obj, 'compute') and hasattr(obj, 'npartitions') and hasattr(obj, 'map')
    
    def universal_bag_writer(obj, filepath: Path) -> None:
        """Write Dask bag using duck typing"""
        if is_dask_bag(obj):
            write_dask_bag_json(obj, filepath)
        else:
            raise TypeError(f"Object is not a Dask bag: {type(obj)}")
    
    # Get Dask bag type
    dask_bag_type = db.Bag
    
    return {
        # JSON format for Dask bags
        '.json': {dask_bag_type: universal_bag_writer},
    }
