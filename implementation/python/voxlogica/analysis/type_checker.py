"""Type checking as abstract execution of a reduced plan.

The checker walks the very same ``SymbolicPlan`` the engine executes, in the
same demand-driven order, with types in place of values:

======================  ==========================  ==========================
                        engine                      type checker
======================  ==========================  ==========================
evaluates               ``NodeId -> value``         ``NodeId -> VoxType``
per-node work           the primitive's kernel      the primitive's type rule
memo                    ``NodeTable.values``        ``TypeChecker.types``
loop bodies             ``Expander.reduce_chunk``   ``_apply_closure``
======================  ==========================  ==========================

Closures are the one place a plan does not already contain the answer: a
``closure`` node carries its body as *source text*, and the body only becomes
nodes when the loop is unrolled. The engine unrolls it by binding the loop
variable to each element's constant node and re-reducing (``engine/expander``).
The checker does exactly that, once, with the loop variable bound to a **typed
hole** — a synthetic node whose type is the sequence's element type and which
has no value. Reduction then produces a real body sub-DAG that the checker types
like any other, so ``for x in [1,2,3] do f(x)`` is checked at ``f: int -> ...``
rather than abandoned as opaque.

Reduction of a closure body *creates nodes*. Those go into a private copy of the
node table, never into the plan handed to the engine: type checking a plan must
not change what the plan executes.

The analysis is gradual, not sound. A primitive with no declared type rule
yields ``VoxAny``, which is compatible with everything, so the checker only ever
reports a mismatch it can actually prove from declared rules. Adding rules
strictly adds diagnostics; it never rejects a program that used to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from voxlogica.analysis.type_helpers import (
    VoxTypeError,
    element_of,
    infer_literal_type,
)
from voxlogica.analysis.types import (
    VoxAny,
    VoxBool,
    VoxClosure,
    VoxSequence,
    VoxType,
    join,
)
from voxlogica.lazy.ir import NodeId, NodeSpec, SymbolicPlan
from voxlogica.primitives.registry import PrimitiveRegistry

#: Operators whose result depends on applying a closure, so the checker has to
#: handle them itself rather than through a ``TypeRule`` over argument types.
#: Mirrors ``engine.expander._EXPANDABLE`` plus ``filter``, which the expander
#: excludes because it changes a sequence's length but which is typed the same way.
_MAP_OPERATORS = frozenset({"for_loop", "default.for_loop", "map", "default.map"})
_FILTER_OPERATORS = frozenset({"filter", "default.filter"})
_FOLD_OPERATORS = frozenset({"fold", "default.fold"})

#: ``&&`` and ``||`` fold to a truth value whatever the element type is; the
#: arithmetic folds carry the element type through.
_BOOLEAN_FOLD_OPERATORS = frozenset({"&&", "||"})

#: How many closure applications may nest while one goal is typed.
#:
#: This is a *stack* bound, not a termination argument. ``_prime`` linearizes the
#: static DAG so typing it costs no Python stack, but each ``_apply_closure``
#: level opens two fresh recursions it cannot cover: ``reduce_expression`` over
#: the body's AST, and ``check_node`` over the sub-DAG that reduction just
#: created. Capping the nesting keeps the checker's stack use independent of how
#: deeply a program nests its loops.
#:
#: Termination does not depend on it. A closure's body is fixed source text, so
#: the nesting a program can reach is the nesting it is written with; a
#: genuinely recursive user function is inlined by the reducer and diverges
#: there, before any type is asked for. The cap is insurance, and 64 is far
#: below CPython's stack budget while being deeper than any real program nests.
#:
#: Reaching it costs precision, so it is reported (``W_TYPE_DEPTH``) rather than
#: applied silently: a result of ``any`` must not blur "nothing declares this"
#: into "the checker stopped looking".
_DEFAULT_MAX_CLOSURE_DEPTH = 64


@dataclass
class TypeCheckResult:
    """Outcome of one abstract execution of a plan."""

    goal_types: dict[str, VoxType] = field(default_factory=dict)
    node_types: dict[NodeId, VoxType] = field(default_factory=dict)
    diagnostics: list[Any] = field(default_factory=list)
    #: Non-fatal notes: the checker declined to analyse something and widened to
    #: ``any``. They never gate ``ok`` -- a limit of the analysis is not a defect
    #: in the program, and refusing to run one over it would be.
    warnings: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return whether the plan type-checked without a single error."""
        return not self.diagnostics

    def raise_on_error(self) -> None:
        """Raise the collected diagnostics as a ``StaticAnalysisError``, if any."""
        if self.diagnostics:
            from voxlogica.reducer import StaticAnalysisError

            raise StaticAnalysisError(list(self.diagnostics))


class TypeChecker:
    """Abstract interpreter that assigns a ``VoxType`` to every demanded node."""

    def __init__(
        self,
        registry: PrimitiveRegistry | None = None,
        *,
        max_closure_depth: int = _DEFAULT_MAX_CLOSURE_DEPTH,
    ) -> None:
        self.registry = registry or PrimitiveRegistry()
        self.max_closure_depth = max_closure_depth

        # Per-run state, reset by ``check_plan``.
        self._nodes: dict[NodeId, NodeSpec] = {}
        self._provenance: dict[NodeId, tuple[str, ...]] = {}
        self.types: dict[NodeId, VoxType] = {}
        self._diagnostics: list[Any] = []
        self._warnings: list[Any] = []
        self._reported: set[tuple[str, str]] = set()
        self._closure_results: dict[tuple[NodeId, VoxType], VoxType] = {}
        self._in_progress: set[NodeId] = set()
        self._site_locations: list[str] = []
        self._depth = 0

    # ── Entry points ─────────────────────────────────────────────────────────

    def check_plan(self, plan: SymbolicPlan) -> TypeCheckResult:
        """Abstractly execute every goal of a plan and collect the diagnostics.

        The plan is not modified: closure-body reduction writes into a private
        copy of its node table.
        """
        self.registry.apply_imports(plan.imported_namespaces)

        # A copy, because reducing a closure body interns new nodes and the plan
        # the engine is about to run must be exactly the plan that was reduced.
        self._nodes = dict(plan.nodes)
        self._provenance = dict(plan.provenance)
        self.types = {}
        self._diagnostics = []
        self._warnings = []
        self._reported = set()
        self._closure_results = {}
        self._in_progress = set()
        self._site_locations = []
        self._depth = 0

        goal_types: dict[str, VoxType] = {}
        for goal in plan.goals:
            self._prime(goal.id)
            goal_types[goal.name] = self.check_node(goal.id)

        return TypeCheckResult(
            goal_types=goal_types,
            node_types=dict(self.types),
            diagnostics=list(self._diagnostics),
            warnings=list(self._warnings),
        )

    def check(self, prepared: Any) -> TypeCheckResult:
        """Check a ``PreparedPlan`` (or a bare ``SymbolicPlan``)."""
        plan = getattr(prepared, "plan", prepared)
        return self.check_plan(plan)

    # ── The abstract interpreter ─────────────────────────────────────────────

    def _dependencies(self, node: NodeSpec) -> tuple[NodeId, ...]:
        """Every node this one reads, in a deterministic order.

        Includes the ids hidden inside a closure's serialized function captures,
        which are dependencies of the body even though they are not arguments —
        the same set ``Expander.dependencies`` pins for the runtime.
        """
        from voxlogica.engine.expander import Expander

        ordered = list(node.args)
        ordered.extend(arg for _, arg in sorted(node.kwargs))
        ordered.extend(sorted(Expander.function_capture_ids(node.attrs)))
        return tuple(ordered)

    def _prime(self, root: NodeId) -> None:
        """Type everything ``root`` depends on, deepest first, without recursing.

        ``check_node`` is naturally recursive, and a plan's dataflow chains are
        as long as the program is deep; an explicit post-order walk keeps the
        Python stack out of it, so a large plan cannot fail to type-check for a
        reason that has nothing to do with its types. Nodes created later, while
        reducing a closure body, still go through the recursive path — their
        depth is that of one expression, not of the plan.
        """
        entered: set[NodeId] = set()
        stack: list[tuple[NodeId, bool]] = [(root, False)]
        while stack:
            node_id, expanded = stack.pop()
            if node_id in self.types:
                continue
            node = self._nodes.get(node_id)
            if node is None:
                continue
            if expanded:
                self.check_node(node_id)
                continue
            if node_id in entered:
                continue
            entered.add(node_id)
            stack.append((node_id, True))
            for dependency in reversed(self._dependencies(node)):
                if dependency not in self.types:
                    stack.append((dependency, False))

    def check_node(self, node_id: NodeId) -> VoxType:
        """Return the type of one node, computing and memoizing it on demand."""
        cached = self.types.get(node_id)
        if cached is not None:
            return cached

        node = self._nodes.get(node_id)
        if node is None:
            # A dangling reference is an engine-level defect, not a type error;
            # the executor reports it far better than the checker could.
            return VoxAny()

        if node_id in self._in_progress:
            # Only reachable through a malformed cyclic table; refuse to loop.
            return VoxAny()

        self._in_progress.add(node_id)
        try:
            result = self._check_node_uncached(node_id, node)
        finally:
            self._in_progress.discard(node_id)

        self.types[node_id] = result
        return result

    def _check_node_uncached(self, node_id: NodeId, node: NodeSpec) -> VoxType:
        """Dispatch on node kind; every branch returns a type, never raises."""
        if node.kind == "constant":
            return infer_literal_type(node.attrs.get("value"))

        if node.kind == "closure":
            # A closure's precise type only exists relative to an argument type,
            # which the applying primitive supplies (see ``_apply_closure``).
            return VoxClosure(VoxAny(), VoxAny())

        if node.kind == "primitive":
            return self._check_primitive(node_id, node)

        return VoxAny()

    def _check_primitive(self, node_id: NodeId, node: NodeSpec) -> VoxType:
        """Type a primitive node through its rule, or natively if higher-order."""
        operator = node.operator

        if operator in _MAP_OPERATORS:
            return self._check_map(node_id, node)
        if operator in _FILTER_OPERATORS:
            return self._check_filter(node_id, node)
        if operator in _FOLD_OPERATORS:
            return self._check_fold(node_id, node)

        argument_types = self._argument_types(node)

        try:
            rule = self.registry.load_type(operator)
        except KeyError:
            # Either the primitive is unknown (the reducer already rejected that
            # case) or it declares no rule: nothing to check, nothing to claim.
            return VoxAny()

        try:
            return rule(argument_types)
        except VoxTypeError as exc:
            self._report(node_id, operator, str(exc))
            return VoxAny()

    def _argument_types(self, node: NodeSpec) -> list[VoxType]:
        """Return the node's argument types in kernel-call order.

        Positional arguments first, then keyword arguments by key. Legacy
        kernels receive positional arguments as the keys ``"0"``, ``"1"``, ...,
        so numeric keys sort numerically rather than lexicographically.
        """
        types = [self.check_node(arg) for arg in node.args]
        if node.kwargs:
            ordered = sorted(
                node.kwargs,
                key=lambda item: (0, int(item[0]), "") if item[0].isdigit() else (1, 0, item[0]),
            )
            types.extend(self.check_node(arg) for _, arg in ordered)
        return types

    # ── Higher-order primitives ──────────────────────────────────────────────

    def _sequence_and_closure(self, node: NodeSpec) -> tuple[NodeId, NodeId] | None:
        """Return ``(sequence_id, closure_id)`` for a closure-over-sequence node.

        The lazy sequence-access rewrite appends a literal ``(start, stop)``
        window to these nodes; the extra arguments do not change the types.
        """
        if len(node.args) < 2:
            return None
        return node.args[0], node.args[1]

    def _element_type(self, node_id: NodeId, operator: str, sequence_id: NodeId) -> VoxType:
        """Return the element type of an argument that must be a sequence."""
        sequence_type = self.check_node(sequence_id)
        element = element_of(sequence_type)
        if element is None:
            self._report(
                node_id,
                operator,
                f"argument 0 has type {sequence_type}, expected a sequence",
            )
            return VoxAny()
        return element

    def _check_map(self, node_id: NodeId, node: NodeSpec) -> VoxType:
        """``for_loop``/``map``: apply the closure at the element type."""
        pair = self._sequence_and_closure(node)
        if pair is None:
            return VoxSequence(VoxAny())
        sequence_id, closure_id = pair
        element = self._element_type(node_id, node.operator, sequence_id)
        return VoxSequence(self._apply_closure(closure_id, element, site=node_id))

    def _check_filter(self, node_id: NodeId, node: NodeSpec) -> VoxType:
        """``filter``: the predicate is applied but the element type survives."""
        pair = self._sequence_and_closure(node)
        if pair is None:
            return VoxSequence(VoxAny())
        sequence_id, closure_id = pair
        element = self._element_type(node_id, node.operator, sequence_id)
        # Applied for its diagnostics: a predicate body that does not type-check
        # is an error whether or not the result type depends on it.
        self._apply_closure(closure_id, element, site=node_id)
        return VoxSequence(element)

    def _check_fold(self, node_id: NodeId, node: NodeSpec) -> VoxType:
        """``fold``: a built-in combiner over a sequence, with an optional seed."""
        operator = str(node.attrs.get("operator", ""))
        if not node.args:
            return VoxAny()

        if len(node.args) == 1:
            sequence_id, initial_type = node.args[0], None
        else:
            initial_type = self.check_node(node.args[0])
            sequence_id = node.args[1]

        element = self._element_type(node_id, node.operator, sequence_id)
        if operator in _BOOLEAN_FOLD_OPERATORS:
            return VoxBool()
        return element if initial_type is None else join(element, initial_type)

    def _apply_closure(
        self, closure_id: NodeId, argument_type: VoxType, *, site: NodeId
    ) -> VoxType:
        """Type a closure body at one argument type by reducing it abstractly.

        Binds the closure's parameter to a *typed hole* — a node with a type but
        no value — and runs the reducer over the body exactly as the expander
        does per element, then types the sub-DAG that comes back.

        ``site`` is the applying node, which carries the source location; a
        closure node has none of its own, so diagnostics from inside a body are
        anchored at the loop that runs it.
        """
        memo_key = (closure_id, argument_type)
        cached = self._closure_results.get(memo_key)
        if cached is not None:
            return cached

        closure = self._nodes.get(closure_id)
        if closure is None or closure.kind != "closure":
            # A ``for`` over a non-closure second argument is not expressible
            # from source; treat it as unknown rather than inventing an error.
            return VoxAny()

        if self._depth >= self.max_closure_depth:
            self._warn(
                site,
                "closure",
                f"stopped analysing at {self.max_closure_depth} nested closure "
                "applications; this loop body's result is reported as 'any' "
                "rather than analysed further",
            )
            return VoxAny()

        from voxlogica.engine.expander import closure_environment
        from voxlogica.parser import parse_expression_content
        from voxlogica.reducer import OperationVal, WorkPlan

        parameter = str(closure.attrs.get("parameter", "arg"))
        hole_id = self._typed_hole(argument_type)

        site_location = self._location(site)
        if site_location is not None:
            self._site_locations.append(site_location)
        self._depth += 1
        try:
            from voxlogica.reducer import reduce_expression

            body_ast = parse_expression_content(str(closure.attrs.get("body", "")))
            # ``for_expansion_cap=0`` keeps a nested loop over a constant
            # sequence as a ``for_loop`` node instead of unrolling it: the
            # checker types every iteration at once through the element
            # type, so unrolling would only cost time.
            # The scratch plan keeps its own provenance and it is discarded:
            # the body is re-parsed from text, so its positions count from the
            # start of the body, not of the file. Reporting them would look like
            # a file offset and point somewhere else entirely. Diagnostics from
            # inside a body are anchored at the enclosing loop instead, through
            # ``_site_locations``.
            work_plan = WorkPlan(nodes=self._nodes, registry=self.registry,
                                 for_expansion_cap=0)
            environment = closure_environment(closure).bind(parameter, OperationVal(hole_id))
            body_id = reduce_expression(environment, work_plan, body_ast)
        except Exception as exc:  # noqa: BLE001 - reduction failure is reported, not raised
            # The reducer already rejects unbound identifiers and bad arities at
            # plan time for non-closure code; inside a body it only gets the
            # chance here, so surface it as a diagnostic instead of a crash.
            self._report(site, "closure", f"cannot analyse loop body: {exc}")
            result: VoxType = VoxAny()
        else:
            result = self.check_node(body_id)
        finally:
            self._depth -= 1
            if site_location is not None:
                self._site_locations.pop()

        self._closure_results[memo_key] = result
        return result

    def _typed_hole(self, argument_type: VoxType) -> NodeId:
        """Intern a synthetic node standing for "some value of this type".

        Distinct types get distinct holes (the type is part of the node's attrs,
        hence of its hash), so a body reduced at ``int`` and the same body
        reduced at ``image`` do not collide in the hash-consed table.
        """
        from voxlogica.lazy.hash import hash_node

        node = NodeSpec(
            kind="constant",
            operator="type_hole",
            attrs={"type": str(argument_type)},
            output_kind="unknown",
        )
        node_id = hash_node(node)
        self._nodes.setdefault(node_id, node)
        self.types[node_id] = argument_type
        return node_id

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def _warn(self, node_id: NodeId, symbol: str, message: str) -> None:
        """Record one non-fatal note, de-duplicated the same way as an error."""
        diagnostic = self._diagnostic(node_id, "W_TYPE_DEPTH", symbol, message)
        if diagnostic is not None:
            self._warnings.append(diagnostic)

    def _location(self, node_id: NodeId) -> str | None:
        """Return the node's own source location, if the plan recorded one."""
        locations = self._provenance.get(node_id, ())
        return locations[0] if locations else None

    def _diagnostic(self, node_id: NodeId, code: str, symbol: str, message: str):
        """Build one located diagnostic, or ``None`` if it was already recorded.

        Hash-consing means one source expression is one node however many times
        it is written, but a plan can still reach the same faulty node from
        several goals; reporting it once is what the reader wants.
        """
        from voxlogica.reducer import StaticDiagnostic

        # A node interned while reducing a loop body has no location of its own
        # (see ``_apply_closure``), so it inherits the enclosing loop's.
        location = self._location(node_id)
        if location is None and self._site_locations:
            location = self._site_locations[-1]
        key = (location or node_id, message)
        if key in self._reported:
            return None
        self._reported.add(key)
        return StaticDiagnostic(
            code=code,
            message=f"Type error in '{symbol}': {message}"
            if code == "E_TYPE"
            else f"Type analysis limit in '{symbol}': {message}",
            location=location,
            symbol=symbol,
        )

    def _report(self, node_id: NodeId, symbol: str, message: str) -> None:
        """Record one type error, de-duplicated by (location, message)."""
        diagnostic = self._diagnostic(node_id, "E_TYPE", symbol, message)
        if diagnostic is not None:
            self._diagnostics.append(diagnostic)


def check_plan(
    plan: SymbolicPlan | Any,
    registry: PrimitiveRegistry | None = None,
) -> TypeCheckResult:
    """Abstractly execute ``plan`` and return its types and diagnostics."""
    return TypeChecker(registry).check(plan)
