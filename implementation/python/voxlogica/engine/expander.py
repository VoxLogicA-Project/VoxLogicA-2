"""Dynamic loop expansion — the single semantics for iteration.

Iteration in VoxLogicA is defined exactly once, by reduction (AST -> nodes).
A ``for_loop``/``map`` node is never interpreted to a value; instead, once its
iterable is materialized, its closure body is *reduced* once per element into new
nodes — the same operation the reducer performs at compile time for constant
iterables, now performed at runtime with the concrete element values. Hash-consing
deduplicates subexpressions shared across elements.

The expander never computes values; it only grows the DAG, returning the new node
ids for the scheduler to pick up.

INCREMENTAL API: ``prepare`` (parse the closure body once, snapshot the items)
→ ``reduce_chunk`` (reduce a contiguous run of elements; pure CPU, safe to run
off the event loop) → ``sequence_id`` (intern the spliced sequence node once
every body id is known). Chunk boundaries cannot affect node identity: each
element reduces independently in the same environment, and hash-consing is
insertion-order-insensitive — so incremental and monolithic expansion produce
byte-identical node ids (determinism, warm-cache identity).

THREADING: ``reduce_chunk`` interns nodes into the shared table from the
expansion thread while the event loop reads other entries. Interning is a
per-key dict put of an immutable spec under a content hash (idempotent — a
racing duplicate insert writes an equivalent spec), and CPython dict operations
are GIL-atomic, so no lock is needed. Nothing on the event loop iterates
``table.nodes`` during a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeId, NodeSpec
from voxlogica.parser import parse_expression_content
from voxlogica.primitives.registry import PrimitiveRegistry

_EXPANDABLE = {"for_loop", "default.for_loop", "map", "default.map"}


@dataclass
class Expansion:
    """One loop's in-flight unroll: parsed body + items + accumulated body ids."""

    node_id: NodeId
    items: list[Any]
    variable: str
    body_ast: Any
    base_env: Any
    body_ids: list[NodeId] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)


class Expander:
    """Unrolls closure-over-sequence nodes into per-element DAG nodes."""

    def __init__(self, table: NodeTable, registry: PrimitiveRegistry):
        self.table = table
        self.registry = registry

    def can_expand(self, node: NodeSpec) -> bool:
        """True for a closure-over-sequence node (args = (iterable, closure))."""
        return node.kind == "primitive" and node.operator in _EXPANDABLE and len(node.args) == 2

    # ── Incremental expansion ─────────────────────────────────────────────────

    def prepare(self, node_id: NodeId, node: NodeSpec, iterable: Any) -> Expansion | None:
        """Parse the closure once and snapshot the items; None if not expandable.

        Cheap (one parse, one env rebuild) — runs on the event loop. The
        per-element reduction work happens in ``reduce_chunk``.
        """
        try:
            items = list(iterable)
        except TypeError:
            return None
        closure = self.table.nodes[node.args[1]]
        if closure.kind != "closure":
            return None
        return Expansion(
            node_id=node_id,
            items=items,
            variable=str(closure.attrs.get("parameter", "arg")),
            body_ast=parse_expression_content(str(closure.attrs.get("body", ""))),
            base_env=self._closure_environment(closure),
        )

    def reduce_chunk(self, expansion: Expansion, start: int, stop: int) -> list[NodeId]:
        """Reduce elements [start, stop) to body node ids (pure CPU, off-loop safe).

        Appends to ``expansion.body_ids`` in element order; per-element results
        are independent, so any chunking yields identical ids.
        """
        from voxlogica.reducer import WorkPlan, OperationVal, reduce_expression, _create_constant_node

        plan = WorkPlan(nodes=self.table.nodes, registry=self.registry)
        ids: list[NodeId] = []
        for item in expansion.items[start:stop]:
            const_id = _create_constant_node(plan, item)
            ids.append(reduce_expression(
                expansion.base_env.bind(expansion.variable, OperationVal(const_id)),
                plan, expansion.body_ast))
        expansion.body_ids.extend(ids)
        return ids

    def sequence_id(self, expansion: Expansion) -> NodeId:
        """Intern the spliced sequence node over all reduced bodies."""
        from voxlogica.reducer import WorkPlan, _plan_primitive_call

        assert len(expansion.body_ids) == expansion.total, "sequence before full unroll"
        plan = WorkPlan(nodes=self.table.nodes, registry=self.registry)
        return _plan_primitive_call(plan, "sequence", tuple(expansion.body_ids),
                                    output_kind="sequence")

    # ── One-shot expansion (compatibility; used by tests/tools) ───────────────

    def expand(self, node_id: NodeId, node: NodeSpec) -> tuple[NodeId, set[NodeId]] | None:
        """Reduce the body per element; return (sequence_id, new_node_ids) or None."""
        iterable = self.table.values.get(node.args[0])
        if iterable is None:
            return None
        expansion = self.prepare(node_id, node, iterable)
        if expansion is None:
            return None
        before = set(self.table.nodes.keys())
        self.reduce_chunk(expansion, 0, expansion.total)
        sequence_id = self.sequence_id(expansion)
        return sequence_id, set(self.table.nodes.keys()) - before

    # ── Closure environments ──────────────────────────────────────────────────

    def _closure_environment(self, closure: NodeSpec) -> Any:
        """Rebuild the closure's reduce-time environment from its captures."""
        from voxlogica.reducer import Environment, OperationVal

        env = Environment({})
        for name, arg_id in zip(closure.attrs.get("capture_names", []), closure.args, strict=False):
            env = env.bind(name, OperationVal(arg_id))
        for name, spec in dict(closure.attrs.get("function_captures", {})).items():
            env = env.bind(name, self._function_value(spec))
        return env

    def _function_value(self, spec: dict) -> Any:
        """Rebuild a captured FunctionVal from its serialized spec."""
        from voxlogica.reducer import Environment, OperationVal, FunctionVal

        env = Environment({})
        for name, node_id in dict(spec.get("captures", {})).items():
            env = env.bind(name, OperationVal(node_id))
        for name, nested in dict(spec.get("functions", {})).items():
            env = env.bind(name, self._function_value(nested))
        return FunctionVal(env, list(spec.get("parameters", [])), parse_expression_content(str(spec["body"])))

    @staticmethod
    def function_capture_ids(attrs: dict) -> set[NodeId]:
        """Node ids referenced inside an attrs' function_captures, recursively."""
        ids: set[NodeId] = set()
        stack = list(attrs.get("function_captures", {}).values())
        while stack:
            spec = stack.pop()
            ids.update(dict(spec.get("captures", {})).values())
            stack.extend(dict(spec.get("functions", {})).values())
        return ids

    @staticmethod
    def dependencies(node: NodeSpec) -> set[NodeId]:
        """Every node id a node depends on, including hidden function-capture refs."""
        deps: set[NodeId] = set(node.args) | {a for _, a in node.kwargs}
        return deps | Expander.function_capture_ids(node.attrs)

    @staticmethod
    def closure_capture_ids(node: NodeSpec) -> set[NodeId]:
        """All node ids a closure captures (pinned so expansion can re-read them)."""
        return set(node.args) | Expander.function_capture_ids(node.attrs)
