"""
VoxLogicA Reducer module - Python implementation (simplified)
"""

from __future__ import annotations

from typing import (
    Dict,
    List,
    Set,
    Optional,
    Sequence,
    Tuple
)
from dataclasses import dataclass, field
from pathlib import Path

import hashlib
import canonicaljson
import logging

logger = logging.getLogger(__name__)

from voxlogica.parser import (
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
    parse_program,
    parse_program_content,
    parse_import,
)

# Type aliases
type identifier = str
type NodeId = str
type Arguments = Dict[str, NodeId]
type Stack = list[Tuple[str, str]]

@dataclass
class ConstantValue:
    """Constant value"""    
    value: str | bool | int | float

@dataclass
class Operation:
    """Operation with operator and arguments"""
    operator: str
    arguments: Arguments

type Node = ConstantValue | Operation

@dataclass
class Goal:
    """Goal with operation type, operation ID, and name"""
    operation: str  # "print" or "save"
    id: NodeId
    name: str

@dataclass
class WorkPlan:
    """A topologically sorted DAG of operations with goals"""
    nodes: Dict[NodeId, Node] = field(default_factory=dict)
    goals: List[Goal] = field(default_factory=list)
    _imported_namespaces: Set[str] = field(default_factory=set)

    def add_goal(self, operation: str, operation_id: NodeId, name: str) -> None:
        """Add a goal to the work plan"""
        self.goals.append(Goal(operation, operation_id, name))
    
    def _compute_node_id(self,node: Node) -> NodeId:
        """Compute content-addressed SHA256 ID for an operation"""
        import dataclasses
        # Convert dataclass to dict for JSON serialization
        if dataclasses.is_dataclass(node):
            node_dict = dataclasses.asdict(node)
        else:
            node_dict = node
        canonical_json = canonicaljson.encode_canonical_json(node_dict)
        sha256_hash = hashlib.sha256(canonical_json).hexdigest()
        return sha256_hash
    
    def add_node(self, node: Node) -> NodeId:
        """Add an operation to the work plan if not already present, return operation ID"""
        operation_id = self._compute_node_id(node)
        if operation_id not in self.nodes:
            self.nodes[operation_id] = node
        return operation_id

    @property
    def operations(self) -> Dict[NodeId, Operation]:
        """Return only Operation nodes (not ConstantValue) for compatibility with execution/converters."""
        return {k: v for k, v in self.nodes.items() if isinstance(v, Operation)}

    @property
    def constants(self) -> Dict[NodeId, ConstantValue]:
        """Return only ConstantValue nodes."""
        return {k: v for k, v in self.nodes.items() if isinstance(v, ConstantValue)}

@dataclass
class OperationVal:
    """Operation value"""
    operation_id: NodeId

@dataclass
class FunctionVal:
    """Function value"""
    environment: Environment
    parameters: List[identifier]
    expression: Expression

# Dynamic values for evaluation
DVal = OperationVal | FunctionVal

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
    
    def bind_list(self, ide_list: List[identifier], expr_list: Sequence[DVal]) -> "Environment":
        """Create a new environment with multiple bindings"""
        if len(ide_list) != len(expr_list):
            raise RuntimeError("Internal error in module Reducer. Please report.")
        
        env = self
        for ide, expr in zip(ide_list, expr_list):
            env = env.bind(ide, expr)
        
        return env


def reduce_expression(
    env: Environment,
    work_plan: WorkPlan,
    expr: Expression,
    stack: Optional[Stack] = None,
) -> NodeId:
    """Reduce an expression to an operation ID"""
    current_stack: Stack = [] if stack is None else stack

    if isinstance(expr, ENumber):
        node = ConstantValue(expr.value)
        op_id = work_plan.add_node(node)
        return op_id

    elif isinstance(expr, EBool):
        node = ConstantValue(expr.value)
        op_id = work_plan.add_node(node)
        return op_id

    elif isinstance(expr, EString):
        node = ConstantValue(expr.value)
        op_id = work_plan.add_node(node)
        return op_id

    elif isinstance(expr, ECall):
        # If it's a variable reference without arguments
        if not expr.arguments:
            val = env.try_find(expr.identifier)
            if val is None:
                # If not found, create a new operation node with the identifier as operator
                node = Operation(operator=expr.identifier, arguments={})
                op_id = work_plan.add_node(node)
                return op_id

            if isinstance(val, OperationVal):
                return val.operation_id

            call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            raise RuntimeError(f"Function '{expr.identifier}' called without arguments\n" + "\n".join(f"{identifier} at {position}" for identifier, position in call_stack))

        # It's a function call with arguments
        this_stack: Stack = [(expr.identifier, expr.position)] + current_stack
        args_ops = [
            reduce_expression(env, work_plan, arg, this_stack)
            for arg in expr.arguments
        ]

        # Convert list to dict with string numeric keys
        args_dict = {str(i): op_id for i, op_id in enumerate(args_ops)}

        # Check if it's a variable with arguments
        val = env.try_find(expr.identifier)
        if val is None:
            # If not found, create a new operation node with the identifier and arguments
            node = Operation(operator=expr.identifier, arguments=args_dict)
            op_id = work_plan.add_node(node)
            return op_id

        if isinstance(val, OperationVal):
            call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            raise RuntimeError(
                f"'{expr.identifier}' is not a function but was called with arguments\n" + "\n".join(f"{identifier} at {position}" for identifier, position in call_stack),
            )

        if isinstance(val, FunctionVal):
            if len(val.parameters) != len(args_dict):
                call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
                raise RuntimeError(
                    f"Function '{expr.identifier}' expects {len(val.parameters)} arguments but was called with {len(args_dict)}\n" + "\n".join(f"{identifier} at {position}" for identifier, position in call_stack),
                )

            # Create operation values from argument operation IDs
            arg_vals: Sequence[DVal] = [
                OperationVal(op_id) for op_id in args_dict.values()
            ]

            # Create a new environment with function arguments bound
            func_env = val.environment.bind_list(val.parameters, arg_vals)

            # Reduce the function body in the new environment
            func_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            return reduce_expression(func_env, work_plan, val.expression, func_stack)

    # This should never happen if the parser is correct
    raise RuntimeError("Internal error in reducer: unknown expression type")
    return ""


def reduce_command(
    env: Environment,
    work_plan: WorkPlan,
    parsed_imports: Set[str],
    command: Command,
) -> tuple[Environment, List[Command]]:
    """Reduce a command and update the environment"""
    
    if isinstance(command, Declaration):
        if not command.arguments:
            # It's a simple variable declaration
            op_id = reduce_expression(env, work_plan, command.expression)
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
        op_id = reduce_expression(env, work_plan, command.expression)
        work_plan.add_goal("save", op_id, command.identifier)
        return env, []
    
    elif isinstance(command, Print):
        op_id = reduce_expression(env, work_plan, command.expression)
        work_plan.add_goal("print", op_id, command.identifier)
        return env, []
    
    elif isinstance(command, Import):
        # Avoid circular imports
        if command.path in parsed_imports:
            return env, []
        
        parsed_imports.add(command.path)
        
        # Check if this is a namespace import or file import
        # Remove quotes from the path
        import_path = command.path.strip('"\'')
        
        # Check if it's a known namespace (no file extension, single word)
        is_namespace_import = ('.' not in import_path and 
                              '/' not in import_path and 
                              not import_path.endswith('.imgql'))
        
        if is_namespace_import:
            # This is a namespace import - signal the execution engine
            # We'll store this information in the workplan for now
            # The execution engine will handle the actual namespace import
            work_plan._imported_namespaces.add(import_path)
            logger.debug(f"Marked namespace '{import_path}' for import")
            return env, []
        else:
            # This is a file import - use existing logic
            try:
                import_commands = parse_import(import_path)
                return env, import_commands
            except Exception as e:
                raise RuntimeError(f"Failed to import '{import_path}': {str(e)}")
                return env, []
    
    # This should never happen if the parser is correct
    raise RuntimeError("Internal error in reducer: unknown command type")
    return env, []


def reduce_program(program: Program) -> WorkPlan:
    """Reduce a program to a work plan"""
    work_plan = WorkPlan()
    env = Environment()
    parsed_imports: Set[str] = set()
    
    # Auto-import stdlib before processing user commands
    stdlib_path = Path(__file__).parent / "stdlib" / "stdlib.imgql"
    if stdlib_path.exists():
        try:
            with open(stdlib_path, 'r', encoding='utf-8') as f:
                stdlib_content = f.read()
            
            # Parse and process stdlib
            stdlib_program = parse_program_content(stdlib_content)
            
            # Process stdlib commands first
            stdlib_commands = list(stdlib_program.commands)
            while stdlib_commands:
                command = stdlib_commands.pop(0)
                env, imports = reduce_command(env, work_plan, parsed_imports, command)
                stdlib_commands = imports + stdlib_commands
                
        except Exception as e:
            logging.warning(f"Failed to load stdlib: {e}")
    
    # Make a copy of the commands list that we can modify
    commands = list(program.commands)
    
    # Process commands until there are none left
    while commands:
        command = commands.pop(0)
        env, imports = reduce_command(env, work_plan, parsed_imports, command)
        commands = imports + commands
    
    return work_plan
