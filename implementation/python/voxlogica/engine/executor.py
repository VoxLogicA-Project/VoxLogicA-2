"""Pure primitive execution on a thread pool.

The executor knows nothing about scheduling, caching, or the language: it takes a
single primitive node whose inputs are already materialized, invokes its kernel,
and returns the value. ITK kernels release the GIL, so a thread pool gives real
CPU parallelism while the event loop keeps coordinating.
"""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeId
from voxlogica.primitives.registry import PrimitiveRegistry


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

    def _compute(self, table: NodeTable, node_id: NodeId) -> Any:
        """Gather already-materialized inputs and invoke the kernel."""
        node = table.nodes[node_id]
        if node.operator == "default.subsequence":
            sequence = table.values[node.args[0]]
            start = int(table.values[node.args[1]])
            stop = int(table.values[node.args[2]])
            kernel = self.registry.load_kernel("default.subsequence")
            return self._invoke(kernel, [sequence, start, stop], {})
        kernel = self.registry.load_kernel(node.operator)
        args = [table.values[arg_id] for arg_id in node.args]
        kwargs = {key: table.values[arg_id] for key, arg_id in node.kwargs}
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
