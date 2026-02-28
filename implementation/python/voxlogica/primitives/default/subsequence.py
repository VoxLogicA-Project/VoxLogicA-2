"""Slice-like subsequence primitive for sequence values."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _as_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"{name} must be an integer, got float: {value!r}")
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{name} must be an integer, got string: {value!r}") from exc
    raise ValueError(f"{name} must be an integer, got: {type(value).__name__}")


def _is_dask_bag(value: Any) -> bool:
    try:
        import dask.bag as db  # type: ignore

        return isinstance(value, db.Bag)
    except Exception:
        return False


def _slice_sequence_value(
    sequence: SequenceValue,
    *,
    start: int,
    stop: int,
) -> SequenceValue:
    total_size = sequence.total_size
    sliced_total_size: int | None = None
    if total_size is not None:
        clamped_start = min(start, max(total_size, 0))
        clamped_stop = min(stop, max(total_size, 0))
        sliced_total_size = max(0, clamped_stop - clamped_start)

    def iterator_factory():
        index = 0
        for item in sequence.iter_values():
            if index < start:
                index += 1
                continue
            if index >= stop:
                break
            yield item
            index += 1

    return SequenceValue(iterator_factory, total_size=sliced_total_size)


def _slice_dask_bag(
    bag: Any,
    *,
    start: int,
    stop: int,
) -> SequenceValue:
    def iterator_factory():
        index = 0
        for delayed_partition in bag.to_delayed():
            partition_items = delayed_partition.compute()
            for item in partition_items:
                if index < start:
                    index += 1
                    continue
                if index >= stop:
                    return
                yield item
                index += 1

    return SequenceValue(iterator_factory, total_size=None)


def execute(**kwargs):
    """Return a subsequence by position.

    Supported signatures:
    - `subsequence(sequence, stop)`
    - `subsequence(sequence, start, stop)`
    """
    if "0" not in kwargs:
        raise ValueError("subsequence requires sequence argument at key '0'")
    if "1" not in kwargs:
        raise ValueError("subsequence requires at least one bound argument")

    sequence = kwargs["0"]
    if "2" in kwargs:
        start = _as_int(kwargs["1"], name="start")
        stop = _as_int(kwargs["2"], name="stop")
    else:
        start = 0
        stop = _as_int(kwargs["1"], name="stop")

    start = max(0, start)
    stop = max(0, stop)
    if stop <= start:
        return []

    if isinstance(sequence, SequenceValue):
        return _slice_sequence_value(sequence, start=start, stop=stop)

    if _is_dask_bag(sequence):
        return _slice_dask_bag(sequence, start=start, stop=stop)

    if isinstance(sequence, (list, tuple, range)):
        return list(sequence[start:stop])

    if isinstance(sequence, Iterable):
        return list(sequence)[start:stop]

    raise ValueError(f"subsequence expects a sequence-like value, got: {type(sequence).__name__}")


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="subsequence",
    namespace="default",
    kind="sequence",
    arity=AritySpec(min_args=2, max_args=3),
    attrs_schema={},
    planner=default_planner_factory("default.subsequence", kind="sequence"),
    kernel_name="default.subsequence",
    description="Extract a sequence slice by index range",
)
