"""Symbolic reducer: AST -> SymbolicPlan."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence
import logging

from voxlogica.lazy import GoalSpec, NodeId, NodeSpec, SymbolicPlan
from voxlogica.lazy.hash import hash_node
from voxlogica.parser import (
    Command,
    Declaration,
    EBool,
    ECall,
    EFor,
    ELet,
    ENumber,
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

logger = logging.getLogger(__name__)

identifier = str
Stack = list[tuple[str, str]]


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
    """Reducer output container with compatibility helpers."""

    nodes: dict[NodeId, NodeSpec] = field(default_factory=dict)
    goals: list[GoalSpec] = field(default_factory=list)
    imported_namespaces: list[str] = field(default_factory=list)
    registry: PrimitiveRegistry = field(default_factory=PrimitiveRegistry, repr=False)

    def add_node(self, node: NodeSpec) -> NodeId:
        node_id = hash_node(node)
        if node_id not in self.nodes:
            self.nodes[node_id] = node
        return node_id

    def add_goal(self, operation: str, operation_id: NodeId, name: str) -> None:
        self.goals.append(GoalSpec(operation=operation, id=operation_id, name=name))

    def import_namespace(self, namespace: str) -> None:
        self.registry.import_namespace(namespace)
        if namespace not in self.imported_namespaces:
            self.imported_namespaces.append(namespace)

    @property
    def operations(self) -> dict[NodeId, Operation]:
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
        return {
            node_id: ConstantValue(value=node.attrs.get("value"))
            for node_id, node in self.nodes.items()
            if node.kind == "constant"
        }

    @property
    def closures(self) -> dict[NodeId, ClosureValue]:
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
        return SymbolicPlan(
            nodes=dict(self.nodes),
            goals=list(self.goals),
            imported_namespaces=tuple(self.imported_namespaces),
        )

    @property
    def definition_store(self) -> dict[NodeId, NodeSpec]:
        return self.nodes

    def __str__(self) -> str:
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
        self.bindings = bindings or {}

    def try_find(self, ide: identifier) -> Optional[DVal]:
        return self.bindings.get(ide)

    def bind(self, ide: identifier, expr: DVal) -> "Environment":
        new_bindings = dict(self.bindings)
        new_bindings[ide] = expr
        return Environment(new_bindings)

    def bind_list(self, ide_list: list[identifier], expr_list: Sequence[DVal]) -> "Environment":
        if len(ide_list) != len(expr_list):
            raise RuntimeError("Reducer internal error: arity mismatch")

        env = self
        for ide, expr in zip(ide_list, expr_list, strict=True):
            env = env.bind(ide, expr)

        return env


def _collect_referenced_variables(expr: Expression) -> set[str]:
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

    if isinstance(expr, ELet):
        refs = _collect_referenced_variables(expr.value)
        body_refs = _collect_referenced_variables(expr.body)
        body_refs.discard(expr.variable)
        refs.update(body_refs)
        return refs

    return set()


def _create_constant_node(work_plan: WorkPlan, value: Any) -> NodeId:
    return work_plan.add_node(
        NodeSpec(
            kind="constant",
            operator="constant",
            attrs={"value": value},
            output_kind="scalar",
        )
    )


def _serialize_function_capture(
    name: str,
    function_value: FunctionVal,
    seen: set[str],
) -> dict[str, Any]:
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
    output_kind: str = "scalar",
) -> NodeId:
    attrs = dict(attrs or {})
    call = PrimitiveCall(args=args, kwargs=kwargs, attrs=attrs)

    try:
        spec = work_plan.registry.get_spec(identifier)
        spec.arity.validate(len(args) + len(kwargs))
        node = spec.planner(call)
    except Exception:
        node = NodeSpec(
            kind="primitive",
            operator=identifier,
            args=args,
            kwargs=kwargs,
            attrs=attrs,
            output_kind=output_kind,
        )

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
    function_expr = call_expr.arguments[0]
    sequence_expr = call_expr.arguments[1]

    sequence_id = reduce_expression(env, work_plan, sequence_expr, stack)

    if not isinstance(function_expr, ECall) or function_expr.arguments:
        raise RuntimeError("map first argument must be a function identifier")

    binding = env.try_find(function_expr.identifier)
    if not isinstance(binding, FunctionVal):
        raise RuntimeError(
            f"map first argument '{function_expr.identifier}' must reference a function"
        )

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

    return _plan_primitive_call(
        work_plan,
        identifier=call_expr.identifier,
        args=(sequence_id, closure_id),
        output_kind="sequence",
    )


def reduce_expression(
    env: Environment,
    work_plan: WorkPlan,
    expr: Expression,
    stack: Optional[Stack] = None,
) -> NodeId:
    current_stack: Stack = [] if stack is None else stack

    if isinstance(expr, ENumber):
        return _create_constant_node(work_plan, expr.value)

    if isinstance(expr, EBool):
        return _create_constant_node(work_plan, expr.value)

    if isinstance(expr, EString):
        return _create_constant_node(work_plan, expr.value)

    if isinstance(expr, ECall):
        if expr.identifier in {"map", "default.map"} and len(expr.arguments) == 2:
            return _reduce_map_call(env, work_plan, expr, current_stack)

        if not expr.arguments:
            val = env.try_find(expr.identifier)
            if val is None:
                return _plan_primitive_call(
                    work_plan,
                    identifier=expr.identifier,
                    args=(),
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

        call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
        args_ids = tuple(
            reduce_expression(env, work_plan, arg, call_stack)
            for arg in expr.arguments
        )

        val = env.try_find(expr.identifier)
        if val is None:
            inferred_kind = (
                "sequence"
                if expr.identifier
                in {
                    "for_loop",
                    "default.for_loop",
                    "map",
                    "default.map",
                    "range",
                    "default.range",
                    "load",
                    "default.load",
                }
                else "scalar"
            )
            return _plan_primitive_call(
                work_plan,
                identifier=expr.identifier,
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
        return reduce_expression(func_env, work_plan, val.expression, call_stack)

    if isinstance(expr, EFor):
        iterable_id = reduce_expression(env, work_plan, expr.iterable, current_stack)
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
        )

    if isinstance(expr, ELet):
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
    if isinstance(command, Declaration):
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
            return env, []

        imported_commands = parse_import(import_path)
        return env, imported_commands

    raise RuntimeError("Reducer internal error: unknown command type")


def reduce_program(program: Program) -> WorkPlan:
    work_plan = WorkPlan()
    env = Environment()
    parsed_imports: set[str] = set()

    stdlib_path = Path(__file__).parent / "stdlib" / "stdlib.imgql"
    if stdlib_path.exists():
        try:
            stdlib_program = parse_program_content(stdlib_path.read_text(encoding="utf-8"))
            commands = list(stdlib_program.commands)
            while commands:
                command = commands.pop(0)
                env, imports = reduce_command(env, work_plan, parsed_imports, command)
                commands = imports + commands
        except Exception as exc:
            logger.warning("Failed to load stdlib: %s", exc)

    commands = list(program.commands)
    while commands:
        command = commands.pop(0)
        env, imports = reduce_command(env, work_plan, parsed_imports, command)
        commands = imports + commands

    return work_plan
