from voxlogica.analysis.types import TypeRule, VoxType, VoxInt, VoxFloat, VoxBool, VoxString, VoxImage
import SimpleITK as sitk
import numpy as np

PYTHON_TO_VOX_TYPE_MAP = {
    int: VoxInt(),
    float: VoxFloat(),
    bool: VoxBool(),
    str: VoxString(),
    sitk.Image: VoxImage(),
    np.ndarray: VoxImage()
}

def primitive_type(rule: TypeRule):
    def decorator(kernel):
        kernel.type_rule = rule
        return kernel
    return decorator

class VoxTypeError(Exception):
    """Raised when a primitive is called with the wrong argument types."""

def simple_type(argsType: list[VoxType], returnType: VoxType) -> TypeRule:
    """Construct a simple type rule that checks for a single argument type."""
    def rule(actualArgsTypes: list[VoxType]) -> VoxType:
        if len(actualArgsTypes) == len(argsType) and all(
            isinstance(actual, expected) for actual, expected in zip(actualArgsTypes, argsType)
        ):
            return returnType
        else:
            raise VoxTypeError(
                f"Expected argument types {argsType}, got {actualArgsTypes}"
            )
    return rule

def infer_literal_type(value: Any) -> VoxType:
    for python_type, vox_type in PYTHON_TO_VOX_TYPE_MAP.items():
        if isinstance(value, python_type):
            return vox_type
    raise VoxTypeError(f"Unsupported literal type: {type(value)}")