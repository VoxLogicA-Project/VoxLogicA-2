"""Shared sequence materialization for the descriptive-statistics primitives.

These primitives summarise a sequence that a `for` produced -- per-case scores,
per-case chosen parameters -- so they all need the same thing first: the
sequence as a plain list, whatever container it arrived in.
"""

from __future__ import annotations

from typing import Any

from voxlogica.execution_strategy.results import SequenceValue


def materialize(value: Any, *, name: str) -> list[Any]:
    """Normalize any supported sequence container to a plain list."""
    if hasattr(value, "compute") and callable(value.compute):
        value = value.compute()
    if isinstance(value, SequenceValue):
        return list(value.iter_values())
    if hasattr(value, "iter_values") and callable(value.iter_values):
        return list(value.iter_values())
    if isinstance(value, range):
        return list(value)
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError(f"{name} requires a sequence argument")


def numbers(value: Any, *, name: str) -> list[float]:
    """Materialize and require every element to be a number."""
    items = materialize(value, name=name)
    if not items:
        raise ValueError(f"{name} requires a non-empty sequence")
    try:
        return [float(item) for item in items]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} requires a sequence of numbers") from exc
