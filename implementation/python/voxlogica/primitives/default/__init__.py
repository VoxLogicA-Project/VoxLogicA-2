"""
Default namespace for VoxLogicA-2 primitives

Contains basic arithmetic and logic primitives that are available
as unqualified operators for backward compatibility.
"""

import importlib
from pathlib import Path

def register_primitives():
    """Register static primitives in this namespace"""
    # Static primitives are loaded by file-based discovery
    # This function is for consistency but not used for static namespaces
    return {}

def list_primitives():
    """List all primitives available in this namespace"""
    primitives = {}
    
    # Get the directory containing this __init__.py file
    namespace_dir = Path(__file__).parent
    
    # Scan for .py files (excluding __init__.py and __pycache__)
    for item in namespace_dir.iterdir():
        if (item.is_file() and 
            item.suffix == '.py' and 
            not item.name.startswith('_')):
            
            module_name = item.stem
            try:
                # Import the module to get its docstring
                module_path = f"voxlogica.primitives.default.{module_name}"
                module = importlib.import_module(module_path)
                
                # Extract description from module docstring or execute function docstring
                description = "No description available"
                if hasattr(module, '__doc__') and module.__doc__:
                    # Get first line of module docstring
                    description = module.__doc__.strip().split('\n')[0]
                elif hasattr(module, 'execute') and module.execute.__doc__:
                    # Get first line of execute function docstring
                    description = module.execute.__doc__.strip().split('\n')[0]
                
                primitives[module_name] = description
                
            except Exception as e:
                # If import fails, still list the primitive with a generic description
                primitives[module_name] = f"Primitive from {module_name}.py"
    
    return primitives
