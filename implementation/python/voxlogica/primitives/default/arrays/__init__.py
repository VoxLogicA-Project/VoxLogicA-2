"""Arrays primitive namespace registry."""

from __future__ import annotations

from typing import Any, Callable, Dict

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.arrays.kernels import (
    array_stats,
    compare_arrays,
    confusion_matrix,
    count_pixels,
    dice_score,
    jaccard_index,
    pixel_accuracy,
    threshold_equal,
    vector_double,
    vector_uint32,
)


_PRIMITIVE_DESCRIPTIONS = {
    "pixel_accuracy": "Calculate pixel-wise accuracy between predicted and ground truth images",
    "confusion_matrix": "Compute confusion matrix between predicted and ground truth images",
    "dice_score": "Calculate Dice similarity coefficient",
    "jaccard_index": "Calculate Jaccard index (IoU)",
    "vector_uint32": "Create a list of uint32-compatible values",
    "vector_double": "Create a list of double-compatible values",
    "count_pixels": "Count pixels with specific values in an image",
    "threshold_equal": "Create binary mask where values equal threshold",
    "array_stats": "Compute basic statistics of an image array",
    "compare_arrays": "Compare two arrays element-wise",
}

_PRIMITIVE_ARITIES = {
    "pixel_accuracy": AritySpec.fixed(2),
    "confusion_matrix": AritySpec(min_args=2, max_args=3),
    "dice_score": AritySpec(min_args=2, max_args=3),
    "jaccard_index": AritySpec(min_args=2, max_args=3),
    "vector_uint32": AritySpec.fixed(1),
    "vector_double": AritySpec.fixed(1),
    "count_pixels": AritySpec(min_args=1, max_args=2),
    "threshold_equal": AritySpec(min_args=2, max_args=4),
    "array_stats": AritySpec.fixed(1),
    "compare_arrays": AritySpec.fixed(2),
}


def get_primitives() -> Dict[str, Callable[..., Any]]:
    """Return array primitive kernels."""
    return {
        "pixel_accuracy": pixel_accuracy,
        "confusion_matrix": confusion_matrix,
        "dice_score": dice_score,
        "jaccard_index": jaccard_index,
        "vector_uint32": vector_uint32,
        "vector_double": vector_double,
        "count_pixels": count_pixels,
        "threshold_equal": threshold_equal,
        "array_stats": array_stats,
        "compare_arrays": compare_arrays,
    }


def register_specs() -> Dict[str, tuple[PrimitiveSpec, Callable[..., Any]]]:
    """Register array primitives with stable PrimitiveSpec contracts."""
    specs: Dict[str, tuple[PrimitiveSpec, Callable[..., Any]]] = {}
    for primitive_name, kernel in get_primitives().items():
        qualified = f"arrays.{primitive_name}"
        spec = PrimitiveSpec(
            name=primitive_name,
            namespace="arrays",
            kind="scalar",
            arity=_PRIMITIVE_ARITIES.get(primitive_name, AritySpec.variadic(0)),
            attrs_schema={},
            planner=default_planner_factory(qualified, kind="scalar"),
            kernel_name=qualified,
            description=_PRIMITIVE_DESCRIPTIONS.get(primitive_name, "Array primitive"),
        )
        specs[primitive_name] = (spec, kernel)
    return specs


def register_primitives():
    """Legacy compatibility shim."""
    return get_primitives()


def list_primitives():
    """List all array primitives."""
    return dict(_PRIMITIVE_DESCRIPTIONS)


__all__ = [
    "get_primitives",
    "register_specs",
    "register_primitives",
    "list_primitives",
]
