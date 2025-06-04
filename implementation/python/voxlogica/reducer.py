"""
VoxLogicA Reducer module - Python implementation
"""

from dataclasses import dataclass, field
from typing import (
    Dict,
    List,
    Set,
    Tuple,
    Optional,
    Union,
    Any,
    Callable,
    Sequence,
    cast,
)
from collections import defaultdict
import itertools
import hashlib
import canonicaljson
from pathlib import Path
import os

from .parser import (
    Expression,
    ECall,
    ENumber,
    EBool,
    EString,
    Command,
    Declaration,
    Save,
    Print,
    Import,
    Program,
)
from .error_msg import fail, fail_with_stacktrace, Stack

# Type aliases
identifier = str
OperationId = str


@dataclass(frozen=True)
class Operator:
    """Base class for operators"""

    def __str__(self) -> str:
        return ""


@dataclass(frozen=True)
class IdentifierOp(Operator):
    """Identifier operator"""

    value: identifier

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class NumberOp(Operator):
    """Number operator"""

    value: float

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class BoolOp(Operator):
    """Boolean operator"""

    value: bool

    def __str__(self) -> str:
        return str(self.value).lower()


@dataclass(frozen=True)
class StringOp(Operator):
    """String operator"""

    value: str

    def __str__(self) -> str:
        return f'"{self.value}"'


# Define types
Arguments = Dict[str, OperationId]


@dataclass(frozen=True)
class Operation:
    """Operation in the work plan"""

    operator: Operator
    arguments: Arguments

    def __post_init__(self):
        # Convert arguments dict to a frozenset of items for hashability
        object.__setattr__(
            self, "arguments", dict(self.arguments)
        )  # Ensure it's a proper dict

    def __hash__(self):
        # Make Operation hashable by converting arguments to frozenset
        return hash((self.operator, frozenset(self.arguments.items())))

    def __str__(self) -> str:
        if not self.arguments:
            return f"{self.operator}"

        args_str = ",".join(str(arg) for arg in self.arguments.values())
        return f"{self.operator}({args_str})"


class Goal:
    """Base class for goals in the work plan"""

    pass


@dataclass(frozen=True)
class GoalSave(Goal):
    """Goal to save an operation's result"""

    name: str
    operation_id: OperationId

    def __str__(self) -> str:
        return f"save({self.name},{self.operation_id})"


@dataclass(frozen=True)
class GoalPrint(Goal):
    """Goal to print an operation's result"""

    name: str
    operation_id: OperationId

    def __str__(self) -> str:
        return f"print({self.name},{self.operation_id})"


@dataclass
class WorkPlan:
    """A work plan consisting of operations and goals"""

    operations: List[Operation]
    goals: List[Goal]
    # Store the mapping from operations to their IDs for DOT generation
    _operation_ids: Optional[Dict[Operation, OperationId]] = None

    def __str__(self) -> str:
        ops_str = "\n".join([f"{i} -> {op}" for i, op in enumerate(self.operations)])
        goals_str = ",".join([str(goal) for goal in self.goals])

        return f"goals: {goals_str}\noperations:\n{ops_str}"

    def to_program(self) -> Program:
        """Convert the work plan back to a program"""
        declarations = []
        for i, op in enumerate(self.operations):
            if isinstance(op.operator, IdentifierOp):
                args: List[Expression] = [
                    ECall("unknown", f"op{arg}", []) for arg in op.arguments.values()
                ]
                expr = ECall("unknown", op.operator.value, args)
            elif isinstance(op.operator, NumberOp):
                expr = ENumber(op.operator.value)
            elif isinstance(op.operator, BoolOp):
                expr = EBool(op.operator.value)
            elif isinstance(op.operator, StringOp):
                expr = EString(op.operator.value)
            else:
                raise ValueError(f"Unknown operator type: {type(op.operator)}")

            declarations.append(Declaration(f"op{i}", [], expr))

        goals_cmds = []
        for goal in self.goals:
            if isinstance(goal, GoalSave):
                goals_cmds.append(
                    Save(
                        "unknown",
                        goal.name,
                        ECall("unknown", f"op{goal.operation_id}", []),
                    )
                )
            elif isinstance(goal, GoalPrint):
                goals_cmds.append(
                    Print(
                        "unknown",
                        goal.name,
                        ECall("unknown", f"op{goal.operation_id}", []),
                    )
                )

        return Program(declarations + goals_cmds)

    def to_dot(self) -> str:
        """Convert the work plan to a DOT graph representation"""
        dot_str = "digraph {\n"

        # Create a mapping from operation ID to short index for readability
        id_to_index = {
            op_id: i for i, (op_id, _) in enumerate(self._get_operations_with_ids())
        }

        for op_id, operation in self._get_operations_with_ids():
            index = id_to_index[op_id]
            # Use truncated hash for display
            short_id = op_id[:8] if len(op_id) > 8 else op_id
            dot_str += f'  "{op_id}" [label="[{index}] {short_id}\\n{operation}"]\n'

            for argument in operation.arguments.values():
                dot_str += f'  "{argument}" -> "{op_id}";\n'

        dot_str += "}\n"
        return dot_str

    def _get_operations_with_ids(self) -> List[Tuple[OperationId, Operation]]:
        """Helper method to get operations with their IDs for internal use"""
        if self._operation_ids:
            return [(self._operation_ids[op], op) for op in self.operations]
        else:
            # Fallback: generate sequential fake IDs for display
            result = []
            for i, operation in enumerate(self.operations):
                fake_id = f"op_{i}"
                result.append((fake_id, operation))
            return result

    def to_json(self) -> dict:
        """Return a JSON-serializable dict representing the work plan."""

        def op_to_dict(op):
            # Output numbers as JSON numbers, not strings
            if isinstance(op.operator, NumberOp):
                operator_value = op.operator.value
            else:
                operator_value = str(op.operator)
            return {
                "operator": operator_value,
                "arguments": op.arguments,
            }

        def goal_to_dict(goal):
            if isinstance(goal, GoalSave):
                return {
                    "type": "save",
                    "name": goal.name,
                    "operation_id": goal.operation_id,
                }
            elif isinstance(goal, GoalPrint):
                return {
                    "type": "print",
                    "name": goal.name,
                    "operation_id": goal.operation_id,
                }
            else:
                return {"type": "unknown"}

        return {
            "operations": [op_to_dict(op) for op in self.operations],
            "goals": [goal_to_dict(goal) for goal in self.goals],
        }


@dataclass
class InternalOperation:
    """Internal operation with ID and operation"""

    def __init__(self, op_id: OperationId, operation: Operation):
        self.id = op_id
        self.operation = operation


class Operations:
    """Collection of operations with content-addressed memoization using SHA256 hashes"""

    def __init__(self):
        self.by_term: Dict[
            Tuple[Operator, Tuple[Tuple[str, OperationId], ...]], InternalOperation
        ] = {}
        self.by_id: Dict[OperationId, InternalOperation] = {}
        self.memoize = True

    def _compute_operation_id(
        self, operator: Operator, arguments: Arguments
    ) -> OperationId:
        """Compute content-addressed SHA256 ID for an operation"""
        # Create a canonical JSON representation of the operation
        op_dict = {
            "operator": self._operator_to_dict(operator),
            "arguments": dict(sorted(arguments.items())),  # Sort for consistency
        }

        # Use canonical JSON encoding (RFC 8785)
        canonical_json = canonicaljson.encode_canonical_json(op_dict)

        # Compute SHA256 hash
        sha256_hash = hashlib.sha256(canonical_json).hexdigest()
        return sha256_hash

    def _operator_to_dict(self, operator: Operator) -> dict:
        """Convert operator to a dictionary for JSON serialization"""
        if isinstance(operator, IdentifierOp):
            return {"type": "identifier", "value": operator.value}
        elif isinstance(operator, NumberOp):
            return {"type": "number", "value": operator.value}
        elif isinstance(operator, BoolOp):
            return {"type": "bool", "value": operator.value}
        elif isinstance(operator, StringOp):
            return {"type": "string", "value": operator.value}
        else:
            raise ValueError(f"Unknown operator type: {type(operator)}")

    def find_or_create(self, operator: Operator, arguments: Arguments) -> OperationId:
        """Find an existing operation or create a new one"""
        if self.memoize:
            key = (operator, tuple(sorted(arguments.items())))
            if key in self.by_term:
                return self.by_term[key].id

        return self.create(operator, arguments)

    def try_find(
        self, operator: Operator, arguments: Arguments
    ) -> Optional[OperationId]:
        """Try to find an existing operation"""
        if self.memoize:
            key = (operator, tuple(sorted(arguments.items())))
            if key in self.by_term:
                return self.by_term[key].id

        return None

    def create(self, operator: Operator, arguments: Arguments) -> OperationId:
        """Create a new operation with content-addressed ID"""
        new_id = self._compute_operation_id(operator, arguments)

        # Check if this ID already exists (hash collision check)
        if new_id in self.by_id:
            # This should be extremely rare, but handle gracefully
            existing_op = self.by_id[new_id]
            if (
                existing_op.operation.operator == operator
                and existing_op.operation.arguments == arguments
            ):
                # Same operation, return existing ID
                return new_id
            else:
                # Hash collision - this is extremely unlikely but theoretically possible
                raise RuntimeError(
                    f"SHA256 hash collision detected for operation ID: {new_id}"
                )

        new_operation = InternalOperation(new_id, Operation(operator, arguments))

        if self.memoize:
            self.by_term[(operator, tuple(sorted(arguments.items())))] = new_operation

        self.by_id[new_id] = new_operation
        return new_id

    def alias(
        self, operator: Operator, arguments: Arguments, operation_id: OperationId
    ) -> None:
        """Create an alias for an existing operation"""
        operation = self.by_id[operation_id]
        self.by_term[(operator, tuple(sorted(arguments.items())))] = operation


class DVal:
    """Base class for dynamic values"""

    pass


@dataclass
class OperationVal(DVal):
    """Operation value"""

    operation_id: OperationId


@dataclass
class FunctionVal(DVal):
    """Function value"""

    environment: "Environment"
    parameters: List[identifier]
    expression: Expression


class Environment:
    """Environment for variable bindings"""

    def __init__(self, bindings: Optional[Dict[identifier, DVal]] = None):
        self.bindings = bindings or {}

    def try_find(self, ide: identifier) -> Optional[DVal]:
        """Try to find a binding for an identifier"""
        return self.bindings.get(ide)

    def bind(self, ide: identifier, expr: DVal) -> "Environment":
        """Create a new environment with an additional binding"""
        new_bindings = dict(self.bindings)
        new_bindings[ide] = expr
        return Environment(new_bindings)

    def bind_list(
        self, ide_list: List[identifier], expr_list: Sequence[DVal]
    ) -> "Environment":
        """Create a new environment with multiple bindings"""
        if len(ide_list) != len(expr_list):
            fail("Internal error in module Reducer. Please report.")

        env = self
        for ide, expr in zip(ide_list, expr_list):
            env = env.bind(ide, expr)

        return env


def reduce_expression(
    env: Environment,
    operations: Operations,
    expr: Expression,
    stack: Optional[Stack] = None,
) -> OperationId:
    """
    Reduce an expression to an operation ID

    Args:
        env: The environment with variable bindings
        operations: The operations collection
        expr: The expression to reduce
        stack: The current stack trace

    Returns:
        The operation ID for the reduced expression
    """
    current_stack: Stack = [] if stack is None else stack

    if isinstance(expr, ENumber):
        return operations.find_or_create(NumberOp(expr.value), {})

    elif isinstance(expr, EBool):
        return operations.find_or_create(BoolOp(expr.value), {})

    elif isinstance(expr, EString):
        return operations.find_or_create(StringOp(expr.value), {})

    elif isinstance(expr, ECall):
        # If it's a variable reference without arguments
        if not expr.arguments:
            val = env.try_find(expr.identifier)
            if val is None:
                # If not found, create a new operation with the identifier
                return operations.find_or_create(IdentifierOp(expr.identifier), {})

            if isinstance(val, OperationVal):
                return val.operation_id

            call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            fail_with_stacktrace(
                f"Function '{expr.identifier}' called without arguments", call_stack
            )

        # It's a function call with arguments
        this_stack: Stack = [(expr.identifier, expr.position)] + current_stack
        args_ops = [
            reduce_expression(env, operations, arg, this_stack)
            for arg in expr.arguments
        ]

        # Convert list to dict with string numeric keys
        args_dict = {str(i): op_id for i, op_id in enumerate(args_ops)}

        # Check if it's a variable with arguments
        val = env.try_find(expr.identifier)
        if val is None:
            # If not found, create a new operation with the identifier and arguments
            return operations.find_or_create(IdentifierOp(expr.identifier), args_dict)

        if isinstance(val, OperationVal):
            call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            fail_with_stacktrace(
                f"'{expr.identifier}' is not a function but was called with arguments",
                call_stack,
            )

        if isinstance(val, FunctionVal):
            if len(val.parameters) != len(args_dict):
                call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
                fail_with_stacktrace(
                    f"Function '{expr.identifier}' expects {len(val.parameters)} arguments but was called with {len(args_dict)}",
                    call_stack,
                )

            # Create operation values from argument operation IDs
            arg_vals: Sequence[DVal] = [
                OperationVal(op_id) for op_id in args_dict.values()
            ]

            # Create a new environment with function arguments bound
            func_env = val.environment.bind_list(val.parameters, arg_vals)

            # Reduce the function body in the new environment
            func_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            return reduce_expression(func_env, operations, val.expression, func_stack)

    # This should never happen if the parser is correct
    fail("Internal error in reducer: unknown expression type")
    # This will never be reached, but added to satisfy the linter
    return ""


def reduce_command(
    env: Environment,
    operations: Operations,
    goals: Set[Goal],
    parsed_imports: Set[str],
    command: Command,
) -> Tuple[Environment, List[Command]]:
    """
    Reduce a command and update the environment, operations, and goals

    Args:
        env: The environment with variable bindings
        operations: The operations collection
        goals: The set of goals to be performed
        parsed_imports: Set of already parsed imports
        command: The command to reduce

    Returns:
        A tuple with the updated environment and any additional commands from imports
    """
    if isinstance(command, Declaration):
        if not command.arguments:
            # It's a simple variable declaration
            op_id = reduce_expression(env, operations, command.expression)
            return env.bind(command.identifier, OperationVal(op_id)), []
        else:
            # It's a function declaration
            return (
                env.bind(
                    command.identifier,
                    FunctionVal(env, command.arguments, command.expression),
                ),
                [],
            )

    elif isinstance(command, Save):
        op_id = reduce_expression(env, operations, command.expression)
        goals.add(GoalSave(command.identifier, op_id))
        return env, []

    elif isinstance(command, Print):
        op_id = reduce_expression(env, operations, command.expression)
        goals.add(GoalPrint(command.identifier, op_id))
        return env, []

    elif isinstance(command, Import):
        # Avoid circular imports
        if command.path in parsed_imports:
            return env, []

        parsed_imports.add(command.path)

        # Import the file and get its commands
        from .parser import parse_import

        try:
            import_commands = parse_import(command.path)
            return env, import_commands
        except Exception as e:
            fail(f"Failed to import '{command.path}': {str(e)}")
            # This will never be reached, but added to satisfy the linter
            return env, []

    # This should never happen if the parser is correct
    fail("Internal error in reducer: unknown command type")
    # This will never be reached, but added to satisfy the linter
    return env, []


def reduce_program(program: Program) -> WorkPlan:
    """
    Reduce a program to a work plan

    Args:
        program: The program to reduce

    Returns:
        A work plan with operations and goals
    """
    operations = Operations()
    goals: Set[Goal] = set()
    env = Environment()
    parsed_imports: Set[str] = set()

    # Make a copy of the commands list that we can modify
    commands = list(program.commands)

    # Process commands until there are none left
    while commands:
        command = commands.pop(0)
        env, imports = reduce_command(env, operations, goals, parsed_imports, command)
        commands = imports + commands

    # Convert the operations dictionary to a list and build ID mapping
    sorted_internal_ops = sorted(operations.by_id.values(), key=lambda x: x.id)
    op_list = [op.operation for op in sorted_internal_ops]
    operation_ids = {op.operation: op.id for op in sorted_internal_ops}

    return WorkPlan(op_list, list(goals), operation_ids)
