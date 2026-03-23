"""Python-like slicing primitive for sequence values."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def _normalize_bound(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer or None, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"{name} must be an integer or None, got float: {value!r}")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{name} must be an integer or None, got string: {value!r}") from exc
    raise ValueError(f"{name} must be an integer or None, got: {type(value).__name__}")


def _is_dask_bag(value: Any) -> bool:
    try:
        import dask.bag as db  # type: ignore

        return isinstance(value, db.Bag)
    except Exception:
        return False


def _slice_sequence_value(sequence: SequenceValue, start: int | None, stop: int | None) -> SequenceValue:
    normalized_start = 0 if start is None else max(0, start)
    normalized_stop = stop
    total_size = sequence.total_size
    sliced_total_size: int | None = None
    if total_size is not None:
        effective_stop = total_size if normalized_stop is None else max(0, min(total_size, normalized_stop))
        effective_start = min(max(normalized_start, 0), max(total_size, 0))
        sliced_total_size = max(0, effective_stop - effective_start)

    def iterator_factory():
        index = 0
        for item in sequence.iter_values():
            if index < normalized_start:
                index += 1
                continue
            if normalized_stop is not None and index >= max(0, normalized_stop):
                break
            yield item
            index += 1

    return SequenceValue(iterator_factory, total_size=sliced_total_size)


def _slice_dask_bag(bag: Any, start: int | None, stop: int | None) -> SequenceValue:
    normalized_start = 0 if start is None else max(0, start)
    normalized_stop = None if stop is None else max(0, stop)

    def iterator_factory():
        index = 0
        for delayed_partition in bag.to_delayed():
            partition_items = delayed_partition.compute()
            for item in partition_items:
                if index < normalized_start:
                    index += 1
                    continue
                if normalized_stop is not None and index >= normalized_stop:
                    return
                yield item
                index += 1

    return SequenceValue(iterator_factory, total_size=None)


def execute(**kwargs):
    """Return a slice from a sequence-like value.

    Signature:
    - `slice(sequence, start_or_none, stop_or_none)`
    """
    if "0" not in kwargs:
        raise ValueError("slice requires sequence argument at key '0'")

    sequence = kwargs["0"]
    start = _normalize_bound(kwargs.get("1"), name="start")
    stop = _normalize_bound(kwargs.get("2"), name="stop")

    if stop is not None and stop <= max(0, 0 if start is None else start):
        return []

    if isinstance(sequence, SequenceValue):
        return _slice_sequence_value(sequence, start, stop)

    if _is_dask_bag(sequence):
        return _slice_dask_bag(sequence, start, stop)

    if isinstance(sequence, (list, tuple, range)):
        return list(sequence[slice(start, stop)])

    if isinstance(sequence, Iterable):
        return list(sequence)[slice(start, stop)]

    raise ValueError(f"slice expects a sequence-like value, got: {type(sequence).__name__}")


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="slice",
    namespace="default",
    kind="sequence",
    arity=AritySpec.fixed(3),
    attrs_schema={},
    planner=default_planner_factory("default.slice", kind="sequence"),
    kernel_name="default.slice",
    description="Extract a sequence slice with optional bounds",
)