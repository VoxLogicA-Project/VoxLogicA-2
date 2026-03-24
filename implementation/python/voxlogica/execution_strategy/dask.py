"""Dask-first execution strategy over the same SymbolicPlan contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json

import dask.bag as db

from voxlogica.lazy.hash import hash_child_ref
from voxlogica.policy import enforce_runtime_read_path_policy
from voxlogica.policy import runtime_policy_is_serve_mode
from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.inspectable_sequence import InspectableMappedSequence
from voxlogica.execution_strategy.strict import StrictExecutionStrategy


class DaskExecutionStrategy(StrictExecutionStrategy):
    """Execution strategy that keeps sequence operations in Dask bags."""

    name = "dask"

    def _prefer_inspectable_sequences(self) -> bool:
        return runtime_policy_is_serve_mode()

    def _evaluate_range(self, args: list[Any], kwargs: dict[str, Any], *, parent_ref: str) -> db.Bag:
        if self._prefer_inspectable_sequences():
            return super()._evaluate_range(args, kwargs, parent_ref=parent_ref)
        del parent_ref
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

    def _evaluate_load(self, args: list[Any], kwargs: dict[str, Any], *, parent_ref: str) -> Any:
        if self._prefer_inspectable_sequences():
            return super()._evaluate_load(args, kwargs, parent_ref=parent_ref)
        del parent_ref
        if not args:
            raise ValueError("load requires one dataset argument")

        source = args[0]
        enforce_runtime_read_path_policy("load", [source])
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

    def _evaluate_map(self, args: list[Any], kwargs: dict[str, Any], *, parent_ref: str) -> Any:
        if self._prefer_inspectable_sequences():
            if not args:
                raise ValueError("map/for_loop requires sequence argument")
            sequence = super()._coerce_sequence(args[0], parent_ref=f"{parent_ref}:source")
            closure = kwargs.get("closure")
            if closure is None and len(args) > 1:
                closure = args[1]
            if closure is None:
                raise ValueError("map/for_loop requires closure argument")
            return InspectableMappedSequence(
                parent_ref=parent_ref,
                source=sequence,
                mapper=self._interactive_mapper(closure, parent_ref=parent_ref),
            )
        if not args:
            raise ValueError("map/for_loop requires sequence argument")

        sequence = args[0]
        closure = kwargs.get("closure")
        if closure is None and len(args) > 1:
            closure = args[1]
        if closure is None:
            raise ValueError("map/for_loop requires closure argument")

        if isinstance(sequence, db.Bag):
            # Runtime closures capture evaluator state that is not safely picklable for
            # Dask task transport; use strict iterator semantics in that case.
            if hasattr(closure, "evaluator"):
                return super()._evaluate_map(args, kwargs, parent_ref=parent_ref)
            if hasattr(closure, "apply") and callable(closure.apply):
                return sequence.map(closure.apply)
            if callable(closure):
                return sequence.map(closure)
            raise ValueError("map closure is not callable")

        return super()._evaluate_map(args, kwargs, parent_ref=parent_ref)

    def _coerce_sequence(self, value: Any, *, parent_ref: str = "runtime") -> SequenceValue:
        if self._prefer_inspectable_sequences():
            return super()._coerce_sequence(value, parent_ref=parent_ref)
        if isinstance(value, db.Bag):
            return SequenceValue(self._iter_dask_bag(value), total_size=None)
        return super()._coerce_sequence(value, parent_ref=parent_ref)

    def _iter_dask_bag(self, bag: db.Bag):
        def iterator_factory() -> Iterable[Any]:
            for delayed_partition in bag.to_delayed():
                partition_items = delayed_partition.compute()
                for item in partition_items:
                    yield item

        return iterator_factory

    def _interactive_mapper(self, closure: Any, *, parent_ref: str):
        strategy = self

        class _Mapper:
            def apply_with_ref(self, upstream: Any, *, runtime_ref: str) -> Any:
                if hasattr(closure, "apply_with_ref") and callable(closure.apply_with_ref):
                    value = closure.apply_with_ref(upstream, runtime_ref=runtime_ref)
                elif hasattr(closure, "invoke") and callable(closure.invoke):
                    value = closure.invoke([upstream], runtime_ref=runtime_ref)
                elif hasattr(closure, "apply") and callable(closure.apply):
                    value = closure.apply(upstream)
                elif callable(closure):
                    value = closure(upstream)
                else:
                    raise ValueError("map closure is not callable")

                if strategy._looks_sequence_like(value):
                    child_ref = hash_child_ref(
                        parent_ref,
                        family="mapped-child",
                        token={"runtime_ref": runtime_ref},
                    )
                    return StrictExecutionStrategy._coerce_sequence(strategy, value, parent_ref=child_ref)
                return value

        return _Mapper()

    def _looks_sequence_like(self, value: Any) -> bool:
        if isinstance(value, SequenceValue):
            return True
        if isinstance(value, (list, tuple, range)):
            return True
        if isinstance(value, db.Bag):
            return True
        return hasattr(value, "compute") and callable(value.compute)
