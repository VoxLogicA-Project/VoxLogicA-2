"""Reducer from parsed AST to symbolic DAG.

This module bridges parsing and execution. It resolves lexical bindings,
expands imports, lowers language constructs to primitive calls, and emits a
deterministic symbolic graph where identical nodes collapse to the same id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence
import logging
import os

from voxlogica.lazy import GoalSpec, NodeId, NodeSpec, SymbolicPlan
from voxlogica.lazy.ir import OutputKind
from voxlogica.lazy.hash import hash_node
from voxlogica.parser import (
    Command,
    Declaration,
    EArray,
    EBool,
    ECall,
    EFilter,
    EFold,
    EFor,
    ELet,
    ENumber,
    ESlice,
    EString,
    Expression,
    Import,
    Print,
    Program,
    Save,
    parse_import,
    parse_program_content,
)
from voxlogica.primitives.api import PrimitiveCall
from voxlogica.primitives.registry import PrimitiveRegistry
from voxlogica.program_context import PROGRAM_SYSVAR_NAMES, ProgramContext
logger = logging.getLogger(__name__)

identifier = str
Stack = list[tuple[str, str]]

# A `for x in <iterable> do <body>` whose iterable is a compile-time-known constant
# sequence of length <= this cap is expanded into one DAG node per element, so the
# async executor can evaluate the bodies in parallel instead of running them
# sequentially inside a single for_loop kernel. Longer literal lists, and all
# range()/dir() iterables, fall back to the lazy for_loop node. Override with
# VOXLOGICA_FOR_EXPANSION_CAP=0 to disable expansion entirely.
_FOR_EXPANSION_CAP = int(os.environ.get("VOXLOGICA_FOR_EXPANSION_CAP", "4096"))

_PRIMITIVE_OPERATOR_ALIASES: dict[str, str] = {
    "!": "not_compat",
    "&&": "bool_and_scalar",
    "||": "bool_or_scalar",
}


class StaticDiagnostic(Exception):
    """Structured reducer diagnostic."""

    def __init__(self, code: str, message: str, location: str | None = None, symbol: str | None = None):
        """Capture a machine-readable static analysis failure."""
        self.code = code
        self.message = message
        self.location = location
        self.symbol = symbol
        super().__init__(message)


class StaticAnalysisError(RuntimeError):
    """Raised when static resolution fails before execution."""

    def __init__(self, diagnostics: list[StaticDiagnostic]):
        """Bundle one or more reducer diagnostics into a raised exception."""
        self.diagnostics = tuple(diagnostics)
        message = diagnostics[0].message if diagnostics else "Static analysis failed"
        super().__init__(message)

    def format_block(self) -> str:
        """Render diagnostics in a CLI-friendly multi-line block."""
        lines: list[str] = []
        for diagnostic in self.diagnostics:
            prefix = diagnostic.code
            if diagnostic.location:
                prefix = f"{prefix} at {diagnostic.location}"
            lines.append(f"{prefix}: {diagnostic.message}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ConstantValue:
    """Compatibility view for constant nodes."""

    value: Any


@dataclass(frozen=True)
class Operation:
    """Compatibility view for primitive nodes."""

    operator: str
    arguments: dict[str, NodeId]
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosureValue:
    """Compatibility view for closure nodes."""

    variable: str
    body: str
    captures: tuple[tuple[str, NodeId], ...]


Goal = GoalSpec
Node = NodeSpec


@dataclass
class WorkPlan:
    """Mutable reducer output before it is frozen into a ``SymbolicPlan``."""

    nodes: dict[NodeId, NodeSpec] = field(default_factory=dict)
    goals: list[GoalSpec] = field(default_factory=list)
    imported_namespaces: list[str] = field(default_factory=list)
    registry: PrimitiveRegistry = field(default_factory=PrimitiveRegistry, repr=False)

    def add_node(self, node: NodeSpec) -> NodeId:
        """Hash-cons a node and return the stable id assigned to it."""
        node_id = hash_node(node)
        if node_id not in self.nodes:
            self.nodes[node_id] = node
        return node_id

    def add_goal(self, operation: str, operation_id: NodeId, name: str) -> None:
        """Record a top-level materialization goal such as print or save."""
        self.goals.append(GoalSpec(operation=operation, id=operation_id, name=name))

    def import_namespace(self, namespace: str) -> None:
        """Import one primitive namespace and track it in reducer order."""
        self.registry.import_namespace(namespace)
        if namespace not in self.imported_namespaces:
            self.imported_namespaces.append(namespace)

    @property
    def operations(self) -> dict[NodeId, Operation]:
        """Expose primitive nodes through the older ``operations`` view."""
        output: dict[NodeId, Operation] = {}
        for node_id, node in self.nodes.items():
            if node.kind != "primitive":
                continue
            arguments = {str(index): arg for index, arg in enumerate(node.args)}
            arguments.update(dict(node.kwargs))
            output[node_id] = Operation(
                operator=node.operator,
                arguments=arguments,
                attrs=dict(node.attrs),
            )
        return output

    @property
    def constants(self) -> dict[NodeId, ConstantValue]:
        """Expose constant nodes through the older ``constants`` view."""
        return {
            node_id: ConstantValue(value=node.attrs.get("value"))
            for node_id, node in self.nodes.items()
            if node.kind == "constant"
        }

    @property
    def closures(self) -> dict[NodeId, ClosureValue]:
        """Expose closure nodes through the older ``closures`` view."""
        output: dict[NodeId, ClosureValue] = {}
        for node_id, node in self.nodes.items():
            if node.kind != "closure":
                continue
            output[node_id] = ClosureValue(
                variable=str(node.attrs.get("parameter", "")),
                body=str(node.attrs.get("body", "")),
                captures=tuple(
                    zip(
                        tuple(node.attrs.get("capture_names", ())),
                        node.args,
                        strict=False,
                    )
                ),
            )
        return output

    def to_symbolic_plan(self) -> SymbolicPlan:
        """Freeze the mutable reducer state into the immutable execution IR."""
        return SymbolicPlan(
            nodes=dict(self.nodes),
            goals=list(self.goals),
            imported_namespaces=tuple(self.imported_namespaces),
        )

    @property
    def definition_store(self) -> dict[NodeId, NodeSpec]:
        """Return the raw node dictionary for compatibility with old callers."""
        return self.nodes

    def __str__(self) -> str:
        """Return a short human-readable summary of the work plan."""
        return (
            "WorkPlan(" 
            f"nodes={len(self.nodes)}, goals={len(self.goals)}, "
            f"imports={self.imported_namespaces}"
            ")"
        )


@dataclass(frozen=True)
class OperationVal:
    """Symbolic ref in environment."""

    operation_id: NodeId


@dataclass(frozen=True)
class FunctionVal:
    """Function value used for declaration-level closures."""

    environment: "Environment"
    parameters: list[identifier]
    expression: Expression


DVal = OperationVal | FunctionVal


class Environment:
    """Lexical environment for reducer bindings."""

    def __init__(self, bindings: Optional[dict[identifier, DVal]] = None):
        """Create an environment from an optional binding map."""
        self.bindings = bindings or {}

    def try_find(self, ide: identifier) -> Optional[DVal]:
        """Look up one identifier, returning ``None`` when it is unbound."""
        return self.bindings.get(ide)

    def bind(self, ide: identifier, expr: DVal) -> "Environment":
        """Return a new environment extended with one additional binding."""
        new_bindings = dict(self.bindings)
        new_bindings[ide] = expr
        return Environment(new_bindings)

    def bind_list(self, ide_list: list[identifier], expr_list: Sequence[DVal]) -> "Environment":
        """Return a new environment extended by multiple aligned bindings."""
        if len(ide_list) != len(expr_list):
            raise RuntimeError("Reducer internal error: arity mismatch")

        env = self
        for ide, expr in zip(ide_list, expr_list, strict=True):
            env = env.bind(ide, expr)

        return env


def _collect_referenced_variables(expr: Expression) -> set[str]:
    """Collect free variable names referenced by one expression tree."""
    if isinstance(expr, EArray):
        refs: set[str] = set()
        for item in expr.items:
            refs.update(_collect_referenced_variables(item))
        return refs

    if isinstance(expr, ESlice):
        refs = _collect_referenced_variables(expr.sequence)
        if expr.start is not None:
            refs.update(_collect_referenced_variables(expr.start))
        if expr.stop is not None:
            refs.update(_collect_referenced_variables(expr.stop))
        return refs

    if isinstance(expr, ECall):
        refs = {expr.identifier}
        for arg in expr.arguments:
            refs.update(_collect_referenced_variables(arg))
        return refs

    if isinstance(expr, EFor):
        refs = _collect_referenced_variables(expr.iterable)
        body_refs = _collect_referenced_variables(expr.body)
        body_refs.discard(expr.variable)
        refs.update(body_refs)
        return refs

    if isinstance(expr, EFilter):
        refs = _collect_referenced_variables(expr.iterable)
        predicate_refs = _collect_referenced_variables(expr.predicate)
        predicate_refs.discard(expr.variable)
        refs.update(predicate_refs)
        return refs

    if isinstance(expr, EFold):
        refs = _collect_referenced_variables(expr.sequence)
        if expr.init is not None:
            refs.update(_collect_referenced_variables(expr.init))
        return refs

    if isinstance(expr, ELet):
        refs = _collect_referenced_variables(expr.value)
        body_refs = _collect_referenced_variables(expr.body)
        body_refs.discard(expr.variable)
        refs.update(body_refs)
        return refs

    return set()


def _raise_reserved_program_identifier(identifier: str) -> None:
    """Reject user declarations that shadow built-in program variables."""
    if identifier not in PROGRAM_SYSVAR_NAMES:
        return
    raise StaticAnalysisError(
        [
            StaticDiagnostic(
                code="E_RESERVED_PROGRAM_VARIABLE",
                message=f"Identifier {identifier!r} is reserved for built-in program metadata",
                symbol=identifier,
            )
        ]
    )


def _seed_program_variables(
    env: Environment,
    work_plan: WorkPlan,
    source_name: str,
) -> Environment:
    """Inject $cwd, $stem, and related bindings before user declarations run."""
    context = ProgramContext.from_source_name(source_name)
    for name, value in context.bindings().items():
        if env.try_find(name) is not None:
            continue
        node_id = _create_constant_node(work_plan, value)
        env = env.bind(name, OperationVal(node_id))
    return env


def _create_constant_node(work_plan: WorkPlan, value: Any) -> NodeId:
    """Intern one constant into the plan and return its stable node id."""
    return work_plan.add_node(
        NodeSpec(
            kind="constant",
            operator="constant",
            attrs={"value": value},
            output_kind="scalar",
        )
    )


def _normalize_primitive_identifier(identifier: str) -> str:
    """Map operator syntax to primitive names and normalize trivial whitespace."""
    normalized = str(identifier or "").strip()
    return _PRIMITIVE_OPERATOR_ALIASES.get(normalized, normalized)


def _format_call_stack(call_stack: Stack) -> str:
    lines = [f"  {name} at {position}" for name, position in call_stack if name]
    return "\n".join(lines)


def _raise_unresolved_identifier(
    identifier: str,
    *,
    position: str | None,
    call_stack: Stack = (),
) -> None:
    """Raise when an identifier is neither bound in the environment nor a primitive."""
    normalized = _normalize_primitive_identifier(identifier)
    message = f"Unbound variable '{normalized}'"
    stack_text = _format_call_stack(call_stack)
    if stack_text:
        message = f"{message}\nCall chain:\n{stack_text}"

    raise StaticAnalysisError(
        [
            StaticDiagnostic(
                code="E_UNBOUND_IDENTIFIER",
                message=message,
                location=position,
                symbol=normalized,
            )
        ]
    )


def _plan_call_not_in_env(
    work_plan: WorkPlan,
    identifier: str,
    *,
    position: str | None,
    call_stack: Stack,
    args: tuple[NodeId, ...],
    kwargs: tuple[tuple[str, NodeId], ...] = (),
    output_kind: OutputKind = "scalar",
) -> NodeId:
    """Plan a primitive call when the callee has no lexical binding."""
    normalized = _normalize_primitive_identifier(identifier)
    try:
        work_plan.registry.get_spec(normalized)
    except KeyError:
        _raise_unresolved_identifier(
            identifier,
            position=position,
            call_stack=call_stack,
        )
    return _plan_primitive_call(
        work_plan,
        identifier=identifier,
        args=args,
        kwargs=kwargs,
        output_kind=output_kind,
        position=position,
    )


def _serialize_function_capture(
    name: str,
    function_value: FunctionVal,
    seen: set[str],
) -> dict[str, Any]:
    """Serialize a captured function into closure metadata for runtime rebuild."""
    if name in seen:
        return {
            "parameters": list(function_value.parameters),
            "body": function_value.expression.to_syntax(),
            "captures": {},
            "functions": {},
        }

    next_seen = set(seen)
    next_seen.add(name)

    capture_refs: dict[str, NodeId] = {}
    nested_functions: dict[str, dict[str, Any]] = {}

    referenced = _collect_referenced_variables(function_value.expression)
    for variable in function_value.parameters:
        referenced.discard(variable)

    for referenced_name in sorted(referenced):
        binding = function_value.environment.try_find(referenced_name)
        if isinstance(binding, OperationVal):
            capture_refs[referenced_name] = binding.operation_id
        elif isinstance(binding, FunctionVal):
            nested_functions[referenced_name] = _serialize_function_capture(
                referenced_name,
                binding,
                next_seen,
            )

    return {
        "parameters": list(function_value.parameters),
        "body": function_value.expression.to_syntax(),
        "captures": capture_refs,
        "functions": nested_functions,
    }


def _create_closure_node(
    variable: str,
    expression: Expression,
    environment: Environment,
    work_plan: WorkPlan,
) -> NodeId:
    """Create a symbolic closure node by capturing referenced outer bindings."""
    referenced = _collect_referenced_variables(expression)
    referenced.discard(variable)

    captures: list[tuple[str, NodeId]] = []
    function_captures: dict[str, dict[str, Any]] = {}
    for name in sorted(referenced):
        binding = environment.try_find(name)
        if isinstance(binding, OperationVal):
            captures.append((name, binding.operation_id))
        elif isinstance(binding, FunctionVal):
            function_captures[name] = _serialize_function_capture(name, binding, set())

    capture_names = tuple(name for name, _ in captures)
    capture_args = tuple(node_id for _, node_id in captures)

    return work_plan.add_node(
        NodeSpec(
            kind="closure",
            operator="closure",
            args=capture_args,
            attrs={
                "parameter": variable,
                "body": expression.to_syntax(),
                "capture_names": list(capture_names),
                "function_captures": function_captures,
            },
            output_kind="closure",
        )
    )


def _plan_primitive_call(
    work_plan: WorkPlan,
    identifier: str,
    args: tuple[NodeId, ...],
    kwargs: tuple[tuple[str, NodeId], ...] = (),
    attrs: Optional[dict[str, Any]] = None,
    output_kind: OutputKind = "scalar",
    position: str | None = None,
) -> NodeId:
    """Validate a primitive call and add its symbolic node to the plan."""
    attrs = dict(attrs or {})
    identifier = _normalize_primitive_identifier(identifier)
    call = PrimitiveCall(args=args, kwargs=kwargs, attrs=attrs)

    try:
        spec = work_plan.registry.get_spec(identifier)
    except KeyError:
        _raise_unresolved_identifier(identifier, position=position)

    try:
        spec.arity.validate(len(args) + len(kwargs))
    except Exception as exc:  # noqa: BLE001
        raise StaticAnalysisError(
            [
                StaticDiagnostic(
                    code="E_ARITY",
                    message=f"Invalid arity for '{identifier}': {exc}",
                    location=position,
                    symbol=identifier,
                )
            ]
        ) from exc

    node = spec.planner(call)

    if output_kind != "unknown" and node.output_kind != output_kind:
        node = NodeSpec(
            kind=node.kind,
            operator=node.operator,
            args=node.args,
            kwargs=node.kwargs,
            attrs=dict(node.attrs),
            output_kind=output_kind,
        )

    return work_plan.add_node(node)


def _reduce_map_call(
    env: Environment,
    work_plan: WorkPlan,
    call_expr: ECall,
    stack: Stack,
) -> NodeId:
    """Lower a ``map`` call into a sequence node plus a closure argument."""
    function_expr = call_expr.arguments[0]
    sequence_expr = call_expr.arguments[1]

    sequence_id = reduce_expression(env, work_plan, sequence_expr, stack)

    if not isinstance(function_expr, ECall) or function_expr.arguments:
        raise RuntimeError("map first argument must be a function identifier")

    binding = env.try_find(function_expr.identifier)
    if isinstance(binding, FunctionVal):
        if len(binding.parameters) != 1:
            raise RuntimeError(
                f"map function '{function_expr.identifier}' must accept exactly one argument"
            )

        closure_id = _create_closure_node(
            variable=binding.parameters[0],
            expression=binding.expression,
            environment=binding.environment,
            work_plan=work_plan,
        )
    elif binding is not None:
        raise RuntimeError(
            f"map first argument '{function_expr.identifier}' must reference a function"
        )
    else:
        try:
            work_plan.registry.get_spec(function_expr.identifier)
        except KeyError:
            _raise_unresolved_identifier(
                function_expr.identifier,
                position=function_expr.position,
                call_stack=stack,
            )

        # Must start with a letter to avoid parser tokenizing it as OPERATOR
        # when closure bodies are reparsed at runtime/persistence time.
        map_parameter = "map_item_arg"
        closure_id = _create_closure_node(
            variable=map_parameter,
            expression=ECall(
                "pos",
                function_expr.identifier,
                [ECall("pos", map_parameter, [])],
            ),
            environment=env,
            work_plan=work_plan,
        )

    return _plan_primitive_call(
        work_plan,
        identifier=call_expr.identifier,
        args=(sequence_id, closure_id),
        output_kind="sequence",
        position=call_expr.position,
    )


def _constant_sequence_elements(
    work_plan: WorkPlan, node_id: NodeId
) -> Optional[tuple[NodeId, ...]]:
    """Return the element node ids if ``node_id`` is a sequence of constants.

    A node qualifies when it is a ``sequence`` primitive (the form emitted for
    array literals) whose every argument is a ``constant`` node. Returns ``None``
    otherwise — including for ``range``/``dir`` sequences, whose elements are not
    materialized as constant nodes at reduce time, so those loops stay lazy.
    """
    node = work_plan.nodes.get(node_id)
    if node is None or node.kind != "primitive":
        return None
    if node.operator not in ("sequence", "default.sequence"):
        return None
    if node.kwargs:
        return None
    for arg_id in node.args:
        arg = work_plan.nodes.get(arg_id)
        if arg is None or arg.kind != "constant":
            return None
    return tuple(node.args)


def reduce_expression(
    env: Environment,
    work_plan: WorkPlan,
    expr: Expression,
    stack: Optional[Stack] = None,
) -> NodeId:
    """Reduce one AST expression to the id of a symbolic node."""
    current_stack: Stack = [] if stack is None else stack

    if isinstance(expr, ENumber):
        return _create_constant_node(work_plan, expr.value)

    if isinstance(expr, EArray):
        item_ids = tuple(
            reduce_expression(env, work_plan, item, current_stack)
            for item in expr.items
        )
        return _plan_primitive_call(
            work_plan,
            identifier="sequence",
            args=item_ids,
            output_kind="sequence",
        )

    if isinstance(expr, ESlice):
        sequence_id = reduce_expression(env, work_plan, expr.sequence, current_stack)
        start_id = (
            reduce_expression(env, work_plan, expr.start, current_stack)
            if expr.start is not None
            else _create_constant_node(work_plan, None)
        )
        stop_id = (
            reduce_expression(env, work_plan, expr.stop, current_stack)
            if expr.stop is not None
            else _create_constant_node(work_plan, None)
        )
        return _plan_primitive_call(
            work_plan,
            identifier="slice",
            args=(sequence_id, start_id, stop_id),
            output_kind="sequence",
        )

    if isinstance(expr, EBool):
        return _create_constant_node(work_plan, expr.value)

    if isinstance(expr, EString):
        return _create_constant_node(work_plan, expr.value)

    if isinstance(expr, ECall):
        if expr.identifier in {"map", "default.map"} and len(expr.arguments) == 2:
            return _reduce_map_call(env, work_plan, expr, current_stack)

        # A user-defined function shadows the primitive alias table: resolve it
        # through the environment below, exactly as the runtime closure path does.
        # Without this guard the reduce path would force e.g. `!` to the scalar
        # `not_compat` even where compat.imgql defines `let !(a) = not(a)`, so an
        # image `!(region)` would compile to a scalar not and feed a bool into
        # downstream image kernels (e.g. dt). See issue #20.
        if expr.identifier in _PRIMITIVE_OPERATOR_ALIASES and not isinstance(
            env.try_find(expr.identifier), FunctionVal
        ):
            args_ids = tuple(
                reduce_expression(env, work_plan, arg, current_stack)
                for arg in expr.arguments
            )
            return _plan_primitive_call(
                work_plan,
                identifier=expr.identifier,
                args=args_ids,
                output_kind="scalar",
                position=expr.position,
            )

        if not expr.arguments:
            val = env.try_find(expr.identifier)
            if val is None:
                normalized = _normalize_primitive_identifier(expr.identifier)
                try:
                    work_plan.registry.get_spec(normalized)
                except KeyError:
                    _raise_unresolved_identifier(
                        expr.identifier,
                        position=expr.position,
                        call_stack=current_stack,
                    )
                return _plan_primitive_call(
                    work_plan,
                    identifier=expr.identifier,
                    args=(),
                    position=expr.position,
                )
            if isinstance(val, OperationVal):
                return val.operation_id

            call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            raise RuntimeError(
                f"Function '{expr.identifier}' called without arguments\n"
                + "\n".join(
                    f"{name} at {position}" for name, position in call_stack
                )
            )

        next_stack: Stack = [(expr.identifier, expr.position)] + current_stack
        args_ids = tuple(
            reduce_expression(env, work_plan, arg, next_stack)
            for arg in expr.arguments
        )

        val = env.try_find(expr.identifier)
        if val is None:
            inferred_kind: OutputKind = (
                "sequence"
                if expr.identifier
                in {
                    "for_loop",
                    "default.for_loop",
                    "filter",
                    "default.filter",
                    "map",
                    "default.map",
                    "range",
                    "default.range",
                    "load",
                    "default.load",
                }
                else "overlay"
                if expr.identifier in {"overlay", "default.overlay"}
                else "scalar"
            )
            return _plan_call_not_in_env(
                work_plan,
                expr.identifier,
                position=expr.position,
                call_stack=next_stack,
                args=args_ids,
                output_kind=inferred_kind,
            )

        if isinstance(val, OperationVal):
            raise RuntimeError(
                f"'{expr.identifier}' is not a function but was called with arguments"
            )

        if len(val.parameters) != len(args_ids):
            raise RuntimeError(
                f"Function '{expr.identifier}' expects {len(val.parameters)} arguments but was called with {len(args_ids)}"
            )

        arg_vals: Sequence[DVal] = [OperationVal(arg_id) for arg_id in args_ids]
        func_env = val.environment.bind_list(val.parameters, arg_vals)
        return reduce_expression(func_env, work_plan, val.expression, next_stack)

    if isinstance(expr, EFor):
        iterable_id = reduce_expression(env, work_plan, expr.iterable, current_stack)

        # Expand the loop when the iterable is a compile-time-known constant
        # sequence: bind the loop variable to each element's constant node and
        # re-reduce the body, emitting one DAG node per element. Hash-consing
        # dedupes subexpressions shared across iterations. See issue #20.
        element_ids = _constant_sequence_elements(work_plan, iterable_id)
        if element_ids is not None and 0 <= len(element_ids) <= _FOR_EXPANSION_CAP:
            body_ids = tuple(
                reduce_expression(
                    env.bind(expr.variable, OperationVal(element_id)),
                    work_plan,
                    expr.body,
                    current_stack,
                )
                for element_id in element_ids
            )
            return _plan_primitive_call(
                work_plan,
                identifier="sequence",
                args=body_ids,
                output_kind="sequence",
                position=expr.position,
            )

        closure_id = _create_closure_node(
            variable=expr.variable,
            expression=expr.body,
            environment=env,
            work_plan=work_plan,
        )
        return _plan_primitive_call(
            work_plan,
            identifier="for_loop",
            args=(iterable_id, closure_id),
            output_kind="sequence",
            position=expr.position,
        )

    if isinstance(expr, EFilter):
        iterable_id = reduce_expression(env, work_plan, expr.iterable, current_stack)
        closure_id = _create_closure_node(
            variable=expr.variable,
            expression=expr.predicate,
            environment=env,
            work_plan=work_plan,
        )
        return _plan_primitive_call(
            work_plan,
            identifier="filter",
            args=(iterable_id, closure_id),
            output_kind="sequence",
            position=expr.position,
        )

    if isinstance(expr, EFold):
        sequence_id = reduce_expression(env, work_plan, expr.sequence, current_stack)
        operator = str(expr.operator)
        if operator not in {"+", "-", "*", "/", "&&", "||", "min", "max"}:
            raise StaticAnalysisError(
                [
                    StaticDiagnostic(
                        code="E_UNSUPPORTED_FOLD",
                        message=(
                            f"Unsupported fold operator {operator!r}; "
                            "expected one of +, -, *, /, &&, ||, min, max"
                        ),
                        location=expr.position,
                        symbol=operator,
                    )
                ]
            )

        if expr.init is None:
            args = (sequence_id,)
        else:
            init_id = reduce_expression(env, work_plan, expr.init, current_stack)
            args = (init_id, sequence_id)

        return _plan_primitive_call(
            work_plan,
            identifier="fold",
            args=args,
            attrs={"operator": operator},
            output_kind="scalar",
            position=expr.position,
        )

    if isinstance(expr, ELet):
        _raise_reserved_program_identifier(expr.variable)
        value_id = reduce_expression(env, work_plan, expr.value, current_stack)
        new_env = env.bind(expr.variable, OperationVal(value_id))
        return reduce_expression(new_env, work_plan, expr.body, current_stack)

    raise RuntimeError("Reducer internal error: unknown expression type")


def reduce_command(
    env: Environment,
    work_plan: WorkPlan,
    parsed_imports: set[str],
    command: Command,
) -> tuple[Environment, list[Command]]:
    """Reduce one top-level command and return any imported commands to queue."""
    if isinstance(command, Declaration):
        _raise_reserved_program_identifier(command.identifier)
        if not command.arguments:
            op_id = reduce_expression(env, work_plan, command.expression)
            return env.bind(command.identifier, OperationVal(op_id)), []

        return (
            env.bind(
                command.identifier,
                FunctionVal(env, command.arguments, command.expression),
            ),
            [],
        )

    if isinstance(command, Save):
        op_id = reduce_expression(env, work_plan, command.expression)
        work_plan.add_goal("save", op_id, command.identifier)
        return env, []

    if isinstance(command, Print):
        op_id = reduce_expression(env, work_plan, command.expression)
        work_plan.add_goal("print", op_id, command.identifier)
        return env, []

    if isinstance(command, Import):
        if command.path in parsed_imports:
            return env, []

        parsed_imports.add(command.path)
        import_path = command.path.strip('"\'')

        is_namespace_import = (
            "." not in import_path
            and "/" not in import_path
            and not import_path.endswith(".imgql")
        )

        if is_namespace_import:
            work_plan.import_namespace(import_path)
            return env, list(work_plan.registry.namespace_imgql_exports(import_path))

        imported_commands = parse_import(import_path)
        return env, imported_commands

    raise RuntimeError("Reducer internal error: unknown command type")


def _reduce_program_internal(
    program: Program,
    environment: Environment | None = None,
    *,
    source_name: str = "<input>",
    collect_bindings: bool = False,
) -> tuple[WorkPlan, dict[str, NodeId]]:
    """Reduce a whole program, optionally tracking final declaration bindings."""
    work_plan = WorkPlan()
    env = Environment() if environment is None else environment
    env = _seed_program_variables(env, work_plan, source_name)
    parsed_imports: set[str] = set()
    declaration_bindings: dict[str, NodeId] = {}

    def _track_binding(command: Command, updated_env: Environment) -> None:
        if not collect_bindings:
            return
        if not isinstance(command, Declaration):
            return
        if command.arguments:
            return
        binding = updated_env.try_find(command.identifier)
        if isinstance(binding, OperationVal):
            declaration_bindings[command.identifier] = binding

    stdlib_path = Path(__file__).parent / "stdlib" / "stdlib.imgql"
    if stdlib_path.exists():
        try:
            stdlib_program = parse_program_content(stdlib_path.read_text(encoding="utf-8"))
            commands = list(stdlib_program.commands)
            while commands:
                command = commands.pop(0)
                env, imports = reduce_command(env, work_plan, parsed_imports, command)
                _track_binding(command, env)
                commands = imports + commands
        except Exception as exc:
            logger.warning("Failed to load stdlib: %s", exc)

    # Imported commands are pushed to the front of the queue so the reducer sees
    # them in a deterministic, source-like order.
    commands = list(program.commands)
    # print(commands)
    while commands:
        command = commands.pop(0)
        env, imports = reduce_command(env, work_plan, parsed_imports, command)
        _track_binding(command, env)
        commands = imports + commands

    return work_plan, declaration_bindings


def reduce_program(program: Program, *, source_name: str = "<input>") -> WorkPlan:
    """Reduce a parsed program into a work plan without exposing bindings."""
    work_plan, _bindings = _reduce_program_internal(
        program,
        collect_bindings=False,
        source_name=source_name,
    )
    return work_plan


def reduce_program_with_bindings(
    program: Program,
    *,
    source_name: str = "<input>",
) -> tuple[WorkPlan, dict[str, NodeId]]:
    """Reduce a program and also return final declaration-to-node bindings."""
    return _reduce_program_internal(
        program,
        collect_bindings=True,
        source_name=source_name,
    )
