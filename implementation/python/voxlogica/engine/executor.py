"""Pure primitive execution on a thread pool.

The executor knows nothing about scheduling, caching, or the language: it takes a
single primitive node whose inputs are already materialized, invokes its kernel,
and returns the value. ITK kernels release the GIL, so a thread pool gives real
CPU parallelism while the event loop keeps coordinating.

This is also the sole PolyArray adapter boundary (see ``voxlogica.arrays``):
kernels are untouched and still speak plain ``sitk.Image``. Inputs that arrived
as a ``PolyArray`` (produced by a prior kernel call, or reloaded from disk —
see ``NodeTable.load``) are unwrapped to their ``.sitk()`` view before a kernel
sees them; a kernel result that is a ``sitk.Image`` is wrapped into a
``PolyArray`` before it re-enters the table. Every other value (scalars,
sequences, closures) passes through unchanged. Keeping the adapter in this one
place, rather than in every kernel, is what lets fused/numba execution
(``engine/fusion.py``) later swap in a different array library without any
kernel ever knowing.
"""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TYPE_CHECKING

import numpy as np

from voxlogica.arrays import PolyArray
from voxlogica.engine.node_table import NodeTable
from voxlogica.engine.numba_fusion import shape_of
from voxlogica.lazy.ir import NodeId
from voxlogica.primitives.registry import PrimitiveRegistry

if TYPE_CHECKING:
    from voxlogica.engine.fusion import Cone
    from voxlogica.engine.numba_fusion import NumbaFusionBackend, ShapeBinding

_sitk = None


def _simpleitk():
    global _sitk
    if _sitk is None:
        import SimpleITK
        _sitk = SimpleITK
    return _sitk


def _unwrap(value: Any) -> Any:
    """PolyArray -> its sitk view; everything else passes through untouched."""
    return value.sitk() if isinstance(value, PolyArray) else value


def _wrap(value: Any) -> Any:
    """A kernel's sitk.Image result -> PolyArray; everything else untouched."""
    sitk = _simpleitk()
    if sitk is not None and isinstance(value, sitk.Image):
        return PolyArray.from_sitk(value)
    return value


class Executor:
    """Runs one primitive at a time on a bounded thread pool."""

    def __init__(self, registry: PrimitiveRegistry, max_workers: int):
        self.registry = registry
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        # Signature introspection is stable per kernel; cache it so the hot path
        # doesn't re-parse it on every one of a sweep's thousands of calls.
        self._signatures: dict[Any, tuple[list, bool, bool]] = {}

    async def run(self, table: NodeTable, node_id: NodeId) -> Any:
        """Materialize one primitive node off the event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._compute, table, node_id)

    async def run_cone(self, table: NodeTable, cone: "Cone") -> dict[NodeId, Any]:
        """Materialize every member of a fusion cone in ONE pool dispatch.

        This is Stage A (see doc/specs/semantic-queueing-fusion.md §3): each
        member still runs its real, unmodified kernel — only the scheduling
        round trip is batched, one thread-pool task instead of one per node.
        Results are bit-identical to running each member individually because
        the math never changes, only where the values are looked up from
        between members (a local scratch dict standing in for the node table
        while the cone is in flight).

        Interior members (``cone.interiors`` — never enter ``table.values``,
        see ``DependencyGraph.complete_cone``) are kept RAW (whatever a kernel
        naturally returns, e.g. ``sitk.Image``) in scratch, never wrapped into
        ``PolyArray``: a cone-internal edge immediately unwraps whatever it
        wrapped one line later, so wrapping it at all was pure waste —
        profiling found ``PolyArray.from_sitk``'s eager ``Geometry`` read
        (3 sitk metadata calls) dominating a fused run's wall time once
        interior completion bookkeeping was already batched (see
        doc/dev/dynamic-scheduler/frontier-scheduler.md). Only exits, which
        must become uniform table-resident values, get wrapped.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._compute_cone, table, cone)

    def _compute_cone(self, table: NodeTable, cone: "Cone") -> dict[NodeId, Any]:
        """Run every cone member in topological order, in this pool thread."""
        scratch: dict[NodeId, Any] = {}

        def lookup(dep_id: NodeId) -> Any:
            return scratch[dep_id] if dep_id in scratch else table.values[dep_id]

        results: dict[NodeId, Any] = {}
        for member_id in cone.members_topo:
            raw = self._compute_node(table.nodes[member_id], lookup)
            if member_id in cone.exits:
                wrapped = _wrap(raw)
                scratch[member_id] = wrapped
                results[member_id] = wrapped
            else:
                scratch[member_id] = raw  # interior: stays raw, never wrapped
        return results

    async def run_cone_numba(self, table: NodeTable, cone: "Cone", compiled_fn: Callable,
                             binding: "ShapeBinding") -> dict[NodeId, Any]:
        """Materialize a cone via its compiled Stage-B kernel (one flat loop,
        one pool dispatch, zero per-member kernel calls or intermediate
        array allocations — see ``engine/numba_fusion.py``).

        Falls back to nothing: the caller (``ComputationEngine._worker``)
        only reaches this once ``NumbaFusionBackend.try_get`` has returned a
        compiled callable for this exact cone shape; every other case still
        runs ``run_cone`` (Stage A), unchanged.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, self._compute_cone_numba, table, cone, compiled_fn, binding)

    async def run_cone_auto(self, table: NodeTable, cone: "Cone",
                             numba_backend: "NumbaFusionBackend | None") -> tuple[dict[NodeId, Any], bool]:
        """Materialize a cone, deciding Stage A vs Stage B on the SAME pool
        thread that runs it — never on the event loop.

        This matters for more than the throughput contract (nothing on the
        event loop blocks on I/O, see ``engine/core.py``'s module docstring):
        ``shape_of``'s dtype/geometry probe can force a table-resident
        ``PolyArray``'s first numpy view into existence (``.np()``), which
        internally calls into sitk's own C++ reference-counted image
        machinery. That must never race a concurrently-running kernel on
        another pool thread touching the SAME shared value (any node with
        more than one consumer) — ``PolyArray`` guards its own view cache
        (see ``arrays.py``) against races between two callers, but only
        keeping every caller of ``.np()``/``.sitk()`` on already-serialized
        pool threads keeps this cheap and simple.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._compute_cone_auto, table, cone, numba_backend)

    def _compute_cone_auto(self, table: NodeTable, cone: "Cone",
                            numba_backend: "NumbaFusionBackend | None") -> tuple[dict[NodeId, Any], bool]:
        # Size-gate BEFORE deriving the shape: shape_of walks every member and
        # probes input dtypes/geometry, and small cones (mean size ~2-5 in
        # typical programs) are the COMMON case — paying that walk on every
        # dispatch just for try_get to reject the shape on the same size test
        # (len(shape.ops) == len(cone)) would tax the majority path for the
        # benefit of none.
        if numba_backend is not None and len(cone) >= numba_backend.min_members:
            binding = shape_of(cone, table, self.registry)
            if binding is not None:
                compiled_fn = numba_backend.try_get(binding.shape)
                if compiled_fn is not None:
                    return self._compute_cone_numba(table, cone, compiled_fn, binding), True
        return self._compute_cone(table, cone), False

    def _compute_cone_numba(self, table: NodeTable, cone: "Cone", compiled_fn: Callable,
                             binding: "ShapeBinding") -> dict[NodeId, Any]:
        arrays: list[Any] = []
        ref_shape: tuple[int, ...] | None = None
        ref_geometry = None
        for dep_id in binding.array_input_ids:
            value = table.values[dep_id]
            arr = value.np()
            if ref_shape is None:
                ref_shape = arr.shape
                ref_geometry = value.geometry
            arrays.append(np.ascontiguousarray(arr).reshape(-1))
        scalars = [float(table.values[dep_id]) for dep_id in binding.scalar_input_ids]

        n = arrays[0].shape[0]
        outputs = []
        for exit_id in binding.exit_ids_topo:
            out_dtype_name = self.registry.get_spec(table.nodes[exit_id].operator).elementwise.out_dtype
            outputs.append(np.empty(n, dtype=np.dtype(out_dtype_name)))

        compiled_fn(*arrays, *scalars, *outputs)

        results: dict[NodeId, Any] = {}
        for exit_id, flat_out in zip(binding.exit_ids_topo, outputs):
            results[exit_id] = PolyArray.from_numpy(flat_out.reshape(ref_shape), ref_geometry)
        return results

    def _compute(self, table: NodeTable, node_id: NodeId) -> Any:
        """Gather already-materialized inputs and invoke the kernel."""
        return _wrap(self._compute_node(table.nodes[node_id], lambda dep_id: table.values[dep_id]))

    def _compute_node(self, node, lookup: Callable[[NodeId], Any]) -> Any:
        """Gather one node's inputs via ``lookup`` and invoke its kernel.

        Returns the RAW kernel result — wrapping is the caller's decision
        (``_compute`` always wraps, since a single-node value always enters
        ``table.values``; ``_compute_cone`` wraps only exits). Shared by both
        paths so kernel invocation and argument adaptation cannot diverge
        between them; only where an input's value comes from differs
        (``table.values`` directly vs. a cone's in-flight scratch dict).
        """
        if node.operator == "default.subsequence":
            sequence = lookup(node.args[0])
            start = int(lookup(node.args[1]))
            stop = int(lookup(node.args[2]))
            kernel = self.registry.load_kernel("default.subsequence")
            return self._invoke(kernel, [sequence, start, stop], {})
        kernel = self.registry.load_kernel(node.operator)
        args = [_unwrap(lookup(arg_id)) for arg_id in node.args]
        kwargs = {key: _unwrap(lookup(arg_id)) for key, arg_id in node.kwargs}
        return self._invoke(kernel, args, kwargs, node.attrs)

    def _invoke(self, kernel, args: list[Any], kwargs: dict[str, Any], attrs: dict[str, Any] | None = None) -> Any:
        """Adapt engine arguments to the kernel's declared Python signature."""
        params, has_varkw, has_varargs = self._signature(kernel)

        if has_varkw:
            payload = {str(index): value for index, value in enumerate(args)}
            payload.update(kwargs)
            if attrs:
                payload.update(attrs)
            return kernel(**payload)
        if has_varargs:
            return kernel(*args, **kwargs)

        bound = dict(kwargs)
        for index, value in enumerate(args):
            if index >= len(params):
                raise ValueError(f"Kernel received too many positional arguments: {len(args)}")
            param = params[index]
            if param.name not in bound:
                bound[param.name] = value
        return kernel(**bound)

    def _signature(self, kernel) -> tuple[list, bool, bool]:
        """Return the kernel's (params, has_varkw, has_varargs), cached per kernel."""
        cached = self._signatures.get(kernel)
        if cached is None:
            params = list(inspect.signature(kernel).parameters.values())
            has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
            has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            cached = (params, has_varkw, has_varargs)
            self._signatures[kernel] = cached
        return cached

    def shutdown(self) -> None:
        """Release the thread pool."""
        self._pool.shutdown(wait=False)
