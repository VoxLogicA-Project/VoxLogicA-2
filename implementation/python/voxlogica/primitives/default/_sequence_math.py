"""Helpers for primitives that can operate on scalars or sequences.

Arithmetic primitives share the same broadcasting-like rule: if either operand
is sequence-like, both inputs are aligned as lists and the operation is applied
element by element.
"""

from __future__ import annotations

from typing import Any, Callable

from voxlogica.execution_strategy.results import SequenceValue


def _materialize_sequence(value: Any) -> list[Any]:
    """Normalize the supported sequence container types to plain lists."""
    if isinstance(value, SequenceValue):
        return list(value.iter_values())
    if isinstance(value, range):
        return list(value)
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def apply_binary_op(name: str, left: Any, right: Any, op: Callable[[Any, Any], Any]) -> Any:
    """Apply a scalar binary operator with simple sequence broadcasting."""
    left_seq = _materialize_sequence(left)
    right_seq = _materialize_sequence(right)

    if left_seq or right_seq:
        if not left_seq:
            left_seq = [left] * len(right_seq)
        if not right_seq:
            right_seq = [right] * len(left_seq)
        if len(left_seq) != len(right_seq):
            raise ValueError(f"{name} requires sequences of the same length")
        return [op(left_value, right_value) for left_value, right_value in zip(left_seq, right_seq, strict=True)]

    return op(left, right)
