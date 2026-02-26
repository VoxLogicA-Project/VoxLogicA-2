"""Helpers to lift scalar binary operations over lazy sequence values."""

from __future__ import annotations

from typing import Any, Callable

import dask.bag as db

from voxlogica.execution_strategy.results import SequenceValue


ScalarBinaryOp = Callable[[Any, Any], Any]


def _is_dask_bag(value: Any) -> bool:
    return isinstance(value, db.Bag)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (SequenceValue, list, tuple, range))


def _to_sequence(value: Any) -> SequenceValue:
    if isinstance(value, SequenceValue):
        return value
    if isinstance(value, (list, tuple, range)):
        return SequenceValue(lambda: iter(value), total_size=len(value))
    raise TypeError(f"Unsupported sequence type: {type(value).__name__}")


def _pair_total_size(left: SequenceValue, right: SequenceValue) -> int | None:
    if left.total_size is None or right.total_size is None:
        return None
    if left.total_size != right.total_size:
        raise ValueError(
            f"sequence length mismatch: {left.total_size} != {right.total_size}"
        )
    return left.total_size


def _apply_sequence_op(name: str, left: Any, right: Any, op: ScalarBinaryOp) -> SequenceValue:
    if _is_sequence(left) and _is_sequence(right):
        left_seq = _to_sequence(left)
        right_seq = _to_sequence(right)
        total_size = _pair_total_size(left_seq, right_seq)

        def iterator_factory():
            sentinel = object()
            left_iter = iter(left_seq.iter_values())
            right_iter = iter(right_seq.iter_values())
            while True:
                left_value = next(left_iter, sentinel)
                right_value = next(right_iter, sentinel)
                if left_value is sentinel and right_value is sentinel:
                    return
                if left_value is sentinel or right_value is sentinel:
                    raise ValueError("sequence length mismatch during iteration")
                try:
                    yield op(left_value, right_value)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"{name} failed: {exc}") from exc

        return SequenceValue(iterator_factory, total_size=total_size)

    if _is_sequence(left):
        left_seq = _to_sequence(left)

        def left_iterator_factory():
            for value in left_seq.iter_values():
                try:
                    yield op(value, right)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"{name} failed: {exc}") from exc

        return SequenceValue(left_iterator_factory, total_size=left_seq.total_size)

    right_seq = _to_sequence(right)

    def right_iterator_factory():
        for value in right_seq.iter_values():
            try:
                yield op(left, value)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"{name} failed: {exc}") from exc

    return SequenceValue(right_iterator_factory, total_size=right_seq.total_size)


def _apply_dask_op(left: Any, right: Any, op: ScalarBinaryOp) -> db.Bag:
    if _is_dask_bag(left) and _is_dask_bag(right):
        return db.map(op, left, right)
    if _is_dask_bag(left):
        return left.map(lambda value: op(value, right))
    return right.map(lambda value: op(left, value))


def apply_binary_op(name: str, left: Any, right: Any, op: ScalarBinaryOp) -> Any:
    """Apply scalar op with lazy lifting over SequenceValue and Dask bags."""
    if _is_dask_bag(left) or _is_dask_bag(right):
        return _apply_dask_op(left, right, op)

    if _is_sequence(left) or _is_sequence(right):
        return _apply_sequence_op(name, left, right, op)

    try:
        return op(left, right)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name} failed: {exc}") from exc
