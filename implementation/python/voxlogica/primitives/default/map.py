"""Map primitive fallback kernel."""

from __future__ import annotations

from typing import Any


def _apply(func: Any, value: Any) -> Any:
    if hasattr(func, "apply") and callable(func.apply):
        return func.apply(value)
    if callable(func):
        return func(value)
    raise ValueError("map expects a callable closure")


def execute(**kwargs) -> list[Any]:
    """Strict fallback: materialize mapped sequence as list."""
    if "0" not in kwargs:
        raise ValueError("map requires sequence argument at key '0'")

    sequence = kwargs["0"]
    closure = kwargs.get("1", kwargs.get("closure"))
    if closure is None:
        raise ValueError("map requires closure argument at key '1' or 'closure'")

    if hasattr(sequence, "compute") and callable(sequence.compute):
        sequence = sequence.compute()

    return [_apply(closure, item) for item in sequence]
