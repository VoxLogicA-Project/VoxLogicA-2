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
    Tuple,
    Union,
    Any
)
from dataclasses import dataclass, field
from pathlib import Path

import hashlib
import canonicaljson
import logging

logger = logging.getLogger(__name__)

from voxlogica.lazy import LazyCompilation, ForLoopCompilation
from voxlogica.parser import (
    Expression,
    ECall,
    ENumber,
    EBool,
    EString,
    EFor,
    ELet,
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

@dataclass  
class ClosureValue:
    """Closure value that captures a variable, expression, and environment"""
    variable: str  # The parameter name (e.g., 'x' in lambda x: ...)
    expression: Expression  # The body expression as AST
    environment: 'Environment'  # The captured environment
    workplan: 'WorkPlan'  # Reference to the workplan for context
    
    def __call__(self, value: Any) -> Any:
        """Execute the closure with the given value for the variable - returns computed result via storage system"""
        try:
            logger.debug(f"Executing closure: variable={self.variable}, expression={self.expression.to_syntax()}")
            
            # For non-serializable objects (like Images), we can't create constant nodes
            # Instead, we need to handle them differently in the environment
            try:
                # Try to add the value as a constant node in the workplan
                value_node = ConstantValue(value=value)
                value_id = self.workplan.add_node(value_node)
                value_dval = OperationVal(value_id)
                logger.debug(f"Added value node {value_id[:8]}... = {type(value).__name__}")
            except (TypeError, ValueError) as serialize_error:
                # Value is not serializable (e.g., SimpleITK Image)
                # Create a special handling for non-serializable values
                logger.debug(f"Value {type(value).__name__} is not serializable, using direct binding")
                
                # For non-serializable values, we need to store them temporarily and 
                # use a placeholder in the environment
                import uuid
                temp_id = f"temp_{uuid.uuid4().hex[:16]}"
                
                # Store the non-serializable value in a temporary location
                # We'll use the execution engine's storage for this
                try:
                    from voxlogica.execution import get_execution_engine
                    engine = get_execution_engine()
                    # Use memory cache for non-serializable objects
                    engine.storage._memory_cache[temp_id] = value
                    value_dval = OperationVal(temp_id)
                    logger.debug(f"Stored non-serializable value with temp ID {temp_id[:8]}...")
                except Exception as e:
                    logger.warning(f"Failed to store non-serializable value: {e}")
                    # As a fallback, return the original value directly without serialization
                    # This will bypass the normal DAG construction for this value
                    logger.debug(f"Using direct value binding for non-serializable {type(value).__name__}")
                    # We'll create a temporary direct value binding
                    temp_id = f"direct_value_{id(value)}"
                    value_dval = OperationVal(temp_id)
            
            # Create environment with the variable bound to this value
            env_with_binding = self.environment.bind(self.variable, value_dval)
            
            # Reduce the expression with this environment, using the existing workplan
            # This builds the DAG lazily without executing anything
            result_id = reduce_expression(env_with_binding, self.workplan, self.expression)
            
            logger.debug(f"Reduced expression to result_id {result_id[:8]}...")
            
            # Use the storage system to resolve the operation ID
            try:
                from voxlogica.execution import get_execution_engine
                engine = get_execution_engine()
                
                # Check if the result is already computed and stored
                if engine.storage.exists(result_id):
                    result = engine.storage.retrieve(result_id)
                    logger.debug(f"Retrieved cached result for {result_id[:8]}...")
                    return result
                else:
                    # The result isn't cached yet - we need to execute it
                    logger.debug(f"Operation {result_id[:8]}... not yet computed, executing now")
                    
                    # Get the operation from the workplan
                    if result_id in self.workplan.nodes:
                        operation = self.workplan.nodes[result_id]
                        if isinstance(operation, Operation):
                            # Execute the operation directly using the engine's execution logic
                            try:
                                # Execute the operation directly
                                result = self._execute_operation_directly(engine, result_id, operation)
                                logger.debug(f"Executed operation {result_id[:8]}... with result: {type(result)}")
                                return result
                            except Exception as exec_e:
                                logger.warning(f"Failed to execute operation {result_id[:8]}...: {exec_e}")
                                # Return operation ID as fallback
                                return result_id
                        elif isinstance(operation, ConstantValue):
                            # It's a constant value
                            return operation.value
                        else:
                            # Other node types, return operation ID
                            return result_id
                    else:
                        # Operation not found in workplan, return operation ID
                        logger.debug(f"Operation {result_id[:8]}... not found in workplan")
                        return result_id
                    
            except Exception as e:
                logger.warning(f"Failed to access storage for {result_id[:8]}...: {e}")
                # Fallback: return the operation ID
                return result_id
                
        except Exception as e:
            logger.warning(f"Closure execution failed: {e}")
            import traceback
            logger.warning(f"Closure execution traceback: {traceback.format_exc()}")
            # Fallback to returning the input value
            return value

    def _execute_operation_directly(self, engine, operation_id: str, operation: 'Operation') -> Any:
        """Execute a single operation directly and store the result"""
        try:
            # First check if result already exists
            if engine.storage.exists(operation_id):
                return engine.storage.retrieve(operation_id)
            
            # Try to mark as running, with retry logic for database locking
            import time
            for attempt in range(3):
                try:
                    if engine.storage.mark_running(operation_id):
                        break
                    else:
                        # Already being computed by another process, wait and retrieve
                        time.sleep(0.1 * attempt)
                        if engine.storage.exists(operation_id):
                            return engine.storage.retrieve(operation_id)
                except Exception as e:
                    if "locked" in str(e).lower() and attempt < 2:
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    else:
                        raise
            else:
                # If we couldn't mark as running after retries, try to retrieve result
                if engine.storage.exists(operation_id):
                    return engine.storage.retrieve(operation_id)
                else:
                    # As a last resort, return the operation ID
                    return operation_id
            
            # Load the primitive function
            primitive_func = engine.primitives.load_primitive(operation.operator)
            
            # Resolve arguments recursively
            resolved_args = {}
            for key, arg_id in operation.arguments.items():
                if arg_id in self.workplan.nodes:
                    arg_node = self.workplan.nodes[arg_id]
                    if isinstance(arg_node, ConstantValue):
                        resolved_args[key] = arg_node.value
                    elif isinstance(arg_node, Operation):
                        # Recursively execute dependencies
                        resolved_args[key] = self._execute_operation_directly(engine, arg_id, arg_node)
                    else:
                        resolved_args[key] = arg_id
                else:
                    # Try to retrieve from storage first
                    if engine.storage.exists(arg_id):
                        resolved_args[key] = engine.storage.retrieve(arg_id)
                    else:
                        # Check if it's a temporary value in memory cache
                        if hasattr(engine.storage, '_memory_cache') and arg_id in engine.storage._memory_cache:
                            resolved_args[key] = engine.storage._memory_cache[arg_id]
                        else:
                            # Use the argument ID as-is (might be a literal value)
                            resolved_args[key] = arg_id
            
            # Map numeric argument keys to semantic names for primitives
            resolved_args = self._map_arguments_to_semantic_names(operation.operator, resolved_args)
            
            # Execute the primitive
            result = primitive_func(**resolved_args)
            
            # Store the result (concurrent execution may cause benign database contention)
            try:
                engine.storage.store(operation_id, result)
            except Exception as store_e:
                # Database locks are expected during concurrent execution - not an error
                error_msg = str(store_e).lower()
                if "locked" in error_msg or "busy" in error_msg or "database" in error_msg:
                    logger.debug(f"Concurrent storage attempt for {operation_id[:8]}... (benign): {store_e}")
                else:
                    logger.warning(f"Failed to store result for {operation_id[:8]}...: {store_e}")
            
            return result
                
        except Exception as e:
            logger.error(f"Failed to execute operation {operation_id[:8]}...: {e}")
            try:
                engine.storage.mark_failed(operation_id, str(e))
            except:
                pass  # Ignore storage errors when marking failed
            # Return the operation ID as fallback instead of raising
            return operation_id

    def _map_arguments_to_semantic_names(self, operator: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map numeric argument keys to semantic names based on operator.
        
        Converts positional argument keys ('0', '1', etc.) to meaningful names
        like 'left'/'right' for binary operators, improving primitive function signatures.
        
        Args:
            operator: Operator to map arguments for
            args: Arguments with numeric keys
            
        Returns:
            Arguments with semantic keys where applicable
        """
        operator_str = str(operator).lower()
        
        # Binary operators mapping
        if operator_str in ['+', 'add', 'addition', '-', 'sub', 'subtract', 
                           '*', 'mul', '/', 'div', 'divide']:
            if '0' in args and '1' in args:
                return {'left': args['0'], 'right': args['1']}
        
        # SimpleITK functions that expect positional arguments as numeric keys
        # (Multiply and other *args functions should keep numeric keys)
        
        # If no mapping found, return original args
        return args

type Node = ConstantValue | Operation | ClosureValue

@dataclass
class Goal:
    """Goal with operation type, operation ID, and name"""
    operation: str  # "print" or "save"
    id: NodeId
    name: str

@dataclass
class WorkPlan:
    """A purely lazy DAG of operations with goals - compilation happens on-demand"""
    nodes: Dict[NodeId, Node] = field(default_factory=dict)
    goals: List[Goal] = field(default_factory=list)
    _imported_namespaces: Set[str] = field(default_factory=set)
    lazy_compilations: List[LazyCompilation] = field(default_factory=list)
    for_loop_compilations: List[ForLoopCompilation] = field(default_factory=list)
    _expanded: bool = False

    def add_goal(self, operation: str, operation_id: NodeId, name: str) -> None:
        """Add a goal to the work plan"""
        self.goals.append(Goal(operation, operation_id, name))
    
    def _compute_node_id(self,node: Node) -> NodeId:
        """Compute content-addressed SHA256 ID for an operation"""
        import dataclasses
        
        # Special handling for ClosureValue which contains non-serializable objects
        if isinstance(node, ClosureValue):
            # Create a serializable representation using the expression syntax
            serializable_dict = {
                'type': 'ClosureValue',
                'variable': node.variable,
                'expression_str': node.expression.to_syntax(),
                # We can't include environment or workplan in the hash since they're not serializable
                # This means closure identity is based on variable name and expression only
            }
            canonical_json = canonicaljson.encode_canonical_json(serializable_dict)
        else:
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

    def add_lazy_compilation(self, lazy_comp: LazyCompilation) -> None:
        """Add a lazy compilation to be processed on-demand"""
        self.lazy_compilations.append(lazy_comp)

    def add_for_loop_compilation(self, for_loop_comp: ForLoopCompilation) -> None:
        """Add a for loop compilation to be processed on-demand"""
        self.for_loop_compilations.append(for_loop_comp)

    def _expand_and_compile_all(self) -> None:
        """Expand all lazy compilations - triggered on first access to operations"""
        if self._expanded:
            return
            
        # Process all lazy compilations
        for lazy_comp in self.lazy_compilations:
            # For now, we'll process them immediately
            # TODO: Implement parameter binding and lazy evaluation
            pass

        # Process all for loop compilations with Dask bag expansion
        for for_loop_comp in self.for_loop_compilations:
            self._expand_for_loop(for_loop_comp)
            
        self._expanded = True

    def _expand_for_loop(self, for_loop_comp: ForLoopCompilation) -> NodeId:
        """Expand a for loop using Dask bags"""
        logger.debug(f"Expanding for loop: {for_loop_comp}")
        
        # First, evaluate the iterable expression to get the dataset
        iterable_id = reduce_expression(
            for_loop_comp.environment, 
            self, 
            for_loop_comp.iterable_expr,
            for_loop_comp.stack
        )
        
        # Create a map operation that applies the body to each element
        # The for loop "for x in iterable do body" becomes "map(lambda x: body, iterable)"
        # This will be represented as a "dask_map" operation
        
        # Create constant values for the variable name and body expression
        variable_const = ConstantValue(for_loop_comp.variable)
        variable_id = self.add_node(variable_const)
        
        body_const = ConstantValue(str(for_loop_comp.body_expr))
        body_id = self.add_node(body_const)
        
        map_args = {
            "0": iterable_id,  # The iterable (should be a Dask bag)
            "variable": variable_id,  # The loop variable name as constant
            "body": body_id  # The body expression (as string for now) as constant
        }
        
        map_node = Operation(operator="dask_map", arguments=map_args)
        map_id = self.add_node(map_node)
        
        logger.debug(f"Created dask_map operation: {map_id}")
        return map_id

    @property
    def operations(self) -> Dict[NodeId, Operation]:
        """Return only Operation nodes - triggers lazy compilation on first access"""
        if not self._expanded:
            self._expand_and_compile_all()
        return {k: v for k, v in self.nodes.items() if isinstance(v, Operation)}

    @property
    def constants(self) -> Dict[NodeId, ConstantValue]:
        """Return only ConstantValue nodes."""
        return {k: v for k, v in self.nodes.items() if isinstance(v, ConstantValue)}
    
    @property
    def closures(self) -> Dict[NodeId, ClosureValue]:
        """Return only ClosureValue nodes."""
        return {k: v for k, v in self.nodes.items() if isinstance(v, ClosureValue)}

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

    elif isinstance(expr, EFor):
        # Handle for loop expression - add to lazy compilation and return a future result ID
        for_loop_comp = ForLoopCompilation(
            variable=expr.variable,
            iterable_expr=expr.iterable,
            body_expr=expr.body,
            environment=env,
            stack=current_stack
        )
        
        # Immediately expand the for loop to get the map operation
        map_id = _expand_for_loop_now(for_loop_comp, work_plan)
        return map_id

    elif isinstance(expr, ELet):
        # Handle let expression: let x = value in body
        # 1. Reduce the value expression in the current environment
        value_id = reduce_expression(env, work_plan, expr.value, current_stack)
        
        # 2. Create a new environment with the variable bound to the value
        value_dval = OperationVal(value_id)
        new_env = env.bind(expr.variable, value_dval)
        
        # 3. Reduce the body expression in the new environment
        body_id = reduce_expression(new_env, work_plan, expr.body, current_stack)
        
        return body_id

    # This should never happen if the parser is correct
    raise RuntimeError("Internal error in reducer: unknown expression type")
    return ""


def _expand_for_loop_now(for_loop_comp: ForLoopCompilation, work_plan: 'WorkPlan') -> NodeId:
    """Expand a for loop immediately during expression reduction"""
    logger.debug(f"Expanding for loop: {for_loop_comp}")
    
    # First, evaluate the iterable expression to get the dataset
    iterable_id = reduce_expression(
        for_loop_comp.environment, 
        work_plan, 
        for_loop_comp.iterable_expr,
        for_loop_comp.stack
    )
    
    # Create a closure that captures the variable and body expression with environment
    closure = ClosureValue(
        variable=for_loop_comp.variable,
        expression=for_loop_comp.body_expr,
        environment=for_loop_comp.environment,
        workplan=work_plan
    )
    closure_id = work_plan.add_node(closure)
    
    # Create a map operation that applies the closure to each element
    map_args = {
        "0": iterable_id,  # The iterable (should be a Dask bag)
        "closure": closure_id  # The closure to apply
    }
    
    map_node = Operation(operator="dask_map", arguments=map_args)
    map_id = work_plan.add_node(map_node)
    
    logger.debug(f"Created dask_map operation with closure: {map_id}")
    return map_id


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
