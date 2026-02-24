"""Dask-first execution strategy over the same SymbolicPlan contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json

import dask.bag as db

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.execution_strategy.strict import StrictExecutionStrategy


class DaskExecutionStrategy(StrictExecutionStrategy):
    """Execution strategy that keeps sequence operations in Dask bags."""

    name = "dask"

    def _evaluate_range(self, args: list[Any], kwargs: dict[str, Any]) -> db.Bag:
        if not args:
            raise ValueError("range requires at least one argument")

        if len(args) == 1:
            start = 0
            stop = int(args[0])
        else:
            start = int(args[0])
            stop = int(args[1])

        values = list(range(start, stop))
        npartitions = max(1, min(32, len(values) or 1))
        return db.from_sequence(values, npartitions=npartitions)

    def _evaluate_load(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if not args:
            raise ValueError("load requires one dataset argument")

        source = args[0]
        if isinstance(source, db.Bag):
            return source

        if isinstance(source, (list, tuple, range)):
            npartitions = max(1, min(32, len(source) or 1))
            return db.from_sequence(list(source), npartitions=npartitions)

        path = Path(str(source))
        if not path.exists():
            raise ValueError(f"load source not found: {path}")

        suffix = path.suffix.lower()
        if suffix in {".txt", ".csv"}:
            return db.read_text(str(path)).map(lambda line: line.rstrip("\n"))

        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                npartitions = max(1, min(32, len(payload) or 1))
                return db.from_sequence(payload, npartitions=npartitions)
            return payload

        return path.read_bytes()

    def _evaluate_map(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if not args:
            raise ValueError("map/for_loop requires sequence argument")

        sequence = args[0]
        closure = kwargs.get("closure")
        if closure is None and len(args) > 1:
            closure = args[1]
        if closure is None:
            raise ValueError("map/for_loop requires closure argument")

        if isinstance(sequence, db.Bag):
            if hasattr(closure, "apply") and callable(closure.apply):
                return sequence.map(closure.apply)
            if callable(closure):
                return sequence.map(closure)
            raise ValueError("map closure is not callable")

        return super()._evaluate_map(args, kwargs)

    def _coerce_sequence(self, value: Any) -> SequenceValue:
        if isinstance(value, db.Bag):
            return SequenceValue(self._iter_dask_bag(value), total_size=None)
        return super()._coerce_sequence(value)

    def _iter_dask_bag(self, bag: db.Bag):
        def iterator_factory() -> Iterable[Any]:
            for delayed_partition in bag.to_delayed():
                partition_items = delayed_partition.compute()
                for item in partition_items:
                    yield item

        return iterator_factory
