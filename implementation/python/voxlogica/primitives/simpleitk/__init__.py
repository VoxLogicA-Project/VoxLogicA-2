"""
SimpleITK namespace for VoxLogicA-2 primitives

Simple crude wrapper - exposes all SimpleITK functions directly through introspection.
No mapping, no aliases - just raw SimpleITK function names.
"""

import SimpleITK as sitk
import inspect
from typing import Dict, Callable, Any
import logging

logger = logging.getLogger(__name__)

# Cache for dynamically registered primitives
_dynamic_primitives_cache: Dict[str, Callable] = {}
_primitives_list_cache: Dict[str, str] = {}

def _wrap_sitk_function(func: Callable, func_name: str) -> Callable:
    """Wrap a SimpleITK function to conform to VoxLogicA primitive interface"""
    
    def execute(**kwargs):
        """Execute the wrapped SimpleITK function"""
        try:
            # Get function signature
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            
            # Map numeric string keys to positional arguments
            args = []
            for i, param_name in enumerate(params):
                key = str(i)
                if key in kwargs:
                    args.append(kwargs[key])
                else:
                    # Try to get default value
                    param = sig.parameters[param_name]
                    if param.default != inspect.Parameter.empty:
                        break  # Use default values for remaining parameters
                    else:
                        raise ValueError(f"{func_name}: missing required argument {i} ({param_name})")
            
            # Call the original function
            result = func(*args)
            return result
            
        except Exception as e:
            raise ValueError(f"{func_name} failed: {e}") from e
    
    # Copy docstring and other attributes
    execute.__doc__ = func.__doc__ or f"SimpleITK {func_name} function"
    execute.__name__ = f"sitk_{func_name}"
    
    return execute

def register_primitives():
    """Register all SimpleITK functions directly without any mapping"""
    global _dynamic_primitives_cache, _primitives_list_cache
    
    if _dynamic_primitives_cache:
        return _dynamic_primitives_cache
    
    try:
        sitk_functions = {}
        
        # Get all callable SimpleITK functions
        for name in dir(sitk):
            if not name.startswith('_'):  # Skip private attributes
                attr = getattr(sitk, name)
                if callable(attr):
                    # Wrap the function
                    wrapped_func = _wrap_sitk_function(attr, name)
                    sitk_functions[name] = wrapped_func
                    
                    # Store description for list_primitives
                    docstring = attr.__doc__ or f"SimpleITK {name} function"
                    # Extract first line or function signature from docstring
                    if '(' in docstring and ')' in docstring:
                        # Try to extract function signature
                        lines = docstring.split('\n')
                        sig_line = next((line.strip() for line in lines if '(' in line and ')' in line), lines[0] if lines else '')
                        description = sig_line.strip()
                    else:
                        description = docstring.split('\n')[0].strip()
                    
                    _primitives_list_cache[name] = description
                    
        logger.info(f"Registered {len(sitk_functions)} SimpleITK primitives dynamically")
        _dynamic_primitives_cache = sitk_functions
        return sitk_functions
        
    except Exception as e:
        logger.error(f"Failed to register SimpleITK primitives: {e}")
        return {}

def list_primitives():
    """List all primitives available in this namespace"""
    # Ensure dynamic primitives are registered
    register_primitives()
    return _primitives_list_cache.copy()
