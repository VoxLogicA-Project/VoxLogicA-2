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

import hashlib
import canonicaljson

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
)

# Type aliases
type identifier = str
type OperationId = str
type Constant = str | bool | int | float
type Arguments = Dict[str, OperationId]
Stack = list[Tuple[str, str]]

@dataclass
class Operation:
    """Operation with operator and arguments"""
    operator: Constant | str
    arguments: Arguments

@dataclass
class Goal:
    """Goal with operation type, operation ID, and name"""
    operation: str  # "print" or "save"
    id: OperationId
    name: str

@dataclass
class WorkPlan:
    """A topologically sorted DAG of operations with goals"""
    operations: Dict[OperationId, Operation] = field(default_factory=dict)
    goals: List[Goal] = field(default_factory=list)

    def add_goal(self, operation: str, operation_id: OperationId, name: str) -> None:
        """Add a goal to the work plan"""
        self.goals.append(Goal(operation, operation_id, name))
    
    def _compute_operation_id(self, operator: Constant | str, arguments: Arguments) -> OperationId:
        """Compute content-addressed SHA256 ID for an operation"""
        op_dict = {"operator": operator, "arguments": arguments}
        canonical_json = canonicaljson.encode_canonical_json(op_dict)
        sha256_hash = hashlib.sha256(canonical_json).hexdigest()
        return sha256_hash
    
    def add_operation(self, operator: Constant | str, arguments: Arguments) -> OperationId:
        """Add an operation to the work plan if not already present, return operation ID"""
        operation_id = self._compute_operation_id(operator, arguments)
        
        if operation_id not in self.operations:
            self.operations[operation_id] = Operation(operator, arguments)
        
        return operation_id

@dataclass
class OperationVal:
    """Operation value"""
    operation_id: OperationId

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
) -> OperationId:
    """Reduce an expression to an operation ID"""
    current_stack: Stack = [] if stack is None else stack
    
    if isinstance(expr, ENumber):
        op_id = work_plan.add_operation(expr.value, {})
        return op_id
    
    elif isinstance(expr, EBool):
        op_id = work_plan.add_operation(expr.value, {})
        return op_id
    
    elif isinstance(expr, EString):
        op_id = work_plan.add_operation(expr.value, {})
        return op_id
    
    elif isinstance(expr, ECall):
        # If it's a variable reference without arguments
        if not expr.arguments:
            val = env.try_find(expr.identifier)
            if val is None:
                # If not found, create a new operation with the identifier
                op_id = work_plan.add_operation(expr.identifier, {})
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
            # If not found, create a new operation with the identifier and arguments
            op_id = work_plan.add_operation(expr.identifier, args_dict)
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
        
        # Import the file and get its commands
        from voxlogica.parser import parse_import
        
        try:
            import_commands = parse_import(command.path)
            return env, import_commands
        except Exception as e:
            raise RuntimeError(f"Failed to import '{command.path}': {str(e)}")
            return env, []
    
    # This should never happen if the parser is correct
    raise RuntimeError("Internal error in reducer: unknown command type")
    return env, []


def reduce_program(program: Program) -> WorkPlan:
    """Reduce a program to a work plan"""
    work_plan = WorkPlan()
    env = Environment()
    parsed_imports: Set[str] = set()
    
    # Make a copy of the commands list that we can modify
    commands = list(program.commands)
    
    # Process commands until there are none left
    while commands:
        command = commands.pop(0)
        env, imports = reduce_command(env, work_plan, parsed_imports, command)
        commands = imports + commands
    
    return work_plan
