"""Default primitive namespace for the DAG runtime.

Most built-ins live one per file. A small set of reducer-facing operator
primitives is registered centrally here because they are used to implement
infix and prefix syntax lowering.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _bool_and_scalar(left: bool, right: bool) -> bool:
    return bool(left) and bool(right)


def _bool_or_scalar(left: bool, right: bool) -> bool:
    return bool(left) or bool(right)


def _not_compat(value: object) -> bool:
    return not bool(value)


def _num_eq(left: float, right: float) -> bool:
    return float(left) == float(right)


def _num_neq(left: float, right: float) -> bool:
    return float(left) != float(right)


def _num_leq(left: float, right: float) -> bool:
    return float(left) <= float(right)


def _num_lt(left: float, right: float) -> bool:
    return float(left) < float(right)


def _num_geq(left: float, right: float) -> bool:
    return float(left) >= float(right)


def _num_gt(left: float, right: float) -> bool:
    return float(left) > float(right)


def register_specs():
    """Register operator-style scalar primitives used by reducer lowering."""

    primitives = {
        "bool_and_scalar": (_bool_and_scalar, AritySpec.fixed(2), "Scalar boolean and"),
        "bool_or_scalar": (_bool_or_scalar, AritySpec.fixed(2), "Scalar boolean or"),
        "not_compat": (_not_compat, AritySpec.fixed(1), "Scalar boolean not"),
        "num_eq": (_num_eq, AritySpec.fixed(2), "Scalar numeric equality"),
        "num_neq": (_num_neq, AritySpec.fixed(2), "Scalar numeric inequality"),
        "num_leq": (_num_leq, AritySpec.fixed(2), "Scalar numeric less-or-equal"),
        "num_lt": (_num_lt, AritySpec.fixed(2), "Scalar numeric less-than"),
        "num_geq": (_num_geq, AritySpec.fixed(2), "Scalar numeric greater-or-equal"),
        "num_gt": (_num_gt, AritySpec.fixed(2), "Scalar numeric greater-than"),
    }

    specs = {}
    for name, (kernel, arity, description) in primitives.items():
        spec = PrimitiveSpec(
            name=name,
            namespace="default",
            kind="scalar",
            arity=arity,
            attrs_schema={},
            planner=default_planner_factory(f"default.{name}", kind="scalar"),
            kernel_name=f"default.{name}",
            description=description,
        )
        specs[name] = (spec, kernel)
    return specs


def register_primitives():
    """Legacy compatibility shim."""
    return {}


def list_primitives():
    """List all primitives available in this namespace for CLI inspection."""
    primitives = {}
    namespace_dir = Path(__file__).parent
    for item in namespace_dir.iterdir():
        if item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
            module_name = item.stem
            try:
                module_path = f"voxlogica.primitives.default.{module_name}"
                module = importlib.import_module(module_path)
                description = "No description available"
                if getattr(module, "__doc__", None):
                    description = module.__doc__.strip().split("\n")[0]
                elif hasattr(module, "execute") and getattr(module.execute, "__doc__", None):
                    description = module.execute.__doc__.strip().split("\n")[0]
                primitives[module_name] = description
            except Exception:
                primitives[module_name] = f"Primitive from {module_name}.py"

    for name, (spec, _kernel) in register_specs().items():
        primitives[name] = spec.description or "Primitive"
    return primitives
