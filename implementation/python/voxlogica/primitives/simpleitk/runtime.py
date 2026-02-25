"""
SimpleITK namespace for VoxLogicA-2 primitives

Simple crude wrapper - exposes all SimpleITK functions directly through introspection.
No mapping, no aliases - just raw SimpleITK function names.
"""

import SimpleITK as sitk
import inspect
from typing import Dict, Callable, Any
import logging

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory

logger = logging.getLogger(__name__)
VERBOSE_LEVEL = 15

# Cache for dynamically registered primitives
_dynamic_primitives_cache: Dict[str, Callable] = {}
_primitives_list_cache: Dict[str, str] = {}
_dynamic_specs_cache: Dict[str, tuple[PrimitiveSpec, Callable]] = {}
_source_functions_cache: Dict[str, Callable] = {}

def _wrap_sitk_function(func: Callable, func_name: str) -> Callable:
    """Wrap a SimpleITK function to conform to VoxLogicA primitive interface"""
    
    def execute(**kwargs):
        """Execute the wrapped SimpleITK function with type adaptation for argument compatibility"""
        try:
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            
            # Special handling for *args functions
            if len(params) == 1 and sig.parameters[params[0]].kind == inspect.Parameter.VAR_POSITIONAL:
                # This is a *args function, collect all numeric arguments
                args = []
                i = 0
                while str(i) in kwargs:
                    args.append(kwargs[str(i)])
                    i += 1
            else:
                # Normal function with named parameters
                args = []
                for i, param_name in enumerate(params):
                    key = str(i)
                    if key in kwargs:
                        arg = kwargs[key]
                        param = sig.parameters[param_name]
                        # Type adaptation: cast float->int if param expects int, and int->float if param expects float
                        # Fallback for known functions/params if annotation is missing
                        if param.annotation in [int] and isinstance(arg, float) and arg.is_integer():
                            arg = int(arg)
                        elif param.annotation in [float] and isinstance(arg, int):
                            arg = float(arg)
                        # Special case for BinaryThreshold: last two args must be int (insideValue, outsideValue)
                        if func_name == "BinaryThreshold" and i in (3, 4):
                            if isinstance(arg, float) and arg.is_integer():
                                arg = int(arg)
                        args.append(arg)
                    else:
                        param = sig.parameters[param_name]
                        if param.default != inspect.Parameter.empty:
                            break  # Use default values for remaining parameters
                        else:
                            raise ValueError(f"{func_name}: missing required argument {i} ({param_name})")
            # Call the original function
            result = func(*args)
            return result
            
        except Exception as e:
            # Enhanced error message for debugging
            error_msg = f"{func_name} failed: {e}"
            if func_name == "Multiply":
                error_msg += f" [Debug: received {len(kwargs)} kwargs: {list(kwargs.keys())}"
                if hasattr(e, '__class__'):
                    error_msg += f", error type: {e.__class__.__name__}"
                error_msg += "]"
            raise ValueError(error_msg) from e
    
    # Copy docstring and other attributes
    execute.__doc__ = func.__doc__ or f"SimpleITK {func_name} function"
    execute.__name__ = f"sitk_{func_name}"
    
    return execute

def _iter_sitk_functions():
    """Yield callable SimpleITK functional APIs exposed as primitives."""
    for name in dir(sitk):
        if name.startswith("_"):
            continue

        attr = getattr(sitk, name)
        if not callable(attr):
            continue

        # Skip filter class constructors when a functional counterpart exists.
        if name.endswith("Filter") or name.endswith("ImageFilter"):
            functional_name = name.replace("ImageFilter", "").replace("Filter", "")
            if hasattr(sitk, functional_name) and functional_name != name:
                logger.log(
                    VERBOSE_LEVEL,
                    f"Skipping filter class {name}, preferring functional {functional_name}",
                )
                continue

        if isinstance(attr, type):
            logger.log(VERBOSE_LEVEL, f"Skipping class constructor: {name}")
            continue

        yield name, attr


def _describe_function(name: str, func: Callable) -> str:
    docstring = func.__doc__ or f"SimpleITK {name} function"
    if "(" in docstring and ")" in docstring:
        lines = docstring.split("\n")
        sig_line = next(
            (line.strip() for line in lines if "(" in line and ")" in line),
            lines[0] if lines else "",
        )
        return sig_line.strip()
    return docstring.split("\n")[0].strip()


def _infer_arity(func: Callable) -> AritySpec:
    try:
        signature = inspect.signature(func)
    except Exception:
        return AritySpec.variadic(0)

    required = 0
    optional = 0
    has_varargs = False
    has_varkw = False

    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            has_varargs = True
            continue
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            has_varkw = True
            continue
        if parameter.default is inspect.Parameter.empty:
            required += 1
        else:
            optional += 1

    if has_varargs or has_varkw:
        return AritySpec.variadic(required)

    return AritySpec(min_args=required, max_args=required + optional)


def get_primitives() -> Dict[str, Callable]:
    """Register SimpleITK functions and return wrapped kernels."""
    global _dynamic_primitives_cache, _primitives_list_cache, _source_functions_cache

    if _dynamic_primitives_cache:
        return _dynamic_primitives_cache

    try:
        sitk_functions: Dict[str, Callable] = {}
        for name, attr in _iter_sitk_functions():
            sitk_functions[name] = _wrap_sitk_function(attr, name)
            _source_functions_cache[name] = attr
            _primitives_list_cache[name] = _describe_function(name, attr)

        logger.log(
            VERBOSE_LEVEL,
            f"Registered {len(sitk_functions)} SimpleITK primitives dynamically",
        )
        _dynamic_primitives_cache = sitk_functions
        return sitk_functions

    except Exception as e:
        logger.error(f"Failed to register SimpleITK primitives: {e}")
        return {}


def register_specs() -> Dict[str, tuple[PrimitiveSpec, Callable]]:
    """Register SimpleITK primitives with stable PrimitiveSpec contracts."""
    global _dynamic_specs_cache
    if _dynamic_specs_cache:
        return _dynamic_specs_cache

    specs: Dict[str, tuple[PrimitiveSpec, Callable]] = {}
    for name, kernel in get_primitives().items():
        qualified = f"simpleitk.{name}"
        source_func = _source_functions_cache.get(name, kernel)
        spec = PrimitiveSpec(
            name=name,
            namespace="simpleitk",
            kind="scalar",
            arity=_infer_arity(source_func),
            attrs_schema={},
            planner=default_planner_factory(qualified, kind="scalar"),
            kernel_name=qualified,
            description=_primitives_list_cache.get(name, f"SimpleITK {name}"),
        )
        specs[name] = (spec, kernel)

    _dynamic_specs_cache = specs
    return specs


def register_primitives():
    """Legacy compatibility shim."""
    return get_primitives()

def list_primitives():
    """List all primitives available in this namespace"""
    # Ensure dynamic primitives are registered
    get_primitives()
    return _primitives_list_cache.copy()

def get_serializers():
    """Return serializers provided by SimpleITK primitives"""
    from typing import Dict, Type, Callable, Any
    from pathlib import Path
    
    def write_image_wrapper(image, filepath: Path) -> None:
        """Wrapper for SimpleITK WriteImage with error handling"""
        try:
            sitk.WriteImage(image, str(filepath))
        except Exception as e:
            raise RuntimeError(f"Failed to write image to {filepath}: {e}")
    
    def write_transform_wrapper(transform, filepath: Path) -> None:
        """Wrapper for SimpleITK WriteTransform with error handling"""
        try:
            sitk.WriteTransform(transform, str(filepath))
        except Exception as e:
            raise RuntimeError(f"Failed to write transform to {filepath}: {e}")
    
    def write_png_slice(image, filepath: Path) -> None:
        """Convert 3D image to 2D slice and save as PNG"""
        try:
            # Extract middle slice if 3D
            if image.GetDimension() == 3:
                size = image.GetSize()
                middle_slice = size[2] // 2
                slice_image = image[:, :, middle_slice]
            else:
                slice_image = image
            
            sitk.WriteImage(slice_image, str(filepath))
        except Exception as e:
            raise RuntimeError(f"Failed to write PNG slice to {filepath}: {e}")
    
    # Get SimpleITK Image type
    sitk_image_type = sitk.Image
    
    # For transforms, we need to handle the fact that SimpleITK transforms 
    # might be different classes, so we'll use a more general approach
    def is_sitk_image(obj):
        """Check if object is a SimpleITK Image"""
        return hasattr(obj, 'GetSize') and hasattr(obj, 'GetSpacing') and hasattr(obj, 'GetOrigin')
    
    def is_sitk_transform(obj):
        """Check if object is a SimpleITK Transform"""
        return hasattr(obj, 'GetParameters') and hasattr(obj, 'GetFixedParameters')
    
    def universal_image_writer(obj, filepath: Path) -> None:
        """Write SimpleITK image using duck typing"""
        if is_sitk_image(obj):
            write_image_wrapper(obj, filepath)
        else:
            raise TypeError(f"Object is not a SimpleITK Image: {type(obj)}")
    
    def universal_transform_writer(obj, filepath: Path) -> None:
        """Write SimpleITK transform using duck typing"""
        if is_sitk_transform(obj):
            write_transform_wrapper(obj, filepath)
        else:
            raise TypeError(f"Object is not a SimpleITK Transform: {type(obj)}")
    
    return {
        # Medical imaging formats (compound extensions first)
        '.nii.gz': {sitk_image_type: universal_image_writer},
        '.nii': {sitk_image_type: universal_image_writer},
        '.mha': {sitk_image_type: universal_image_writer},
        '.mhd': {sitk_image_type: universal_image_writer},
        '.nrrd': {sitk_image_type: universal_image_writer},
        '.vtk': {sitk_image_type: universal_image_writer},
        
        # DICOM formats
        '.dcm': {sitk_image_type: universal_image_writer},
        '.dicom': {sitk_image_type: universal_image_writer},
        
        # Standard image formats (with 2D conversion)
        '.png': {sitk_image_type: write_png_slice},
        '.jpg': {sitk_image_type: write_png_slice},
        '.jpeg': {sitk_image_type: write_png_slice},
        '.tiff': {sitk_image_type: write_png_slice},
        '.tif': {sitk_image_type: write_png_slice},
        '.bmp': {sitk_image_type: write_png_slice},
    }
