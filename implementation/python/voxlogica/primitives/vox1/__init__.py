"""VoxLogicA experimental compatibility primitive namespace."""

from __future__ import annotations

from typing import Any, Callable

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.vox1 import kernels


_PRIMITIVES: dict[str, tuple[Callable[..., Any], AritySpec, str]] = {
    "num_div": (kernels.num_div, AritySpec.fixed(2), "Scalar floating-point division"),
    "num_mul": (kernels.num_mul, AritySpec.fixed(2), "Scalar floating-point multiplication"),
    "num_add": (kernels.num_add, AritySpec.fixed(2), "Scalar floating-point addition"),
    "num_sub": (kernels.num_sub, AritySpec.fixed(2), "Scalar floating-point subtraction"),
    "bool_and_scalar": (kernels.bool_and_scalar, AritySpec.fixed(2), "Scalar boolean and"),
    "bool_or_scalar": (kernels.bool_or_scalar, AritySpec.fixed(2), "Scalar boolean or"),
    "bool_not_scalar": (kernels.bool_not_scalar, AritySpec.fixed(1), "Scalar boolean not"),
    "num_eq": (kernels.num_eq, AritySpec.fixed(2), "Scalar floating-point equality"),
    "num_leq": (kernels.num_leq, AritySpec.fixed(2), "Scalar floating-point less-or-equal"),
    "num_lt": (kernels.num_lt, AritySpec.fixed(2), "Scalar floating-point less-than"),
    "num_geq": (kernels.num_geq, AritySpec.fixed(2), "Scalar floating-point greater-or-equal"),
    "num_gt": (kernels.num_gt, AritySpec.fixed(2), "Scalar floating-point greater-than"),
    "bconstant": (kernels.bconstant, AritySpec.fixed(1), "Boolean constant image"),
    "tt": (kernels.tt, AritySpec.fixed(0), "Boolean true image"),
    "ff": (kernels.ff, AritySpec.fixed(0), "Boolean false image"),
    "not": (kernels.logical_not, AritySpec.fixed(1), "Voxel-wise boolean negation"),
    "and": (kernels.logical_and, AritySpec.fixed(2), "Voxel-wise boolean and"),
    "or": (kernels.logical_or, AritySpec.fixed(2), "Voxel-wise boolean or"),
    "dt": (kernels.dt, AritySpec.fixed(1), "Signed Maurer distance transform"),
    "constant": (kernels.constant, AritySpec.fixed(1), "Numeric constant image"),
    "eq_sv": (kernels.eq_sv, AritySpec.fixed(2), "Scalar equals voxel value mask"),
    "geq_sv": (kernels.geq_sv, AritySpec.fixed(2), "Scalar less-or-equal comparison mask"),
    "leq_sv": (kernels.leq_sv, AritySpec.fixed(2), "Scalar greater-or-equal comparison mask"),
    "between": (kernels.between, AritySpec.fixed(3), "Inclusive scalar interval mask"),
    "max": (kernels.max_value, AritySpec.fixed(1), "Maximum voxel value"),
    "abs": (kernels.abs_value, AritySpec.fixed(1), "Voxel-wise absolute value"),
    "min": (kernels.min_value, AritySpec.fixed(1), "Minimum voxel value"),
    "+": (kernels.add, AritySpec.fixed(2), "Voxel-wise addition"),
    "*": (kernels.multiply, AritySpec.fixed(2), "Voxel-wise multiplication"),
    "/": (kernels.divide, AritySpec.fixed(2), "Voxel-wise division"),
    "-": (kernels.subtract, AritySpec.fixed(2), "Voxel-wise subtraction"),
    "mask": (kernels.mask, AritySpec.fixed(2), "Mask a numeric image with a boolean image"),
    "avg": (kernels.avg, AritySpec.fixed(2), "Average value in a boolean mask"),
    "div_sv": (kernels.div_sv, AritySpec.fixed(2), "Scalar divided by each voxel"),
    "sub_sv": (kernels.sub_sv, AritySpec.fixed(2), "Scalar minus each voxel"),
    "div_vs": (kernels.div_vs, AritySpec.fixed(2), "Each voxel divided by scalar"),
    "sub_vs": (kernels.sub_vs, AritySpec.fixed(2), "Each voxel minus scalar"),
    "add_vs": (kernels.add_vs, AritySpec.fixed(2), "Each voxel plus scalar"),
    "mul_vs": (kernels.mul_vs, AritySpec.fixed(2), "Each voxel multiplied by scalar"),
    "near": (kernels.near, AritySpec.fixed(1), "Spatial closure (dilation)"),
    "interior": (kernels.interior, AritySpec.fixed(1), "Spatial interior (erosion)"),
    "through": (kernels.through, AritySpec.fixed(2), "Component-restricted reachability"),
    "crossCorrelation": (
        kernels.crossCorrelation,
        AritySpec.fixed(7),
        "Statistical cross-correlation comparison",
    ),
    "border": (kernels.border, AritySpec.fixed(0), "True on image border voxels"),
    "x": (kernels.x, AritySpec.fixed(0), "x-coordinate image"),
    "y": (kernels.y, AritySpec.fixed(0), "y-coordinate image"),
    "z": (kernels.z, AritySpec.fixed(0), "z-coordinate image"),
    "intensity": (kernels.intensity, AritySpec.fixed(1), "Image intensity channel"),
    "red": (kernels.red, AritySpec.fixed(1), "Red channel"),
    "green": (kernels.green, AritySpec.fixed(1), "Green channel"),
    "blue": (kernels.blue, AritySpec.fixed(1), "Blue channel"),
    "alpha": (kernels.alpha, AritySpec.fixed(1), "Alpha channel"),
    "volume": (kernels.volume, AritySpec.fixed(1), "Number of true voxels"),
    "vol": (kernels.vol, AritySpec.fixed(1), "Alias of volume"),
    "maxvol": (
        kernels.maxvol,
        AritySpec.fixed(1),
        "Largest connected component mask (ties keep union)",
    ),
    "percentiles": (
        kernels.percentiles,
        AritySpec.fixed(3),
        "Percentile rank image constrained by a mask",
    ),
    "rgb": (kernels.rgb, AritySpec.fixed(3), "Compose RGB image"),
    "rgba": (kernels.rgba, AritySpec.fixed(4), "Compose RGBA image"),
    "lcc": (kernels.lcc, AritySpec.fixed(1), "Connected component labels (float32)"),
    "Lcc": (kernels.Lcc, AritySpec.fixed(1), "Alias of lcc"),
    "otsu": (kernels.otsu, AritySpec.fixed(3), "Otsu threshold with mask"),
}


def get_primitives() -> dict[str, Callable[..., Any]]:
    return {name: item[0] for name, item in _PRIMITIVES.items()}


def register_specs() -> dict[str, tuple[PrimitiveSpec, Callable[..., Any]]]:
    specs: dict[str, tuple[PrimitiveSpec, Callable[..., Any]]] = {}
    for primitive_name, (kernel, arity, description) in _PRIMITIVES.items():
        qualified = f"vox1.{primitive_name}"
        spec = PrimitiveSpec(
            name=primitive_name,
            namespace="vox1",
            kind="scalar",
            arity=arity,
            attrs_schema={},
            planner=default_planner_factory(qualified, kind="scalar"),
            kernel_name=qualified,
            description=description,
        )
        specs[primitive_name] = (spec, kernel)
    return specs


def register_primitives():
    return get_primitives()


def list_primitives():
    return {name: item[2] for name, item in _PRIMITIVES.items()}


def reset_runtime_state() -> None:
    kernels.reset_runtime_state()


__all__ = [
    "get_primitives",
    "register_specs",
    "register_primitives",
    "list_primitives",
    "reset_runtime_state",
]
