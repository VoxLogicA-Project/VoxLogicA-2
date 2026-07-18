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

from voxlogica.arrays import PolyArray
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeId
from voxlogica.primitives.registry import PrimitiveRegistry

if TYPE_CHECKING:
    from voxlogica.engine.fusion import Cone

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
        while the cone is in flight — the same PolyArray wrap/unwrap contract
        as ``_compute`` applies at every step).
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
            value = self._compute_node(table.nodes[member_id], lookup)
            scratch[member_id] = value
            results[member_id] = value
        return results

    def _compute(self, table: NodeTable, node_id: NodeId) -> Any:
        """Gather already-materialized inputs and invoke the kernel."""
        return self._compute_node(table.nodes[node_id], lambda dep_id: table.values[dep_id])

    def _compute_node(self, node, lookup: Callable[[NodeId], Any]) -> Any:
        """Gather one node's inputs via ``lookup`` and invoke its kernel.

        Shared by single-node dispatch (``_compute``, looks up ``table.values``
        directly) and cone execution (``_compute_cone``, looks up a scratch
        dict first) — the only difference between the two paths is where an
        input's value comes from, never how a kernel is invoked or how its
        result is adapted, so the two paths cannot semantically diverge.
        """
        if node.operator == "default.subsequence":
            sequence = lookup(node.args[0])
            start = int(lookup(node.args[1]))
            stop = int(lookup(node.args[2]))
            kernel = self.registry.load_kernel("default.subsequence")
            return _wrap(self._invoke(kernel, [sequence, start, stop], {}))
        kernel = self.registry.load_kernel(node.operator)
        args = [_unwrap(lookup(arg_id)) for arg_id in node.args]
        kwargs = {key: _unwrap(lookup(arg_id)) for key, arg_id in node.kwargs}
        return _wrap(self._invoke(kernel, args, kwargs, node.attrs))

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
