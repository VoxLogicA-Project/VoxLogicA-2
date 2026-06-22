"""Lazy in-process interpreter for symbolic plans.

This strategy evaluates the DAG directly in Python, memoizing node results in
the prepared plan and reconstructing reducer-generated closures on demand.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import inspect
import json
import os
import pickle
import time
import traceback

from tqdm import tqdm

from voxlogica.execution_strategy.base import ExecutionStrategy
from voxlogica.execution_strategy.results import ExecutionResult, PageResult, PreparedPlan, SequenceValue
from voxlogica.lazy.ir import NodeId, NodeSpec, SymbolicPlan
from voxlogica.parser import EArray, EBool, ECall, EFilter, EFold, EFor, ELet, ENumber, ESlice, EString, Expression, parse_expression_content
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.storage import MaterializationStore, StorageBackend
from voxlogica.value_model import adapt_runtime_value
from voxlogica.pod_codec import encode_for_storage
from voxlogica.lazy.hash import hash_sequence_item

def _default_max_concurrency() -> int:
    """Max primitive kernels running concurrently in the async executor.

    ITK kernels are internally multithreaded, so a handful already saturate the
    CPU; the cap's real job is to bound peak memory. Each in-flight image kernel
    holds a large working set and produces a result that the (single) persistence
    thread must write to disk before the memo cache can evict it. Too many
    concurrent producers outrun persistence and the un-evictable results pile up
    until OOM. Default to the core count; override with VOXLOGICA_MAX_CONCURRENCY.
    """
    raw = os.environ.get("VOXLOGICA_MAX_CONCURRENCY")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return os.cpu_count() or 8


_MAX_CONCURRENCY = _default_max_concurrency()

# Dynamic DAG expansion (issue #22): when a runtime-valued for-loop's iterable is
# computed, unroll its body into one DAG node per element and splice them into the
# live scheduler, instead of running the body sequentially in the for_loop kernel.
# Disable with VOXLOGICA_DYNAMIC_EXPANSION=0 to fall back to the sequential kernel.
_DYNAMIC_EXPANSION = os.environ.get("VOXLOGICA_DYNAMIC_EXPANSION", "1") != "0"

# Operators eligible for dynamic expansion (closure-over-sequence form: args = (iterable, closure)).
_DYNAMIC_EXPANSION_OPERATORS = {"for_loop", "default.for_loop"}

# Module-level thread pool: workers call ITK kernels (GIL released → true parallelism).
# Sized to the concurrency cap so queued futures don't pin more worker threads than
# we allow to run.
_executor = ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY)

_LAZY_SEQUENCE_OPERATORS = {
    "default.map", 
    "map",
    "default.filter",
    "filter",
    "default.for_loop",
    "for_loop"
}

@dataclass
class RuntimeFunction:
    """Reducer-level function value reified for runtime invocation."""

    parameters: list[str]
    expression: Expression
    captures: dict[str, Any]
    evaluator: "LazyExecutionStrategy"

    def invoke(self, args: list[Any]) -> Any:
        if len(args) != len(self.parameters):
            raise ValueError(f"Function expects {len(self.parameters)} args, got {len(args)}")
        env = dict(self.captures)
        for parameter, value in zip(self.parameters, args, strict=False):
            env[parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.expression, env)


@dataclass
class RuntimeClosure:
    """One-argument runtime closure used by ``map`` and ``for_loop``."""

    parameter: str
    body_expression: Expression
    captures: dict[str, Any]
    evaluator: "LazyExecutionStrategy"

    def apply(self, value: Any) -> Any:
        env = dict(self.captures)
        env[self.parameter] = value
        return self.evaluator._evaluate_runtime_expression(self.body_expression, env)

    def __call__(self, value: Any) -> Any:
        return self.apply(value)
    
@dataclass
class Demand:
    pass

@dataclass
class FullDemand(Demand):
    pass

@dataclass
class SliceDemand(Demand):
    start = 0
    stop = 0

    def __init__(self,start,stop):
        self.start = start
        self.stop = stop

@dataclass
class IndexDemand(Demand):
    index = -1
    
    def __init__(self,index):
        self.index = index


class LazyExecutionStrategy(ExecutionStrategy):
    """Strategy that evaluates the symbolic graph locally."""

    name = "lazy"

    def __init__(self, registry: PrimitiveRegistry | None = None, results_database: StorageBackend | None = None):
        self.registry = registry or PrimitiveRegistry()
        self.results_database = results_database
        self._cache_summary: dict[str, Any] = {}
        self._node_events: list[dict[str, Any]] = []
        self._progress: tqdm | None = None

    def compile(self, plan: SymbolicPlan) -> PreparedPlan:
        """Prepare a plan for execution and reset namespace runtime state."""
        self.registry.apply_imports(plan.imported_namespaces)
        self.registry.reset_runtime_state()
        #if self.results_database is not None:
        #    self.results_database.put_plan_definitions(plan)
        return PreparedPlan(
            plan=plan,
            # definition_store=DefinitionStore(plan.nodes),
            materialization_store=MaterializationStore(
                backend=self.results_database,
                read_through=True,
                write_through=True,
            ),
            strategy_name=self.name,
        )

    def run(self, prepared: PreparedPlan, goals: list[NodeId] | None = None) -> ExecutionResult:
        started = time.time()
        failures: dict[NodeId, str] = {}
        self._cache_summary = {"computed": 0, "cached_local": 0, "cached_store": 0, "failed": 0}
        self._node_events = []
        target_goals = [goal.id for goal in prepared.plan.goals] if goals is None else list(goals)
        target_goal_set = set(target_goals)

        self._progress = tqdm(total=len(prepared.plan.nodes), desc="nodes", unit="node",
                              dynamic_ncols=True, file=__import__("sys").stderr, leave=True)
        try:
            asyncio.run(self._async_run(prepared, list(target_goal_set)))
        finally:
            self._progress.close()
            self._progress = None

        if goals is None:
            for goal in prepared.plan.goals:
                if goal.id not in target_goal_set:
                    continue
                if goal.id not in prepared.values:
                    # Goal was not computed - check if it failed
                    if goal.id in failures:
                        # Already recorded as failed, skip side effect
                        continue
                    # This shouldn't happen - goal should be in values or failures
                    import sys
                    print(f"WARNING: Goal {goal.id} not in prepared.values or failures", file=sys.stderr)
                    print(f"Available values: {list(prepared.values.keys())}", file=sys.stderr)
                    print(f"Failed operations: {list(failures.keys())}", file=sys.stderr)
                    continue
                try:
                    value = prepared.values[goal.id]
                    self._run_goal_side_effect(goal.operation, goal.name, value)
                except Exception as exc:  # noqa: BLE001
                    error_trace = traceback.format_exc()
                    failures[goal.id] = error_trace

        if prepared.materialization_store is not None:
            prepared.materialization_store.flush(timeout_s=10.0)

        return ExecutionResult(
            success=not failures,
            completed_operations=set(prepared.completed_nodes),
            failed_operations=failures,
            execution_time=time.time() - started,
            total_operations=len(prepared.plan.nodes),
            cache_summary=dict(self._cache_summary),
            node_events=list(self._node_events),
        )

    def stream(self, prepared: PreparedPlan, node: NodeId, chunk_size: int) -> Iterable[list[object]]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")

        sequence = self._coerce_sequence(self._ensure_node_value(prepared, node))
        chunk: list[object] = []
        for item in sequence.iter_values():
            chunk.append(item)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk

    def page(self, prepared: PreparedPlan, node: NodeId, offset: int, limit: int) -> PageResult:
        value = self._ensure_node_value(prepared, node)
        try:
            sequence = self._coerce_sequence(value)
            items = sequence.page(offset=offset, limit=limit)
            next_offset = offset + len(items)
            if len(items) < limit:
                next_offset = None
        except ValueError:
            items = [] if offset > 0 or limit <= 0 else [value]
            next_offset = None
        return PageResult(items=items, offset=offset, limit=limit, next_offset=next_offset)
    
    def cache(self, prepared: PreparedPlan, nodeId: NodeId, value: Any):
        node = prepared.plan.nodes[nodeId]
        expression = node.operator if node.kind != "closure" else {"body": node.attrs.get("body"), "parameter": node.attrs.get("parameter"), "capture_names": node.attrs.get("capture_names"), "function_captures": node.attrs.get("function_captures")}
        dependencies = list(node.args) + [value_id for _, value_id in node.kwargs]
        # Persist only through the materialization store (its backend is the
        # results database). A second synchronous results_database.put_success
        # here would double-write and, for large images, block the event loop on
        # disk I/O — starving the executor of work. See issue #19.
        if prepared.materialization_store is not None:
            prepared.materialization_store.put(nodeId, expression, dependencies, value, metadata={"source": "runtime", "operator": node.operator})
        prepared.completed_nodes.add(nodeId)
        if self._progress is not None:
            self._progress.set_postfix_str(node.operator, refresh=False)
            self._progress.update(1)

    def cache_sequence_item(self, prepared: PreparedPlan, nodeId: NodeId, index:int, value:Any):
        node = prepared.plan.nodes[nodeId]
        id = hash_sequence_item(nodeId,index)
        expression = node.operator if node.kind != "closure" else {"body": node.attrs.get("body"), "parameter": node.attrs.get("parameter"), "capture_names": node.attrs.get("capture_names"), "function_captures": node.attrs.get("function_captures")}
        dependencies = list(node.args) + [value_id for _, value_id in node.kwargs]
        prepared.completed_nodes.add(nodeId)
        if self._progress is not None:
            self._progress.update(1)
        if prepared.materialization_store is not None:
            prepared.materialization_store.put(id, expression, dependencies, value, metadata={"source": "runtime", "operator": node.operator})

    def cache_lookup(self, prepared: PreparedPlan, nodeId: NodeId):
        value = None
        if prepared.materialization_store is not None:
            value = prepared.materialization_store.get(nodeId)
            if value is not None:
                if nodeId not in prepared.completed_nodes:
                    prepared.completed_nodes.add(nodeId)
                    if self._progress is not None:
                        self._progress.update(1)
                return value
        if self.results_database is not None:
            tmp = self.results_database.get_record(nodeId)
            if tmp is not None:
                if tmp.vox_type == "image":
                    value = tmp.payload_bin
                else:
                    value = tmp.payload_json["value"]
        if value is not None and nodeId not in prepared.completed_nodes:
            prepared.completed_nodes.add(nodeId)
            if self._progress is not None:
                self._progress.update(1)
        return value

    def cache_sequence_item_lookup(self, prepared: PreparedPlan, nodeId: NodeId, index: int):
        value = None
        if prepared.materialization_store is not None:
            value = prepared.materialization_store.get(hash_sequence_item(nodeId, index))
            if value is not None:
                return value
        if self.results_database is not None:
            tmp = self.results_database.get_record(hash_sequence_item(nodeId, index))
            if tmp is not None:
                if tmp.vox_type == "image":
                    value = tmp.payload_bin
                else:
                    value = tmp.payload_json["value"]
        return value

    def _evaluate_node_lazy(self, prepared: PreparedPlan, nodeid: NodeId, demand: Demand) -> Any:
        value = self.cache_lookup(prepared, nodeid)
        if value is not None:
            return value
        node = prepared.plan.nodes[nodeid]
        if node.kind == "constant":
            return node.attrs.get("value")

        if node.kind == "closure":
            return self._build_runtime_closure_from_values(prepared, node)

        if node.kind == "primitive":
            if node.operator == "default.subsequence":
                start = int(self._evaluate_node_lazy(prepared, node.args[1], demand))
                stop = int(self._evaluate_node_lazy(prepared, node.args[2], demand))
                child_operator = prepared.plan.nodes[node.args[0]].operator
                if child_operator in _LAZY_SEQUENCE_OPERATORS:
                    value = self._evaluate_node_lazy(
                        prepared, node.args[0], SliceDemand(start, stop)
                    )
                else:
                    sequence = self._evaluate_node_lazy(prepared, node.args[0], demand)
                    kernel = self.registry.load_kernel("default.subsequence")
                    value = self._invoke_kernel(kernel, [sequence, start, stop], {})
                self.cache(prepared, nodeid, value)
                return value
            
            kernel = self.registry.load_kernel(node.operator)
            tmpargs = [self._evaluate_node_lazy(prepared,arg_id,demand) for arg_id in node.args]
            kwargs = {key: self._evaluate_node_lazy(prepared,arg_id,demand) for key, arg_id in node.kwargs}
            if node.operator in _LAZY_SEQUENCE_OPERATORS:
                if isinstance(demand,SliceDemand):
                    value = []
                    for i in range(demand.start,demand.stop):
                        tmp = self.cache_sequence_item_lookup(prepared, nodeid, i)
                        if tmp is not None:
                            value.extend(tmp)
                        else:
                            args = tmpargs + [i,i+1]
                            tmp = self._invoke_kernel(kernel, args, kwargs, node.attrs)
                            value.extend(tmp)
                            self.cache_sequence_item(prepared,nodeid,i,tmp)
                else: 
                    args = tmpargs
                    value = self._invoke_kernel(kernel, args, kwargs, node.attrs)
                    self.cache(prepared,nodeid,value)
            else:
                args = tmpargs
                # kwargs = {key: self._evaluate_node_lazy(prepared,arg_id,demand) for key, arg_id in node.kwargs}
                value = self._invoke_kernel(kernel, args, kwargs, node.attrs)
            if node.operator in _LAZY_SEQUENCE_OPERATORS and isinstance(demand,FullDemand):
                for i in range(0,len(value)):
                    self.cache_sequence_item(prepared,nodeid,i,value[i])
            else:
                self.cache(prepared, nodeid, value)
            return value

        raise ValueError(f"Unsupported node kind: {node.kind}")


    # ── Async task-graph executor (issue #19) ───────────────────────────────────────────────────

    @staticmethod
    def _extract_function_spec_node_ids(spec: dict) -> list[NodeId]:
        """Recursively collect all node IDs referenced inside a function_capture spec."""
        ids = list(dict(spec.get("captures", {})).values())
        for nested in dict(spec.get("functions", {})).values():
            ids.extend(LazyExecutionStrategy._extract_function_spec_node_ids(nested))
        return ids

    def _get_all_deps(self, node: NodeSpec) -> set[NodeId]:
        """All dependency node IDs for a node, including hidden function_capture references."""
        deps: set[NodeId] = set(node.args) | {a for _, a in node.kwargs}
        for spec in node.attrs.get("function_captures", {}).values():
            deps.update(self._extract_function_spec_node_ids(spec))
        return deps

    def _cached_exists(self, prepared: PreparedPlan, nid: NodeId) -> bool:
        """Cheap existence check for a previously-persisted node value.

        Must not materialize the value (that would defeat lazy loading). Only
        consults the in-RAM memo tier and the backend's existence index.
        """
        store = prepared.materialization_store
        if store is not None and nid in store._memory:
            return True
        if self.results_database is not None:
            return self.results_database.has(nid)
        return False

    # ── Dynamic DAG expansion (issue #22) ────────────────────────────────────────────────────

    def _funcval_from_spec(self, spec: dict) -> Any:
        """Rebuild a reduce-time FunctionVal from a serialized function_capture spec."""
        from voxlogica.reducer import Environment, OperationVal, FunctionVal

        env = Environment({})
        for name, node_id in dict(spec.get("captures", {})).items():
            env = env.bind(name, OperationVal(node_id))
        for name, nested in dict(spec.get("functions", {})).items():
            env = env.bind(name, self._funcval_from_spec(nested))
        return FunctionVal(env, list(spec.get("parameters", [])), parse_expression_content(str(spec["body"])))

    def _reduce_env_from_closure(self, node: NodeSpec) -> Any:
        """Reconstruct the closure's reduce-time environment (captures + function captures)."""
        from voxlogica.reducer import Environment, OperationVal

        env = Environment({})
        for name, arg_id in zip(node.attrs.get("capture_names", []), node.args, strict=False):
            env = env.bind(name, OperationVal(arg_id))
        for name, spec in dict(node.attrs.get("function_captures", {})).items():
            env = env.bind(name, self._funcval_from_spec(spec))
        return env

    def _expand_for_loop(self, prepared: PreparedPlan, nid: NodeId, node: NodeSpec):
        """Unroll a for_loop node over its (already-computed) iterable.

        Returns (seq_id, new_node_ids) where seq_id is a `sequence` node over the
        per-element body reductions, or None if the loop cannot be expanded (then the
        caller falls back to the sequential kernel). Newly created nodes are interned
        into prepared.plan.nodes via a WorkPlan sharing that same dict.
        """
        from voxlogica.reducer import WorkPlan, OperationVal, reduce_expression, _create_constant_node, _plan_primitive_call
        from voxlogica.lazy.ir import NodeSpec as _NodeSpec  # noqa: F401

        iterable_id, closure_id = node.args[0], node.args[1]
        items = prepared.values.get(iterable_id)
        if items is None:
            return None
        try:
            items = list(items)
        except TypeError:
            return None

        closure_node = prepared.plan.nodes[closure_id]
        if closure_node.kind != "closure":
            return None
        variable = str(closure_node.attrs.get("parameter", "arg"))
        body_ast = parse_expression_content(str(closure_node.attrs.get("body", "")))
        base_env = self._reduce_env_from_closure(closure_node)

        wp = WorkPlan(nodes=prepared.plan.nodes, registry=self.registry,
                      imported_namespaces=list(prepared.plan.imported_namespaces))
        before = set(prepared.plan.nodes.keys())

        body_ids: list[NodeId] = []
        for item in items:
            const_id = _create_constant_node(wp, item)
            env = base_env.bind(variable, OperationVal(const_id))
            body_ids.append(reduce_expression(env, wp, body_ast))

        seq_id = _plan_primitive_call(wp, "sequence", tuple(body_ids), output_kind="sequence")
        new_ids = set(prepared.plan.nodes.keys()) - before
        return seq_id, new_ids

    def _compute_primitive(self, prepared: PreparedPlan, nid: NodeId) -> Any:
        """Evaluate a single primitive node whose dependencies are already in prepared.values.

        Called from a ThreadPoolExecutor thread — must only read from prepared.values
        (never write to it) and must not call cache() or update tqdm.
        """
        node = prepared.plan.nodes[nid]
        assert node.kind == "primitive", f"_compute_primitive called on non-primitive {nid}"

        if node.operator == "default.subsequence":
            start = int(prepared.values[node.args[1]])
            stop  = int(prepared.values[node.args[2]])
            sequence = prepared.values[node.args[0]]
            kernel = self.registry.load_kernel("default.subsequence")
            return self._invoke_kernel(kernel, [sequence, start, stop], {})

        kernel = self.registry.load_kernel(node.operator)
        args   = [prepared.values[arg_id] for arg_id in node.args]
        kwargs = {key: prepared.values[arg_id] for key, arg_id in node.kwargs}
        return self._invoke_kernel(kernel, args, kwargs, node.attrs)

    def _build_runtime_closure_from_values_eager(self, prepared: PreparedPlan, node: NodeSpec) -> RuntimeClosure:
        """Build a RuntimeClosure by reading captures from prepared.values (already evaluated)."""
        body           = parse_expression_content(str(node.attrs.get("body", "")))
        parameter      = str(node.attrs.get("parameter", "arg"))
        capture_names  = list(node.attrs.get("capture_names", []))
        captures = {
            name: prepared.values[node_id]
            for name, node_id in zip(capture_names, node.args, strict=True)
        }
        for name, spec in dict(node.attrs.get("function_captures", {})).items():
            captures[name] = self._build_runtime_function_from_values_eager(prepared, spec)
        return RuntimeClosure(parameter=parameter, body_expression=body, captures=captures, evaluator=self)

    def _build_runtime_function_from_values_eager(self, prepared: PreparedPlan, spec: dict) -> RuntimeFunction:
        """Build a RuntimeFunction by reading captures from prepared.values."""
        expression = parse_expression_content(str(spec["body"]))
        captures = {
            name: prepared.values[node_id]
            for name, node_id in dict(spec.get("captures", {})).items()
        }
        for name, nested_spec in dict(spec.get("functions", {})).items():
            captures[name] = self._build_runtime_function_from_values_eager(prepared, nested_spec)
        return RuntimeFunction(
            parameters=list(spec.get("parameters", [])),
            expression=expression,
            captures=captures,
            evaluator=self,
        )

    async def _async_run(self, prepared: PreparedPlan, goal_ids: list[NodeId]) -> None:
        """Async task-graph executor: evaluate all nodes reachable from goal_ids in parallel.

        Strategy:
          1. BFS from goals to find nodes to compute, pruning at cached subtrees
             (cached values are loaded lazily, never bulk-pre-loaded into RAM).
          2. Build a reverse-edge dependency graph over nodes still needing computation.
          3. Seed with zero-in-degree nodes; when a node finishes, decrement its dependents
             and launch any that become ready.  ITK kernels run in a ThreadPoolExecutor
             (GIL released → true CPU parallelism).  All bookkeeping stays in the event loop.
        """
        # Goal values must survive until run() reads them for side-effects.
        goal_set = set(goal_ids)

        # ── Step 1: find nodes to compute, pruning at cached subtrees ─────────
        # BFS from the goals, but stop descending whenever a node's value is
        # already persisted: we will load it lazily on demand and never need its
        # dependencies. This keeps a re-run from walking (and bulk-loading) the
        # entire historical DAG, which would blow up memory on wide plans.
        to_compute: set[NodeId] = set()
        cached_leaves: set[NodeId] = set()
        queue = list(goal_ids)
        seen: set[NodeId] = set()
        while queue:
            nid = queue.pop()
            if nid in seen:
                continue
            seen.add(nid)
            if nid in prepared.values:
                continue
            # A goal must be materialized for its side-effect even if cached, so
            # it is always computed/loaded as a graph node (never a pruned leaf).
            if nid not in goal_set and self._cached_exists(prepared, nid):
                cached_leaves.add(nid)
                continue
            to_compute.add(nid)
            for dep in self._get_all_deps(prepared.plan.nodes[nid]):
                if dep not in seen:
                    queue.append(dep)

        # ── Step 2: build dependency graph over the nodes to compute ──────────
        # dependents[p] = list of children waiting on p (only p in `scheduled`).
        dependents: dict[NodeId, list[NodeId]] = defaultdict(list)
        pending: dict[NodeId, int] = {}  # remaining unsatisfied scheduled deps
        # consumers[p] = how many scheduled nodes will read p's value (covers
        # both computed and cached-leaf deps). Once that many have completed, p
        # is no longer needed and its (possibly large) value is dropped from
        # prepared.values to bound peak memory on wide plans.
        consumers: dict[NodeId, int] = defaultdict(int)
        # `scheduled` is the set of nodes the executor will compute. It starts as
        # to_compute and grows as runtime loop expansion splices in new nodes.
        scheduled: set[NodeId] = set(to_compute)
        # `pinned` values must not be evicted: a closure holds its captured nodes
        # by id, and a for_loop re-reads those captures when it is expanded — which
        # can happen long after the capture's last ordinary consumer has run.
        pinned: set[NodeId] = set()

        def pin_closures(node_ids) -> None:
            for cid in node_ids:
                cnode = prepared.plan.nodes.get(cid)
                if cnode is not None and cnode.kind == "closure":
                    pinned.update(self._get_all_deps(cnode))

        first_error: BaseException | None = None
        loop = asyncio.get_running_loop()

        # LIFO ready queue + a fixed pool of workers. LIFO drives each pipeline
        # depth-first to completion before spreading out, so the live (computed
        # but not-yet-consumed) frontier — and thus peak memory — stays bounded
        # by the worker count rather than the plan width. The worker count is
        # the concurrency cap; ITK kernels run in the thread pool. See #20.
        ready_queue: asyncio.LifoQueue[NodeId] = asyncio.LifoQueue()

        def register(rid: NodeId) -> bool:
            """Add one node to the dependency bookkeeping; return True if ready now.

            A dependency gates readiness only if it is itself scheduled for
            computation; deps already in prepared.values or available as cached
            leaves (loaded on demand by the worker) do not gate.
            """
            cnt = 0
            for dep in self._get_all_deps(prepared.plan.nodes[rid]):
                consumers[dep] += 1
                if dep in prepared.values:
                    continue  # already available
                if dep in scheduled:
                    if dep in prepared.completed_nodes:
                        # Finished earlier and evicted; it will never release a
                        # new dependent, so bring its value back instead of gating.
                        # Pin it so a further expansion can reuse it without churn.
                        rematerialize(dep)
                        pinned.add(dep)
                    else:
                        cnt += 1
                        dependents[dep].append(rid)
                # else: a cached leaf, loaded on demand by the worker.
            pending[rid] = cnt
            return cnt == 0

        pin_closures(to_compute)
        for nid in to_compute:
            if register(nid):
                ready_queue.put_nowait(nid)

        if ready_queue.empty():
            return

        # for_loop nodes awaiting their spliced `sequence` result: alias[for_loop] = seq_id.
        alias: dict[NodeId, NodeId] = {}

        def release(dep: NodeId) -> None:
            """Drop one consumer of `dep`; evict its value once none remain."""
            remaining = consumers.get(dep, 0)
            if remaining > 0:
                consumers[dep] = remaining - 1
                if consumers[dep] == 0 and dep not in goal_set and dep not in pinned:
                    prepared.values.pop(dep, None)
                    if prepared.materialization_store is not None:
                        prepared.materialization_store.forget(dep)

        def rematerialize(dep: NodeId) -> Any:
            """Recompute a node whose value was evicted but is needed again.

            Dynamic expansion can splice a body that references, by id, a
            loop-invariant node already finished and evicted earlier in the run.
            Such a node will never finish again, so gating on it would deadlock;
            instead we synchronously recompute it (in the event loop) and put it
            back into prepared.values. Recursion bottoms out at live or constant
            nodes; results are cheap for the loop-invariant subexpressions that
            trigger this (constants, small scalars), occasionally an image.
            """
            if dep in prepared.values:
                return prepared.values[dep]
            cached = self.cache_lookup(prepared, dep)
            if cached is not None:
                prepared.values[dep] = cached
                return cached
            dn = prepared.plan.nodes[dep]
            if dn.kind == "constant":
                value = dn.attrs.get("value")
            elif dn.kind == "closure":
                for child in self._get_all_deps(dn):
                    rematerialize(child)
                value = self._build_runtime_closure_from_values_eager(prepared, dn)
            else:
                for child in self._get_all_deps(dn):
                    rematerialize(child)
                value = self._compute_primitive(prepared, dep)
            prepared.values[dep] = value
            return value

        def finish(nid: NodeId, node: NodeSpec, value: Any) -> None:
            """Event-loop bookkeeping after a node's value is known."""
            prepared.values[nid] = value

            if node.kind == "primitive":
                if node.operator in _LAZY_SEQUENCE_OPERATORS:
                    self.cache(prepared, nid, value)
                    for i, item in enumerate(value):
                        self.cache_sequence_item(prepared, nid, i, item)
                else:
                    self.cache(prepared, nid, value)
            else:
                # Constants/closures are not persisted; just record progress.
                prepared.completed_nodes.add(nid)
                if self._progress is not None:
                    self._progress.set_postfix_str(node.operator, refresh=False)
                    self._progress.update(1)

            # This node has consumed its dependencies; free any whose last
            # consumer has now run, from both prepared.values and the memo tier,
            # so peak memory tracks the live frontier rather than the whole plan.
            for dep in self._get_all_deps(node):
                release(dep)

            # Enable children whose dependencies are now all satisfied.
            for child_id in dependents[nid]:
                pending[child_id] -= 1
                if pending[child_id] == 0:
                    ready_queue.put_nowait(child_id)

        def try_expand(nid: NodeId, node: NodeSpec) -> bool:
            """Dynamically unroll a for_loop node and splice the bodies into the
            scheduler. Returns True if expanded (the node now awaits its spliced
            `sequence` result), False to fall back to the sequential kernel."""
            if not _DYNAMIC_EXPANSION or node.operator not in _DYNAMIC_EXPANSION_OPERATORS:
                return False
            if len(node.args) != 2:
                return False
            try:
                result = self._expand_for_loop(prepared, nid, node)
            except Exception:  # noqa: BLE001 — any failure falls back to the sequential kernel
                result = None
            if result is None:
                return False
            seq_id, new_ids = result
            # Register every spliced node, then queue the ones that are ready.
            scheduled.update(new_ids)
            pin_closures(new_ids)
            for rid in new_ids:
                if register(rid):
                    ready_queue.put_nowait(rid)
            # The for_loop node now forwards the spliced sequence's value: wait on it.
            alias[nid] = seq_id
            consumers[seq_id] += 1
            if seq_id in scheduled and seq_id not in prepared.values:
                pending[nid] = 1
                dependents[seq_id].append(nid)
            else:
                pending[nid] = 0
                ready_queue.put_nowait(nid)
            return True

        async def worker() -> None:
            nonlocal first_error
            while True:
                nid = await ready_queue.get()
                try:
                    if first_error is None:
                        node = prepared.plan.nodes[nid]
                        if nid in alias:
                            # Spliced for_loop: forward the sequence value, then free it.
                            seq_id = alias.pop(nid)
                            value = prepared.values[seq_id]
                            finish(nid, node, value)
                            release(seq_id)  # the for_loop was seq_id's consumer
                        elif node.kind == "primitive" and try_expand(nid, node):
                            pass  # expanded; node will run again via its alias
                        else:
                            # Load any cached-leaf dependencies on demand, in the event
                            # loop (keeps prepared.values single-writer). Missing deps
                            # are exactly the cached subtrees pruned in Step 1.
                            for dep in self._get_all_deps(node):
                                if dep not in prepared.values:
                                    prepared.values[dep] = self.cache_lookup(prepared, dep)
                            if node.kind == "constant":
                                value = node.attrs.get("value")
                            elif node.kind == "closure":
                                value = self._build_runtime_closure_from_values_eager(prepared, node)
                            else:
                                # ITK kernel: run in thread pool (GIL released → real parallelism).
                                value = await loop.run_in_executor(_executor, self._compute_primitive, prepared, nid)
                            finish(nid, node, value)
                except Exception as exc:  # noqa: BLE001
                    if first_error is None:
                        first_error = exc
                finally:
                    ready_queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(_MAX_CONCURRENCY)]
        try:
            await ready_queue.join()
        finally:
            for w in workers:
                w.cancel()

        if first_error is not None:
            raise first_error

    # ── End async executor ───────────────────────────────────────────────────────────────────

    def _build_runtime_closure_from_values(self, prepared: PreparedPlan, node: NodeSpec) -> RuntimeClosure:
        body = parse_expression_content(str(node.attrs.get("body", "")))
        parameter = str(node.attrs.get("parameter", "arg"))
        capture_names = list(node.attrs.get("capture_names", []))

        captures = {
            name: self._evaluate_node_lazy(prepared,node_id,FullDemand())
            for name, node_id in zip(capture_names, node.args, strict=True)
        }

        for name, spec in dict(node.attrs.get("function_captures", {})).items():
            captures[name] = self._build_runtime_function_from_values(prepared, spec)

        return RuntimeClosure(parameter=parameter, body_expression=body, captures=captures, evaluator=self)


    def _build_runtime_function_from_values(self, prepared: PreparedPlan, spec: dict[str, Any]) -> RuntimeFunction:
        expression = parse_expression_content(str(spec["body"]))
        captures = {
            name: self._evaluate_node_lazy(prepared,node_id,FullDemand())
            for name, node_id in dict(spec.get("captures", {})).items()
        }
        for name, nested_spec in dict(spec.get("functions", {})).items():
            captures[name] = self._build_runtime_function_from_values(prepared, nested_spec)
        return RuntimeFunction(
            parameters=list(spec.get("parameters", [])),
            expression=expression,
            captures=captures,
            evaluator=self,
        )

    def _execute_primitive(self, prepared: PreparedPlan, node: NodeSpec) -> Any:
        """Load the primitive kernel, materialize inputs, and invoke it."""
        kernel = self.registry.load_kernel(node.operator)
        args = [prepared.values[arg_id] for arg_id in node.args]
        kwargs = {key: prepared.values[value_id] for key, value_id in node.kwargs}
        return self._invoke_kernel(kernel, args, kwargs, node.attrs)

    def _ensure_node_value(self, prepared: PreparedPlan, node_id: NodeId) -> Any:
        if node_id in prepared.values:
            return prepared.values[node_id]
        for current_id, node in prepared.plan.nodes.items():
            if current_id in prepared.values:
                continue
            value = self._evaluate_node_sequential(prepared, node)
            prepared.values[current_id] = value
            prepared.completed_nodes.add(current_id)
            if current_id == node_id:
                return value
        raise KeyError(f"Node not found in prepared plan: {node_id}")

    def _evaluate_runtime_expression(self, expression: Expression, env: dict[str, Any]) -> Any:
        """Evaluate deferred closure bodies using the same primitive runtime."""
        if isinstance(expression, ENumber):
            return expression.value
        if isinstance(expression, EArray):
            return [self._evaluate_runtime_expression(item, env) for item in expression.items]
        if isinstance(expression, EBool):
            return expression.value
        if isinstance(expression, EString):
            return expression.value
        if isinstance(expression, ECall):
            if not expression.arguments:
                if expression.identifier in env:
                    return env[expression.identifier]
                kernel = self.registry.load_kernel(expression.identifier)
                return self._invoke_kernel(kernel, [], {})
            arg_values = [self._evaluate_runtime_expression(arg, env) for arg in expression.arguments]
            # Closure bodies may call captured user functions or globally
            # registered primitives, so resolution checks the environment first.
            function_value = env.get(expression.identifier)
            if isinstance(function_value, RuntimeFunction):
                return function_value.invoke(arg_values)
            kernel = self.registry.load_kernel(expression.identifier)
            return self._invoke_kernel(kernel, arg_values, {})
        if isinstance(expression, ESlice):
            sequence_value = self._evaluate_runtime_expression(expression.sequence, env)
            start_value = self._evaluate_runtime_expression(expression.start, env) if expression.start is not None else None
            stop_value = self._evaluate_runtime_expression(expression.stop, env) if expression.stop is not None else None
            kernel = self.registry.load_kernel("slice")
            return self._invoke_kernel(kernel, [sequence_value, start_value, stop_value], {})
        if isinstance(expression, ELet):
            value = self._evaluate_runtime_expression(expression.value, env)
            next_env = dict(env)
            next_env[expression.variable] = value
            return self._evaluate_runtime_expression(expression.body, next_env)
        if isinstance(expression, EFor):
            sequence = self._coerce_sequence(self._evaluate_runtime_expression(expression.iterable, env))
            closure = RuntimeClosure(
                parameter=expression.variable,
                body_expression=expression.body,
                captures=env,
                evaluator=self,
            )
            return [closure.apply(item) for item in sequence.iter_values()]
        if isinstance(expression, EFilter):
            sequence = self._coerce_sequence(self._evaluate_runtime_expression(expression.iterable, env))
            closure = RuntimeClosure(
                parameter=expression.variable,
                body_expression=expression.predicate,
                captures=env,
                evaluator=self,
            )
            return [
                item
                for item in sequence.iter_values()
                if bool(closure.apply(item))
            ]
        if isinstance(expression, EFold):
            from voxlogica.primitives.default.fold import fold_sequence

            sequence = self._evaluate_runtime_expression(expression.sequence, env)
            init = (
                None
                if expression.init is None
                else self._evaluate_runtime_expression(expression.init, env)
            )
            return fold_sequence(expression.operator, init, sequence)
        raise ValueError(f"Unsupported runtime expression: {type(expression).__name__}")

    def _coerce_sequence(self, value: Any) -> SequenceValue:
        if isinstance(value, SequenceValue):
            return value
        if isinstance(value, (list, tuple, range)):
            return SequenceValue.from_iterable(value)
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray, dict)):
            return SequenceValue.from_iterable(value)
        raise ValueError(f"Value is not a sequence: {type(value).__name__}")

    def _invoke_kernel(
        self,
        kernel,
        args: list[Any],
        kwargs: dict[str, Any],
        attrs: dict[str, Any] | None = None,
    ) -> Any:
        """Adapt engine arguments to the kernel's declared Python signature."""
        signature = inspect.signature(kernel)
        params = list(signature.parameters.values())
        has_varkw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params)
        has_varargs = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params)

        if has_varkw:
            payload = {str(index): value for index, value in enumerate(args)}
            payload.update(kwargs)
            if attrs:
                payload.update(attrs)
            return kernel(**payload)
        if has_varargs:
            return kernel(*args, **kwargs)

        bound_kwargs = dict(kwargs)
        for index, value in enumerate(args):
            if index >= len(params):
                raise ValueError(f"Kernel received too many positional arguments: {len(args)}")
            param = params[index]
            if param.name not in bound_kwargs:
                bound_kwargs[param.name] = value
        return kernel(**bound_kwargs)

    def _run_goal_side_effect(self, operation: str, name: str, value: Any) -> None:
        if operation == "print":
            print(f"{name}={self._materialize_goal_value(value)}")
            return
        if operation == "save":
            self._save_to_file(name, self._materialize_goal_value(value))
            return
        raise ValueError(f"Unknown goal operation: {operation}")

    def _save_to_file(self, filename: str, value: Any) -> None:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        if suffix == ".json":
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
            return
        if suffix in {".pkl", ".pickle", ".bin"}:
            path.write_bytes(pickle.dumps(value))
            return
        path.write_text(str(value), encoding="utf-8")

    def _materialize_goal_value(self, value: Any) -> Any:
        if isinstance(value, SequenceValue):
            return list(value.iter_values())
        return value
