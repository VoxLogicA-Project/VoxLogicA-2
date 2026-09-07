"""Static analyses that run over a reduced plan before it is executed.

Exports are resolved lazily: ``voxlogica.primitives.api`` refers to
``analysis.types.TypeRule`` and ``analysis.type_checker`` imports the primitive
registry, so eagerly importing the checker here would close that loop.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "TypeChecker",
    "TypeCheckResult",
    "TypeRule",
    "VoxType",
    "VoxTypeError",
    "check_plan",
    "primitive_type",
    "simple_type",
]

_EXPORTS = {
    "TypeChecker": "voxlogica.analysis.type_checker",
    "TypeCheckResult": "voxlogica.analysis.type_checker",
    "check_plan": "voxlogica.analysis.type_checker",
    "VoxTypeError": "voxlogica.analysis.type_helpers",
    "primitive_type": "voxlogica.analysis.type_helpers",
    "simple_type": "voxlogica.analysis.type_helpers",
    "TypeRule": "voxlogica.analysis.types",
    "VoxType": "voxlogica.analysis.types",
}


def __getattr__(name: str) -> Any:
    """Import an export on first use (PEP 562), keeping this package import-cheap."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)
