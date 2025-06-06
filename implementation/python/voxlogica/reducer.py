"""
VoxLogicA Reducer module - Python implementation (simplified)
"""

from typing import (
    Dict,
    List,
    Set,
    Optional,
    Union,
    Any,
    Sequence,
)
import hashlib
import canonicaljson

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
Constant = str | bool | int | float
Arguments = Dict[str, OperationId]

# Simple operation structure: (operator, arguments)
Operation = tuple[Union[Constant, str], Arguments]

# Goals
Goal = tuple[str, str, OperationId]  # (type, name, operation_id)

class WorkPlan:
    """A topologically sorted DAG of operations with goals"""
    
    def __init__(self):
        self.operations: Dict[OperationId, Operation] = {}
        self.goals: List[Goal] = []
    
    def add_operation(self, op_id: OperationId, operator: Union[Constant, str], arguments: Arguments) -> None:
        """Add an operation to the work plan"""
        self.operations[op_id] = (operator, arguments)
    
    def add_goal(self, goal_type: str, name: str, operation_id: OperationId) -> None:
        """Add a goal to the work plan"""
        self.goals.append((goal_type, name, operation_id))
    
    def __str__(self) -> str:
        goals_str = ",".join([f"{t}({n},{oid})" for t, n, oid in self.goals])
        ops_str = "\n".join([f"{i} -> {self._format_operation(op)}" for i, (oid, op) in enumerate(self.operations.items())])
        return f"goals: {goals_str}\noperations:\n{ops_str}"
    
    def _format_operation(self, op: Operation) -> str:
        """Format an operation for display"""
        operator, arguments = op
        if not arguments:
            return str(operator)
        args_str = ",".join(arguments.values())
        return f"{operator}({args_str})"
    
    def to_json(self, buffer_assignment: Optional[Dict[OperationId, int]] = None) -> dict:
        """Convert to JSON format"""
        operations_list = []
        for op_id, (operator, arguments) in self.operations.items():
            op_dict = {
                "id": op_id,
                "operator": operator,
                "arguments": arguments,
            }
            if buffer_assignment and op_id in buffer_assignment:
                op_dict["buffer_id"] = buffer_assignment[op_id]
            operations_list.append(op_dict)
        
        goals_list = []
        for goal_type, name, operation_id in self.goals:
            goals_list.append({
                "type": goal_type,
                "name": name,
                "operation_id": operation_id,
            })
        
        return {
            "operations": operations_list,
            "goals": goals_list,
        }
    
    def to_dot(self, buffer_assignment: Optional[Dict[OperationId, int]] = None) -> str:
        """Convert to DOT format"""
        dot_str = "digraph {\n"
        for op_id, (operator, arguments) in self.operations.items():
            op_name = str(operator)
            op_label = f"{op_name}"
            
            if buffer_assignment and op_id in buffer_assignment:
                buffer_id = buffer_assignment[op_id]
                op_label = f"{op_name}\\nbuf:{buffer_id}"
            
            dot_str += f'  "{op_id}" [label="{op_label}"]\n'
            
            for argument in arguments.values():
                dot_str += f'  "{argument}" -> "{op_id}";\n'
        
        dot_str += "}\n"
        return dot_str


class Operations:
    """Collection of operations with content-addressed memoization using SHA256 hashes"""
    
    def __init__(self):
        self.by_content: Dict[tuple, OperationId] = {}
        self.memoize = True
    
    def _compute_operation_id(self, operator: Union[Constant, str], arguments: Arguments) -> OperationId:
        """Compute content-addressed SHA256 ID for an operation"""
        op_dict = {"operator": operator, "arguments": arguments}
        canonical_json = canonicaljson.encode_canonical_json(op_dict)
        sha256_hash = hashlib.sha256(canonical_json).hexdigest()
        return sha256_hash
    
    def find_or_create(self, operator: Union[Constant, str], arguments: Arguments) -> OperationId:
        """Find an existing operation or create a new one"""
        if self.memoize:
            key = (operator, tuple(sorted(arguments.items())))
            if key in self.by_content:
                return self.by_content[key]
        
        return self.create(operator, arguments)
    
    def create(self, operator: Union[Constant, str], arguments: Arguments) -> OperationId:
        """Create a new operation with content-addressed ID"""
        new_id = self._compute_operation_id(operator, arguments)
        
        if self.memoize:
            key = (operator, tuple(sorted(arguments.items())))
            self.by_content[key] = new_id
        
        return new_id


# Dynamic values for evaluation
DVal = Union["OperationVal", "FunctionVal"]

class OperationVal:
    """Operation value"""
    def __init__(self, operation_id: OperationId):
        self.operation_id = operation_id

class FunctionVal:
    """Function value"""
    def __init__(self, environment: "Environment", parameters: List[identifier], expression: Expression):
        self.environment = environment
        self.parameters = parameters
        self.expression = expression

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
            fail("Internal error in module Reducer. Please report.")
        
        env = self
        for ide, expr in zip(ide_list, expr_list):
            env = env.bind(ide, expr)
        
        return env


def reduce_expression(
    env: Environment,
    operations: Operations,
    work_plan: WorkPlan,
    expr: Expression,
    stack: Optional[Stack] = None,
) -> OperationId:
    """Reduce an expression to an operation ID"""
    current_stack: Stack = [] if stack is None else stack
    
    if isinstance(expr, ENumber):
        op_id = operations.find_or_create(expr.value, {})
        work_plan.add_operation(op_id, expr.value, {})
        return op_id
    
    elif isinstance(expr, EBool):
        op_id = operations.find_or_create(expr.value, {})
        work_plan.add_operation(op_id, expr.value, {})
        return op_id
    
    elif isinstance(expr, EString):
        op_id = operations.find_or_create(expr.value, {})
        work_plan.add_operation(op_id, expr.value, {})
        return op_id
    
    elif isinstance(expr, ECall):
        # If it's a variable reference without arguments
        if not expr.arguments:
            val = env.try_find(expr.identifier)
            if val is None:
                # If not found, create a new operation with the identifier
                op_id = operations.find_or_create(expr.identifier, {})
                work_plan.add_operation(op_id, expr.identifier, {})
                return op_id
            
            if isinstance(val, OperationVal):
                return val.operation_id
            
            call_stack: Stack = [(expr.identifier, expr.position)] + current_stack
            fail_with_stacktrace(
                f"Function '{expr.identifier}' called without arguments", call_stack
            )
        
        # It's a function call with arguments
        this_stack: Stack = [(expr.identifier, expr.position)] + current_stack
        args_ops = [
            reduce_expression(env, operations, work_plan, arg, this_stack)
            for arg in expr.arguments
        ]
        
        # Convert list to dict with string numeric keys
        args_dict = {str(i): op_id for i, op_id in enumerate(args_ops)}
        
        # Check if it's a variable with arguments
        val = env.try_find(expr.identifier)
        if val is None:
            # If not found, create a new operation with the identifier and arguments
            op_id = operations.find_or_create(expr.identifier, args_dict)
            work_plan.add_operation(op_id, expr.identifier, args_dict)
            return op_id
        
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
            return reduce_expression(func_env, operations, work_plan, val.expression, func_stack)
    
    # This should never happen if the parser is correct
    fail("Internal error in reducer: unknown expression type")
    return ""


def reduce_command(
    env: Environment,
    operations: Operations,
    work_plan: WorkPlan,
    parsed_imports: Set[str],
    command: Command,
) -> tuple[Environment, List[Command]]:
    """Reduce a command and update the environment"""
    
    if isinstance(command, Declaration):
        if not command.arguments:
            # It's a simple variable declaration
            op_id = reduce_expression(env, operations, work_plan, command.expression)
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
        op_id = reduce_expression(env, operations, work_plan, command.expression)
        work_plan.add_goal("save", command.identifier, op_id)
        return env, []
    
    elif isinstance(command, Print):
        op_id = reduce_expression(env, operations, work_plan, command.expression)
        work_plan.add_goal("print", command.identifier, op_id)
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
            return env, []
    
    # This should never happen if the parser is correct
    fail("Internal error in reducer: unknown command type")
    return env, []


def reduce_program(program: Program) -> WorkPlan:
    """Reduce a program to a work plan"""
    operations = Operations()
    work_plan = WorkPlan()
    env = Environment()
    parsed_imports: Set[str] = set()
    
    # Make a copy of the commands list that we can modify
    commands = list(program.commands)
    
    # Process commands until there are none left
    while commands:
        command = commands.pop(0)
        env, imports = reduce_command(env, operations, work_plan, parsed_imports, command)
        commands = imports + commands
    
    return work_plan
