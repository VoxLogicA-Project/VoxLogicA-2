"""VoxLogicA experimental compatibility primitive namespace."""

from __future__ import annotations

from typing import Any, Callable

from voxlogica.primitives.api import AritySpec, ElementwiseSpec, PrimitiveSpec, default_planner_factory
from voxlogica.primitives.vox1 import kernels


# Elementwise opt-in for schedule-time fusion (engine/fusion.py). ``expr``
# placeholders match each kernel's real positional argument order — several
# of these (leq_sv, geq_sv, between) put the scalar operand(s) BEFORE the
# image, so {0} is not always "the image". UNVALIDATED until Phase 2's
# bit-identical property tests run (see ElementwiseSpec docstring).
_ELEMENTWISE: dict[str, ElementwiseSpec] = {
    "not": ElementwiseSpec(expr="~({0} != 0)", out_dtype="uint8"),
    "and": ElementwiseSpec(expr="{0} & {1}", out_dtype="uint8"),
    "or": ElementwiseSpec(expr="{0} | {1}", out_dtype="uint8"),
    # leq_sv(value, image) / geq_sv(value, image): scalar is {0}, image is {1}.
    "leq_sv": ElementwiseSpec(expr="{1} <= {0}", out_dtype="uint8"),
    "geq_sv": ElementwiseSpec(expr="{1} >= {0}", out_dtype="uint8"),
    # between(value1, value2, image): image is {2}.
    "between": ElementwiseSpec(expr="({0} <= {2}) & ({2} <= {1})", out_dtype="uint8"),
}

_PRIMITIVES: dict[str, tuple[Callable[..., Any], AritySpec]] = {
    "num_div": (kernels.num_div, AritySpec.fixed(2)),
    "num_mul": (kernels.num_mul, AritySpec.fixed(2)),
    "num_add": (kernels.num_add, AritySpec.fixed(2)),
    "num_sub": (kernels.num_sub, AritySpec.fixed(2)),
    "bool_and_scalar": (kernels.bool_and_scalar, AritySpec.fixed(2)),
    "bool_or_scalar": (kernels.bool_or_scalar, AritySpec.fixed(2)),
    "bool_not_scalar": (kernels.bool_not_scalar, AritySpec.fixed(1)),
    "not_compat": (kernels.not_compat, AritySpec.fixed(1)),
    "num_eq": (kernels.num_eq, AritySpec.fixed(2)),
    "num_neq": (kernels.num_neq, AritySpec.fixed(2)),
    "num_leq": (kernels.num_leq, AritySpec.fixed(2)),
    "num_lt": (kernels.num_lt, AritySpec.fixed(2)),
    "num_geq": (kernels.num_geq, AritySpec.fixed(2)),
    "num_gt": (kernels.num_gt, AritySpec.fixed(2)),
    "==": (kernels.equal, AritySpec.fixed(2)),
    "!=": (kernels.not_equal, AritySpec.fixed(2)),
    "<": (kernels.less, AritySpec.fixed(2)),
    "<=": (kernels.less_equal, AritySpec.fixed(2)),
    ">": (kernels.greater, AritySpec.fixed(2)),
    ">=": (kernels.greater_equal, AritySpec.fixed(2)),
    "bconstant": (kernels.bconstant, AritySpec.fixed(1)),
    "tt": (kernels.tt, AritySpec.fixed(0)),
    "ff": (kernels.ff, AritySpec.fixed(0)),
    "not": (kernels.logical_not, AritySpec.fixed(1)),
    "and": (kernels.logical_and, AritySpec.fixed(2)),
    "or": (kernels.logical_or, AritySpec.fixed(2)),
    "dt": (kernels.dt, AritySpec.fixed(1)),
    "gradient": (kernels.gradient, AritySpec.fixed(1)),
    "constant": (kernels.constant, AritySpec.fixed(1)),
    "eq_sv": (kernels.eq_sv, AritySpec.fixed(2)),
    "geq_sv": (kernels.geq_sv, AritySpec.fixed(2)),
    "leq_sv": (kernels.leq_sv, AritySpec.fixed(2)),
    "between": (kernels.between, AritySpec.fixed(3)),
    "max": (kernels.max_value, AritySpec.fixed(1)),
    "abs": (kernels.abs_value, AritySpec.fixed(1)),
    "min": (kernels.min_value, AritySpec.fixed(1)),
    "+": (kernels.add, AritySpec.fixed(2)),
    "*": (kernels.multiply, AritySpec.fixed(2)),
    "/": (kernels.divide, AritySpec.fixed(2)),
    "-": (kernels.subtract, AritySpec.fixed(2)),
    "mask": (kernels.mask, AritySpec.fixed(2)),
    "avg": (kernels.avg, AritySpec.fixed(2)),
    "avg0": (kernels.avg0, AritySpec.fixed(2)),
    "div_sv": (kernels.div_sv, AritySpec.fixed(2)),
    "sub_sv": (kernels.sub_sv, AritySpec.fixed(2)),
    "div_vs": (kernels.div_vs, AritySpec.fixed(2)),
    "sub_vs": (kernels.sub_vs, AritySpec.fixed(2)),
    "add_vs": (kernels.add_vs, AritySpec.fixed(2)),
    "mul_vs": (kernels.mul_vs, AritySpec.fixed(2)),
    "near": (kernels.near, AritySpec.fixed(1)),
    "interior": (kernels.interior, AritySpec.fixed(1)),
    "through": (kernels.through, AritySpec.fixed(2)),
    "crossCorrelation": (kernels.crossCorrelation, AritySpec.fixed(7)),
    "border": (kernels.border, AritySpec.fixed(1)),
    "x": (kernels.x, AritySpec.fixed(1)),
    "y": (kernels.y, AritySpec.fixed(1)),
    "z": (kernels.z, AritySpec.fixed(1)),
    "intensity": (kernels.intensity, AritySpec.fixed(1)),
    "red": (kernels.red, AritySpec.fixed(1)),
    "green": (kernels.green, AritySpec.fixed(1)),
    "blue": (kernels.blue, AritySpec.fixed(1)),
    "alpha": (kernels.alpha, AritySpec.fixed(1)),
    "volume": (kernels.volume, AritySpec.fixed(1)),
    "vol": (kernels.vol, AritySpec.fixed(1)),
    "extract": (kernels.extract, AritySpec.fixed(3)),
    "maxvol": (kernels.maxvol, AritySpec.fixed(1)),
    "percentiles": (kernels.percentiles, AritySpec.fixed(3)),
    "rgb": (kernels.rgb, AritySpec.fixed(3)),
    "rgba": (kernels.rgba, AritySpec.fixed(4)),
    "lcc": (kernels.lcc, AritySpec.fixed(1)),
    "Lcc": (kernels.Lcc, AritySpec.fixed(1)),
    "otsu": (kernels.otsu, AritySpec.fixed(3)),
    "n4": (kernels.n4, AritySpec.fixed(2)),
    "hd95": (kernels.hd95, AritySpec.fixed(2)),
    "nsd": (kernels.nsd, AritySpec.fixed(3)),
    "label_mean": (kernels.label_mean, AritySpec.fixed(2)),
    "slic": (kernels.slic, AritySpec.fixed(3)),
}


def get_primitives() -> dict[str, Callable[..., Any]]:
    return {name: item[0] for name, item in _PRIMITIVES.items()}


def register_specs() -> dict[str, tuple[PrimitiveSpec, Callable[..., Any]]]:
    specs: dict[str, tuple[PrimitiveSpec, Callable[..., Any]]] = {}
    for primitive_name, (kernel, arity) in _PRIMITIVES.items():
        qualified = f"vox1.{primitive_name}"
        spec = PrimitiveSpec(
            name=primitive_name,
            namespace="vox1",
            kind="scalar",
            arity=arity,
            attrs_schema={},
            planner=default_planner_factory(qualified, kind="scalar"),
            kernel_name=qualified,
            description=(kernel.__doc__ or "").strip(),
            elementwise=_ELEMENTWISE.get(primitive_name),
        )
        specs[primitive_name] = (spec, kernel)
    return specs


def register_primitives():
    return get_primitives()


def list_primitives():
    return {name: (fn.__doc__ or "").strip() for name, (fn, _) in _PRIMITIVES.items()}


def reset_runtime_state() -> None:
    kernels.reset_runtime_state()


__all__ = [
    "get_primitives",
    "register_specs",
    "register_primitives",
    "list_primitives",
    "reset_runtime_state",
]
