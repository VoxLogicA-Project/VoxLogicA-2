"""
VoxLogica-2 Execution Engine

This module provides distributed execution semantics for VoxLogica-2 workplans.
It compiles workplans to Dask lazy delayed graphs and handles actual execution
of DAG nodes with content-addressed deduplication and persistent storage.
"""

import logging
from typing import Dict, Set, Any, Optional, List, Callable, Union
from collections import defaultdict, deque
from dataclasses import dataclass
import dask
from dask.delayed import delayed
from dask.base import compute
from dask.distributed import as_completed
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
import traceback
import importlib
import sys
from pathlib import Path

from voxlogica.reducer import WorkPlan, Operation, ConstantValue, Goal, NodeId
from voxlogica.storage import StorageBackend, get_storage
from voxlogica.converters.json_converter import WorkPlanJSONEncoder

logger = logging.getLogger("voxlogica.execution")

@dataclass
class ExecutionResult:
    """Result of workplan execution"""
    success: bool
    completed_operations: Set[NodeId]
    failed_operations: Dict[NodeId, str]  # operation_id -> error message
    execution_time: float
    total_operations: int

@dataclass 
class ExecutionStatus:
    """Status of ongoing execution"""
    running: bool
    completed: Set[NodeId]
    failed: Dict[NodeId, str]
    total: int
    progress: float  # 0.0 to 1.0

class PrimitivesLoader:
    """Dynamic loader for primitive operations from primitives/ directory"""
    
    def __init__(self, primitives_dir: Optional[Path] = None):
        if primitives_dir is None:
            # Default to primitives directory next to this module
            primitives_dir = Path(__file__).parent / "primitives"
        
        self.primitives_dir = primitives_dir
        self._cache: Dict[str, Callable] = {}
        self._ensure_primitives_dir()
    
    def _ensure_primitives_dir(self):
        """Ensure primitives directory exists and has __init__.py"""
        self.primitives_dir.mkdir(exist_ok=True)
        init_file = self.primitives_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("# VoxLogica-2 Primitives Directory")
    
    def load_primitive(self, operator_name: str) -> Optional[Callable]:
        """Load a primitive operation by name"""
        if operator_name in self._cache:
            return self._cache[operator_name]
        
        try:
            # Convert operator name to module name
            module_name = self._operator_to_module_name(operator_name)
            module_path = f"voxlogica.primitives.{module_name}"
            
            # Import the module
            module = importlib.import_module(module_path)
            
            # Look for execute function
            if hasattr(module, 'execute'):
                primitive_func = module.execute
                self._cache[operator_name] = primitive_func
                logger.debug(f"Loaded primitive '{operator_name}' from {module_path}")
                return primitive_func
            else:
                logger.warning(f"Primitive module {module_path} has no 'execute' function")
                return None
                
        except ImportError as e:
            logger.debug(f"Could not load primitive '{operator_name}': {e}")
            return None
        except Exception as e:
            logger.error(f"Error loading primitive '{operator_name}': {e}")
            return None
    
    def _operator_to_module_name(self, operator_name: str) -> str:
        """Convert operator name to Python module name"""
        # Handle special cases
        if operator_name in ['+', 'add', 'addition']:
            return 'addition'
        elif operator_name in ['-', 'sub', 'subtract']:
            return 'subtraction'
        elif operator_name in ['*', 'mul', 'multiply']:
            return 'multiplication'
        elif operator_name in ['/', 'div', 'divide']:
            return 'division'
        elif operator_name == 'print':
            return 'print_primitive'
        else:
            # Default: use operator name as module name, sanitized
            return operator_name.replace('-', '_').replace(' ', '_').lower()

class ExecutionEngine:
    """
    Execution engine that compiles VoxLogica workplans to Dask delayed graphs
    and manages execution with persistent storage backend.
    """
    
    def __init__(self, storage_backend: Optional[StorageBackend] = None, 
                 primitives_loader: Optional[PrimitivesLoader] = None):
        self.storage = storage_backend or get_storage()
        self.primitives = primitives_loader or PrimitivesLoader()
        
        # Execution state
        self._active_executions: Dict[str, 'ExecutionSession'] = {}
        self._lock = threading.Lock()
    
    def execute_workplan(self, workplan: WorkPlan, execution_id: Optional[str] = None) -> ExecutionResult:
        """
        Execute a workplan and return results
        
        Args:
            workplan: The workplan to execute
            execution_id: Optional ID for this execution (defaults to hash of goals)
            
        Returns:
            ExecutionResult with success status and operation results
        """
        if execution_id is None:
            execution_id = self._generate_execution_id(workplan)
                
        logger.info(f"Starting execution {execution_id[:8]}... with {len(workplan.operations)} operations")
        
        start_time = time.time()
        
        try:
            # Create execution session
            session = ExecutionSession(execution_id, workplan, self.storage, self.primitives)
            
            with self._lock:
                self._active_executions[execution_id] = session
            
            try:
                # Execute the workplan
                completed, failed = session.execute()
                
                execution_time = time.time() - start_time
                logger.info(f"Execution {execution_id[:8]}... completed in {execution_time:.2f}s")
                logger.info(f"  Completed: {len(completed)}/{len(workplan.operations)}")
                logger.info(f"  Failed: {len(failed)}")
                
                return ExecutionResult(
                    success=(len(failed) == 0),
                    completed_operations=completed,
                    failed_operations=failed,
                    execution_time=execution_time,
                    total_operations=len(workplan.operations)
                )
                
            finally:
                with self._lock:
                    self._active_executions.pop(execution_id, None)
                    
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Execution {execution_id[:8]}... failed after {execution_time:.2f}s: {e}")
            return ExecutionResult(
                success=False,
                completed_operations=set(),
                failed_operations={"execution": str(e)},
                execution_time=execution_time,
                total_operations=len(workplan.operations)
            )
    
    def get_execution_status(self, execution_id: str) -> Optional[ExecutionStatus]:
        """Get status of running execution"""
        with self._lock:
            session = self._active_executions.get(execution_id)
            if session:
                return session.get_status()
            return None
    
    def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution"""
        with self._lock:
            session = self._active_executions.get(execution_id)
            if session:
                session.cancel()
                return True
            return False
    
    def list_active_executions(self) -> List[str]:
        """List IDs of currently active executions"""
        with self._lock:
            return list(self._active_executions.keys())
    
    def _generate_execution_id(self, workplan: WorkPlan) -> str:
        """Generate execution ID from workplan goals"""
        import hashlib
        goals_str = ",".join(sorted(f"{goal.operation}:{goal.id}:{goal.name}" for goal in workplan.goals))
        return hashlib.sha256(goals_str.encode()).hexdigest()

class ExecutionSession:
    """
    Individual execution session that handles the actual compilation
    and execution of a workplan using Dask delayed.
    """
    
    def __init__(self, execution_id: str, workplan: WorkPlan, 
                 storage: StorageBackend, primitives: PrimitivesLoader):
        self.execution_id = execution_id
        self.workplan = workplan
        self.storage = storage
        self.primitives = primitives
        
        # Execution state
        self.completed: Set[NodeId] = set()
        self.failed: Dict[NodeId, str] = {}
        self.cancelled = False
        self._status_lock = threading.Lock()
        
        # Dask delayed graph
        self.delayed_graph: Dict[NodeId, Any] = {}
        
        # Separate pure operations from side-effect goals
        self.pure_operations: Dict[NodeId, Operation] = {}
        self.goal_operations: Dict[NodeId, Operation] = {}
        self._categorize_operations()
    
    def execute(self) -> tuple[Set[NodeId], Dict[NodeId, str]]:
        """Execute the workplan and return completed/failed operation sets"""
        
        # Build dependency graph for topological ordering
        dependencies = self._build_dependency_graph()
        
        # Compile pure operations to Dask delayed graph
        self._compile_pure_operations_to_dask(dependencies)
        
        # Execute the pure computation DAG first
        computation_goals = [goal.id for goal in self.workplan.goals 
                           if goal.id in self.pure_operations]
        
        if computation_goals:
            goal_computations = [self.delayed_graph[goal_id] for goal_id in computation_goals]
            
            try:
                logger.info(f"Executing {len(goal_computations)} computation goals with Dask")
                compute(*goal_computations)
            except Exception as e:
                logger.error(f"Dask computation failed: {e}")
                with self._status_lock:
                    self.failed["dask_computation"] = str(e)
                    return self.completed.copy(), self.failed.copy()
        
        # Execute side-effect goals (print, save, etc.)
        self._execute_goal_operations()
        
        # Return final results
        with self._status_lock:
            return self.completed.copy(), self.failed.copy()
    
    def _categorize_operations(self):
        """Categorize nodes into pure operations vs side-effect goals"""
        side_effect_operators = {'print', 'save', 'output', 'write', 'display'}
        for node_id, node in self.workplan.nodes.items():
            if isinstance(node, Operation):
                op_str = str(node.operator.value) if isinstance(node.operator, ConstantValue) else str(node.operator)
                if op_str.lower() in side_effect_operators:
                    self.goal_operations[node_id] = node
                else:
                    self.pure_operations[node_id] = node
        # constants are not added to pure_operations or goal_operations

    def _execute_goal_operations(self):
        """Execute side-effect operations (print, save, etc.) as special Dask nodes"""
        for goal in self.workplan.goals:
            try:
                # Execute the goal operation with the computed result
                self._execute_goal_with_result(goal)
                
                with self._status_lock:
                    self.completed.add(goal.id)
            except Exception as e:
                logger.error(f"Goal operation {goal.operation} '{goal.name}' failed: {e}")
                with self._status_lock:
                    self.failed[goal.id] = str(e)
                    
    def _execute_pure_operation(self, operation: Operation, operation_id: NodeId, dependency_results: List[Any]) -> Any:
        """Execute a single pure operation with content-addressed deduplication"""
        if self.cancelled:
            raise Exception("Execution cancelled")
        if self.storage.exists(operation_id):
            logger.info(f"Operation {operation_id[:8]}... found in storage, skipping")
            result = self.storage.retrieve(operation_id)
            with self._status_lock:
                self.completed.add(operation_id)
            return result
        if not self.storage.mark_running(operation_id):
            logger.info(f"Operation {operation_id[:8]}... being computed by another worker, waiting")
            return self._wait_for_result(operation_id)
        try:
            logger.info(f"Executing operation {operation_id[:8]}... ({operation.operator})")
            if self._is_literal_operation(operation):
                result = operation.operator.value if isinstance(operation.operator, ConstantValue) else operation.operator
                logger.info(f"Operation {operation_id[:8]}... is literal: {result}")
            else:
                primitive_func = self.primitives.load_primitive(str(operation.operator.value) if isinstance(operation.operator, ConstantValue) else str(operation.operator))
                if primitive_func is None:
                    raise Exception(f"No primitive implementation for operator: {operation.operator}")
                resolved_args = self._resolve_arguments(operation, dependency_results)
                result = primitive_func(**resolved_args)
            # Unwrap ConstantValue if returned as result
            if isinstance(result, ConstantValue):
                result = result.value
            self.storage.store(operation_id, result)
            with self._status_lock:
                self.completed.add(operation_id)
            logger.info(f"Operation {operation_id[:8]}... completed successfully")
            return result
        except Exception as e:
            error_msg = f"Operation {operation_id[:8]}... failed: {e}"
            logger.error(error_msg)
            logger.debug(traceback.format_exc())
            self.storage.mark_failed(operation_id, str(e))
            with self._status_lock:
                self.failed[operation_id] = str(e)
            raise e

    def _execute_goal_with_result(self, goal):
        """Execute a goal operation with the result from storage"""
        # Get the computed result from storage
        if self.storage.exists(goal.id):
            result = self.storage.retrieve(goal.id)
        else:
            raise Exception(f"Missing computed result for goal operation {goal.id}")
        # Unwrap ConstantValue if present
        if isinstance(result, ConstantValue):
            result = result.value
        # Execute the appropriate goal action
        if goal.operation == 'print':
            print(f"{goal.name}: {result}")
        elif goal.operation == 'save':
            self._save_result_to_file(result, goal.name)
        else:
            raise Exception(f"Unknown goal operation: {goal.operation}")
            
    def _save_result_to_file(self, result, filename: str):
        """Save a result to a file"""
        import json
        import pickle
        from pathlib import Path
        from voxlogica.converters.json_converter import WorkPlanJSONEncoder
        filepath = Path(filename)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        ext = filepath.suffix.lower()
        logger.info(f"Saving result to {filepath}")
        if ext == ".json":
            with open(filepath, 'w') as f:
                json.dump(result, f, indent=2, cls=WorkPlanJSONEncoder)
        elif ext in [".pkl", ".pickle"]:
            with open(filepath, 'wb') as f:
                pickle.dump(result, f)
        else:  # txt format or no extension
            with open(filepath, 'w') as f:
                f.write(str(result))
    
    def cancel(self):
        """Cancel execution"""
        self.cancelled = True
    
    def get_status(self) -> ExecutionStatus:
        """Get current execution status"""
        with self._status_lock:
            total_ops = len(self.workplan.operations)
            completed_count = len(self.completed)
            progress = completed_count / total_ops if total_ops > 0 else 0.0
            
            return ExecutionStatus(
                running=not self.cancelled,
                completed=self.completed.copy(),
                failed=self.failed.copy(),
                total=total_ops,
                progress=progress
            )
    
    def _build_dependency_graph(self) -> Dict[NodeId, Set[NodeId]]:
        """Build dependency graph (operation -> dependencies) for pure operations only"""
        dependencies: Dict[NodeId, Set[NodeId]] = defaultdict(set)
        for op_id, operation in self.pure_operations.items():
            for arg_name, dep_id in operation.arguments.items():
                if dep_id in self.pure_operations:
                    dependencies[op_id].add(dep_id)
        return dict(dependencies)
    
    def _compile_pure_operations_to_dask(self, dependencies: Dict[NodeId, Set[NodeId]]):
        """Compile pure operations to Dask delayed graph"""
        for op_id, operation in self.pure_operations.items():
            dep_delayed = []
            for dep_id in dependencies.get(op_id, set()):
                if dep_id in self.delayed_graph:
                    dep_delayed.append(self.delayed_graph[dep_id])
            self.delayed_graph[op_id] = delayed(self._execute_pure_operation)(
                operation, op_id, dep_delayed
            )

    def _is_literal_operation(self, operation: Operation) -> bool:
        """Check if an operation represents a literal value (constant)"""
        # Literal operations have no arguments and their operator is a ConstantValue or primitive
        return not operation.arguments and isinstance(operation.operator, ConstantValue)
    
    def _resolve_arguments(self, operation: Operation, dependency_results: List[Any]) -> Dict[str, Any]:
        """Resolve operation arguments, substituting dependency results from pure operations"""
        resolved = {}
        dep_results_map = {}
        
        # Map dependency IDs to their results
        dep_idx = 0
        for arg_name, arg_value in operation.arguments.items():
            if arg_value in self.pure_operations:
                # This is a dependency reference to a pure operation
                if dep_idx < len(dependency_results):
                    dep_results_map[arg_value] = dependency_results[dep_idx]
                    dep_idx += 1
                else:
                    # Try to get from storage
                    if self.storage.exists(arg_value):
                        dep_results_map[arg_value] = self.storage.retrieve(arg_value)
                    else:
                        raise Exception(f"Missing dependency result for {arg_value}")
            elif arg_value in self.workplan.nodes:
                # Check if it's a ConstantValue node
                node = self.workplan.nodes[arg_value]
                if isinstance(node, ConstantValue):
                    dep_results_map[arg_value] = node.value
                else:
                    # It's an Operation node that should have been handled above
                    raise Exception(f"Unhandled node type for argument {arg_value}: {type(node)}")
        
        # Resolve all arguments
        for arg_name, arg_value in operation.arguments.items():
            if arg_value in dep_results_map:
                resolved[arg_name] = dep_results_map[arg_value]
            else:
                # This branch should not be reached with content-addressed IDs
                raise RuntimeError(f"Unexpected direct value for argument '{arg_name}': {arg_value}. This indicates a logic error in argument resolution.")
        
        # Map numeric string keys to semantic argument names for known operators
        resolved = self._map_arguments_to_semantic_names(operation.operator, resolved)
        
        return resolved
    
    def _map_arguments_to_semantic_names(self, operator: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        """Map numeric argument keys to semantic names based on operator"""
        operator_str = str(operator).lower()
        
        # Binary operators mapping
        if operator_str in ['+', 'add', 'addition', '-', 'sub', 'subtract', 
                           '*', 'mul', 'multiply', '/', 'div', 'divide']:
            if '0' in args and '1' in args:
                return {'left': args['0'], 'right': args['1']}
        
        # If no mapping found, return original args
        return args
    
    def _wait_for_result(self, operation_id: NodeId, timeout: float = 300.0) -> Any:
        """Wait for another worker to complete the operation"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.cancelled:
                raise Exception("Execution cancelled")
            
            if self.storage.exists(operation_id):
                result = self.storage.retrieve(operation_id)
                with self._status_lock:
                    self.completed.add(operation_id)
                return result
            
            time.sleep(0.1)  # Poll every 100ms
        
        raise Exception(f"Timeout waiting for operation {operation_id[:8]}... to complete")

def unwrap_node(obj):
    import dataclasses
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        # Recursively unwrap all dataclass fields
        return {k: unwrap_node(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {unwrap_node(k): unwrap_node(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [unwrap_node(i) for i in obj]
    return obj

# Global execution engine instance
_execution_engine: Optional[ExecutionEngine] = None
_engine_lock = threading.Lock()

def get_execution_engine() -> ExecutionEngine:
    """Get the global execution engine instance"""
    global _execution_engine
    
    with _engine_lock:
        if _execution_engine is None:
            _execution_engine = ExecutionEngine()
        return _execution_engine

def set_execution_engine(engine: ExecutionEngine):
    """Set the global execution engine instance"""
    global _execution_engine
    
    with _engine_lock:
        _execution_engine = engine

# Convenience functions
def execute_workplan(workplan: WorkPlan, execution_id: Optional[str] = None) -> ExecutionResult:
    """Execute a workplan using the global execution engine"""
    return get_execution_engine().execute_workplan(workplan, execution_id)

def get_execution_status(execution_id: str) -> Optional[ExecutionStatus]:
    """Get status of a running execution"""
    return get_execution_engine().get_execution_status(execution_id)

def cancel_execution(execution_id: str) -> bool:
    """Cancel a running execution"""
    return get_execution_engine().cancel_execution(execution_id)
