"""
VoxLogica-2 Execution Engine

This module provides distributed execution semantics for VoxLogica-2 workplans.
It compiles workplans to Dask lazy delayed graphs and handles actual execution
of DAG nodes with content-addressed deduplication and persistent storage.
"""

import logging
from typing import Dict, Set, Any, Optional, List, Callable, Union, Type, TYPE_CHECKING
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
from voxlogica.main import VERBOSE_LEVEL

if TYPE_CHECKING:
    from voxlogica.reducer import Environment

logger = logging.getLogger("voxlogica.execution")

# Type aliases for custom serializers
SerializerFunc = Callable[[Any, Path], None]
TypeSerializerMap = Dict[Type, SerializerFunc]
SerializerRegistry = Dict[str, TypeSerializerMap]

class SuffixMatcher:
    """Handles suffix matching for custom serializers"""
    
    def match_suffix(self, filepath: Path, available_suffixes: Set[str]) -> Optional[str]:
        """
        Match longest available suffix from filepath
        
        Args:
            filepath: Target file path
            available_suffixes: Set of known suffix patterns
            
        Returns:
            Longest matching suffix or None
        """
        path_str = str(filepath).lower()
        
        # Sort by length descending - longest match wins
        suffixes = sorted(available_suffixes, key=len, reverse=True)
        
        for suffix in suffixes:
            if path_str.endswith(suffix.lower()):
                return suffix
                
        return None

class CustomSerializerRegistry:
    """Registry for custom file format serializers"""
    
    def __init__(self):
        self._serializers: SerializerRegistry = {}
        self._loaded = False
    
    def register_serializers(self, suffix: str, type_serializers: TypeSerializerMap) -> None:
        """Register serializers for a file suffix"""
        if suffix not in self._serializers:
            self._serializers[suffix] = {}
        
        self._serializers[suffix].update(type_serializers)
    
    def get_serializer(self, suffix: str, obj_type: Type) -> Optional[SerializerFunc]:
        """Get serializer for suffix and object type"""
        if suffix not in self._serializers:
            return None
            
        type_map = self._serializers[suffix]
        
        # Exact type match first
        if obj_type in type_map:
            return type_map[obj_type]
        
        # Inheritance-based match
        for registered_type, serializer in type_map.items():
            if isinstance(obj_type, type) and issubclass(obj_type, registered_type):
                return serializer
                
        # Check if obj_type is an instance and try isinstance checks
        for registered_type, serializer in type_map.items():
            try:
                # Create a dummy instance to test isinstance (if obj_type is a class)
                if hasattr(obj_type, '__mro__'):
                    for base_type in obj_type.__mro__:
                        if base_type == registered_type:
                            return serializer
            except:
                pass
        
        return None
    
    def get_available_suffixes(self) -> Set[str]:
        """Get all registered suffixes"""
        self._ensure_loaded()
        return set(self._serializers.keys())
    
    def _ensure_loaded(self) -> None:
        """Lazy load serializers from primitive modules"""
        if self._loaded:
            return
            
        # Discover and load serializers from all primitive modules
        self._load_from_primitives()
        self._loaded = True
    
    def _load_from_primitives(self) -> None:
        """Load serializers from all loaded primitive modules"""
        try:
            # Import primitive modules and collect serializers
            # For now, specifically handle SimpleITK since it's the main use case
            self._load_simpleitk_serializers()
            # Also load dataset serializers
            self._load_dataset_serializers()
        except Exception as e:
            logger.warning(f"Failed to load some serializers: {e}")
    
    def _load_simpleitk_serializers(self) -> None:
        """Load serializers from SimpleITK primitive module"""
        try:
            # Check if SimpleITK primitives are available
            from voxlogica.primitives.simpleitk import get_serializers
            serializers = get_serializers()
            for suffix, type_map in serializers.items():
                self.register_serializers(suffix, type_map)
                logger.debug(f"Registered serializers for {suffix} from SimpleITK")
        except ImportError:
            logger.debug("SimpleITK primitives not available")
        except Exception as e:
            logger.warning(f"Failed to load SimpleITK serializers: {e}")

    def _load_dataset_serializers(self) -> None:
        """Load serializers from dataset primitive module"""
        try:
            # Check if dataset primitives are available
            from voxlogica.primitives.dataset import get_serializers
            serializers = get_serializers()
            for suffix, type_map in serializers.items():
                self.register_serializers(suffix, type_map)
                logger.debug(f"Registered serializers for {suffix} from dataset")
        except ImportError:
            logger.debug("Dataset primitives not available")
        except Exception as e:
            logger.warning(f"Failed to load dataset serializers: {e}")

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
    """Namespace-aware loader for primitive operations"""
    
    def __init__(self, primitives_dir: Optional[Path] = None):
        if primitives_dir is None:
            # Default to primitives directory next to this module
            primitives_dir = Path(__file__).parent / "primitives"
        
        self.primitives_dir = primitives_dir
        self._cache: Dict[str, Callable] = {}
        self._namespaces: Dict[str, Dict[str, Callable]] = {}
        self._imported_namespaces: Set[str] = set()
        self._ensure_primitives_dir()
        self._discover_namespaces()
    
    def _ensure_primitives_dir(self):
        """Ensure primitives directory exists and has __init__.py"""
        self.primitives_dir.mkdir(exist_ok=True)
        init_file = self.primitives_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("# VoxLogica-2 Primitives Directory")
    
    def _discover_namespaces(self):
        """Discover and initialize all namespaces"""
        # Always import default namespace for backward compatibility
        self._import_namespace('default')
        
        # Discover all namespace directories
        for item in self.primitives_dir.iterdir():
            if item.is_dir() and not item.name.startswith('_'):
                namespace_name = item.name
                if namespace_name not in self._namespaces:
                    self._load_namespace(namespace_name)
    
    def _load_namespace(self, namespace_name: str):
        """Load a namespace and its primitives"""
        if namespace_name in self._namespaces:
            return
        
        try:
            namespace_dir = self.primitives_dir / namespace_name
            if not namespace_dir.exists():
                logger.warning(f"Namespace directory does not exist: {namespace_name}")
                return
            
            # Import the namespace module
            namespace_module_path = f"voxlogica.primitives.{namespace_name}"
            namespace_module = importlib.import_module(namespace_module_path)
            
            # Load static primitives (files in the namespace directory)
            static_primitives = {}
            for item in namespace_dir.iterdir():
                if item.is_file() and item.suffix == '.py' and not item.name.startswith('_'):
                    module_name = item.stem
                    try:
                        module_path = f"{namespace_module_path}.{module_name}"
                        module = importlib.import_module(module_path)
                        if hasattr(module, 'execute'):
                            static_primitives[module_name] = module.execute
                            logger.debug(f"Loaded static primitive '{module_name}' from {namespace_name}")
                    except Exception as e:
                        logger.error(f"Error loading static primitive {module_name} from {namespace_name}: {e}")
            
            # Load dynamic primitives if namespace supports it
            dynamic_primitives = {}
            if hasattr(namespace_module, 'register_primitives'):
                try:
                    dynamic_primitives = namespace_module.register_primitives()
                    logger.debug(f"Loaded {len(dynamic_primitives)} dynamic primitives from {namespace_name}")
                except Exception as e:
                    logger.error(f"Error loading dynamic primitives from {namespace_name}: {e}")
            
            # Combine static and dynamic primitives
            all_primitives = {**static_primitives, **dynamic_primitives}
            self._namespaces[namespace_name] = all_primitives
            
            logger.debug(f"Loaded namespace '{namespace_name}' with {len(all_primitives)} primitives")
            
        except Exception as e:
            logger.error(f"Error loading namespace '{namespace_name}': {e}")
            self._namespaces[namespace_name] = {}
    
    def _import_namespace(self, namespace_name: str):
        """Mark a namespace as imported (available for unqualified lookups)"""
        if namespace_name not in self._namespaces:
            self._load_namespace(namespace_name)
        self._imported_namespaces.add(namespace_name)
        logger.debug(f"Imported namespace: {namespace_name}")
    
    def load_primitive(self, operator_name: str) -> Optional[Callable]:
        """Load a primitive operation by name (qualified or unqualified)"""
        if operator_name in self._cache:
            return self._cache[operator_name]
        
        primitive_func = None
        
        # Check if operator name is qualified (namespace.primitive)
        if '.' in operator_name:
            namespace_name, primitive_name = operator_name.split('.', 1)
            primitive_func = self._load_qualified_primitive(namespace_name, primitive_name)
        else:
            # Unqualified name - check in resolution order
            primitive_func = self._load_unqualified_primitive(operator_name)
        
        if primitive_func:
            self._cache[operator_name] = primitive_func
            logger.debug(f"Loaded primitive '{operator_name}'")
        
        return primitive_func
    
    def _load_qualified_primitive(self, namespace_name: str, primitive_name: str) -> Optional[Callable]:
        """Load a primitive from a specific namespace"""
        if namespace_name not in self._namespaces:
            self._load_namespace(namespace_name)
        
        if namespace_name in self._namespaces:
            primitives = self._namespaces[namespace_name]
            return primitives.get(primitive_name)
        
        return None
    
    def _load_unqualified_primitive(self, operator_name: str) -> Optional[Callable]:
        """Load an unqualified primitive following resolution order"""
        # Resolution order: default namespace -> imported namespaces
        search_order = ['default'] + [ns for ns in self._imported_namespaces if ns != 'default']
        
        for namespace_name in search_order:
            if namespace_name in self._namespaces:
                primitives = self._namespaces[namespace_name]
                
                # Check direct name match
                if operator_name in primitives:
                    return primitives[operator_name]
                
                # Check operator aliases for default namespace
                if namespace_name == 'default':
                    aliased_name = self._resolve_operator_alias(operator_name)
                    if aliased_name and aliased_name in primitives:
                        return primitives[aliased_name]
        
        return None
    
    def _resolve_operator_alias(self, operator_name: str) -> Optional[str]:
        """Resolve operator aliases for backward compatibility"""
        aliases = {
            '+': 'addition',
            'add': 'addition',
            '-': 'subtraction', 
            'sub': 'subtraction',
            'subtract': 'subtraction',
            '*': 'multiplication',
            'mul': 'multiplication',
            'multiply': 'multiplication',
            '/': 'division',
            'div': 'division',
            'divide': 'division',
            'print': 'print_primitive',
        }
        return aliases.get(operator_name)
    
    def import_namespace(self, namespace_name: str):
        """Import a namespace for unqualified access"""
        self._import_namespace(namespace_name)
    
    def list_namespaces(self) -> List[str]:
        """List all available namespaces"""
        return list(self._namespaces.keys())
    
    def list_primitives(self, namespace_name: Optional[str] = None) -> Dict[str, str]:
        """List primitives in a namespace or all namespaces"""
        if namespace_name:
            if namespace_name not in self._namespaces:
                self._load_namespace(namespace_name)
            
            # Try to get descriptions from namespace module
            try:
                namespace_module_path = f"voxlogica.primitives.{namespace_name}"
                namespace_module = importlib.import_module(namespace_module_path)
                if hasattr(namespace_module, 'list_primitives'):
                    return namespace_module.list_primitives()
            except Exception:
                pass
            
            # Fallback to primitive names without descriptions
            if namespace_name in self._namespaces:
                return {name: f"Primitive from {namespace_name} namespace" 
                       for name in self._namespaces[namespace_name].keys()}
            return {}
        else:
            # List all primitives from all namespaces
            all_primitives = {}
            for ns_name in self._namespaces:
                ns_primitives = self.list_primitives(ns_name)
                for prim_name, description in ns_primitives.items():
                    qualified_name = f"{ns_name}.{prim_name}"
                    all_primitives[qualified_name] = description
            return all_primitives

class ExecutionEngine:
    """
    Execution engine that compiles VoxLogica workplans to Dask delayed graphs
    and manages execution with persistent storage backend.
    """
    
    def __init__(self, storage_backend: Optional[StorageBackend] = None, 
                 primitives_loader: Optional[PrimitivesLoader] = None,
                 environment: Optional['Environment'] = None):
        self.storage = storage_backend or get_storage()
        self.primitives = primitives_loader or PrimitivesLoader()
        self.environment = environment  # Optional: enables dynamic compilation
        
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
                
        logger.log(VERBOSE_LEVEL,f"Starting execution {execution_id[:8]}... with {len(workplan.operations)} operations")
        
        start_time = time.time()
        
        try:
            # Handle namespace imports from the workplan
            if hasattr(workplan, '_imported_namespaces'):
                for namespace_name in workplan._imported_namespaces:
                    self.primitives.import_namespace(namespace_name)
                    logger.debug(f"Imported namespace '{namespace_name}' for execution")
            
            # Create execution session
            session = ExecutionSession(execution_id, workplan, self.storage, self.primitives, self.environment)
            
            with self._lock:
                self._active_executions[execution_id] = session
            
            try:
                # Execute the workplan
                completed, failed = session.execute()
                
                execution_time = time.time() - start_time
                logger.log(VERBOSE_LEVEL,f"Execution {execution_id[:8]}... completed in {execution_time:.2f}s")
                logger.log(VERBOSE_LEVEL,f"  Completed: {len(completed)}/{len(workplan.operations)}")
                logger.log(VERBOSE_LEVEL,f"  Failed: {len(failed)}")
                
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
                 storage: StorageBackend, primitives: PrimitivesLoader,
                 environment: Optional['Environment'] = None):
        self.execution_id = execution_id
        self.workplan = workplan
        self.storage = storage
        self.primitives = primitives
        self.environment = environment
        
        # Execution state
        self.completed: Set[NodeId] = set()
        self.failed: Dict[NodeId, str] = {}
        self.cancelled = False
        self._status_lock = threading.Lock()
        
        # Dask delayed graph
        self.delayed_graph: Dict[NodeId, Any] = {}
        
        # Custom serializer infrastructure
        self._serializer_registry = CustomSerializerRegistry()
        self._suffix_matcher = SuffixMatcher()
        
        # Separate pure operations from side-effect goals
        self.pure_operations: Dict[NodeId, Operation] = {}
        self.goal_operations: Dict[NodeId, Operation] = {}
        self._categorize_operations()
    
    def execute(self) -> tuple[Set[NodeId], Dict[NodeId, str]]:
        """Execute the workplan and return completed/failed operation sets"""
        
        # Set execution context for thread-local access
        set_execution_context(self)
        
        try:
            # Store constants in storage first so they can be retrieved by goals
            self._store_constants()
            
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
                    logger.log(VERBOSE_LEVEL,f"Executing {len(goal_computations)} computation goals with Dask")
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
        finally:
            # Clear execution context
            set_execution_context(None)
    
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
                    
    def _execute_pure_operation(self, operation: Operation, operation_id: NodeId, *dependency_results) -> Any:
        """Execute a single pure operation with content-addressed deduplication"""
        # Set execution context for this thread
        set_execution_context(self)
        
        try:
            if self.cancelled:
                raise Exception("Execution cancelled")
            if self.storage.exists(operation_id):
                logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... found in storage, skipping")
                result = self.storage.retrieve(operation_id)
                with self._status_lock:
                    self.completed.add(operation_id)
                return result
            if not self.storage.mark_running(operation_id):
                logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... being computed by another worker, waiting")
                return self._wait_for_result(operation_id)
            try:
                logger.log(VERBOSE_LEVEL, f"[START] Executing operation {operation_id[:8]}... ({operation.operator})")
                if self._is_literal_operation(operation):
                    result = operation.operator.value if isinstance(operation.operator, ConstantValue) else operation.operator
                    logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... is literal: {result}")
                else:
                    primitive_func = self.primitives.load_primitive(str(operation.operator.value) if isinstance(operation.operator, ConstantValue) else str(operation.operator))
                    if primitive_func is None:
                        raise Exception(f"No primitive implementation for operator: {operation.operator}")
                    resolved_args = self._resolve_arguments(operation, list(dependency_results))
                    logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... resolved args: {list(resolved_args.keys())}")
                    result = primitive_func(**resolved_args)
                # Unwrap ConstantValue if returned as result
                if isinstance(result, ConstantValue):
                    result = result.value
                self.storage.store(operation_id, result)
                with self._status_lock:
                    self.completed.add(operation_id)
                logger.log(VERBOSE_LEVEL, f"[DONE] Operation {operation_id[:8]}... completed successfully")
                return result
            except Exception as e:
                error_msg = f"Operation {operation_id[:8]}... failed: {e}"
                logger.error(error_msg)
                logger.debug(traceback.format_exc())
                self.storage.mark_failed(operation_id, str(e))
                with self._status_lock:
                    self.failed[operation_id] = str(e)
                raise e
        finally:
            # Note: We don't clear the execution context here because Dask 
            # may run operations in the same thread, and we want the context
            # to persist for the duration of the execution session
            pass

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
            # Special handling for Dask bags - compute and display values
            try:
                # Use duck typing to detect Dask bags more safely
                if (hasattr(result, 'compute') and 
                    hasattr(result, 'npartitions') and 
                    hasattr(result, 'map') and
                    callable(getattr(result, 'compute', None))):
                    # This is likely a Dask bag - compute it to get actual values
                    computed_values = result.compute()
                    logger.info(f"{goal.name}={list(computed_values)}")
                else:
                    logger.info(f"{goal.name}={result}")
            except Exception as e:
                # Fallback to regular printing if computation fails
                logger.info(f"{goal.name}={result}")
                logger.debug(f"Dask computation failed for {goal.name}: {e}")
        elif goal.operation == 'save':            
            self._save_result_to_file(result, goal.name, goal.id)
        else:
            raise Exception(f"Unknown goal operation: {goal.operation}")
            
    def _save_result_to_file(self, result, filename: str, operation_id: Optional[str] = None):
        """Save a result to a file with custom serializer support"""
        import json
        import pickle
        import sqlite3
        from pathlib import Path
        from voxlogica.converters.json_converter import WorkPlanJSONEncoder
        filepath = Path(filename)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        ext = filepath.suffix.lower()
        logger.info(f"Saving result to {filepath}")
        
        # Try custom serializers first
        if self._try_custom_serializer(result, filepath):
            return
        
        # For binary files (.bin, no extension), dump raw pickled data from database
        if ext == ".bin" or ext == "":
            if operation_id and self.storage.exists(operation_id):
                # Get raw pickled data directly from database
                with self.storage._get_connection() as conn:
                    cursor = conn.execute("SELECT data FROM results WHERE operation_id = ?", (operation_id,))
                    row = cursor.fetchone()
                    if row:
                        raw_pickled_data = row[0]  # This is the raw pickle bytes
                        with open(filepath, 'wb') as f:
                            f.write(raw_pickled_data)
                        logger.info(f"Saved raw pickled data ({len(raw_pickled_data)} bytes) to {filepath}")
                        return
            # Fallback to regular pickle if no operation_id or not in storage
            with open(filepath, 'wb') as f:
                pickle.dump(result, f)
        elif ext == ".json":
            with open(filepath, 'w') as f:
                json.dump(result, f, indent=2, cls=WorkPlanJSONEncoder)
        elif ext in [".pkl", ".pickle"]:
            with open(filepath, 'wb') as f:
                pickle.dump(result, f)
        else:  # txt format 
            with open(filepath, 'w') as f:
                f.write(str(result))
    
    def _try_custom_serializer(self, result, filepath: Path) -> bool:
        """
        Attempt to save using custom serializer
        
        Returns:
            True if custom serializer was used successfully, False otherwise
        """
        try:
            # Find matching serializer
            available_suffixes = self._serializer_registry.get_available_suffixes()
            suffix = self._suffix_matcher.match_suffix(filepath, available_suffixes)
            
            if not suffix:
                return False
            
            # Get type-appropriate serializer
            serializer = self._serializer_registry.get_serializer(suffix, type(result))
            
            if not serializer:
                logger.debug(f"No serializer found for type {type(result)} with suffix {suffix}")
                return False
            
            # Execute serialization
            logger.log(VERBOSE_LEVEL,f"Using custom serializer for {suffix} format")
            serializer(result, filepath)
            logger.log(VERBOSE_LEVEL,f"Saved {type(result).__name__} to {filepath}")
            
            return True
            
        except Exception as e:
            logger.warning(f"Custom serializer failed for {filepath}: {e}")
            # Fall back to standard serialization
            return False
    
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
        # Compile operations in topological order to ensure dependencies are compiled first
        topo_order = self._topological_sort(dependencies)
        
        for op_id in topo_order:
            if op_id not in self.pure_operations:
                continue
                
            operation = self.pure_operations[op_id]
            
            # Build dependency list in argument order (not dependency set order!)
            dep_delayed = []
            for arg_name in sorted(operation.arguments.keys()):  # Sort to ensure consistent order: '0', '1', '2', etc.
                dep_id = operation.arguments[arg_name]
                if dep_id in dependencies.get(op_id, set()) and dep_id in self.delayed_graph:
                    dep_delayed.append(self.delayed_graph[dep_id])
                elif dep_id in self.delayed_graph:
                    # This dependency might not be in the dependencies dict if it's a constant
                    dep_delayed.append(self.delayed_graph[dep_id])
            
            # Pass dependency results as individual arguments, not a list
            # This allows Dask to properly resolve dependencies
            self.delayed_graph[op_id] = delayed(self._execute_pure_operation)(
                operation, op_id, *dep_delayed
            )

    def _topological_sort(self, dependencies: Dict[NodeId, Set[NodeId]]) -> List[NodeId]:
        """Topological sort of operations based on dependencies"""
        # Kahn's algorithm for topological sorting
        in_degree = {op_id: 0 for op_id in self.pure_operations}
        
        # Calculate in-degrees (how many dependencies each operation has)
        for op_id, deps in dependencies.items():
            if op_id in in_degree:
                in_degree[op_id] = len(deps)
        
        # Start with nodes that have no dependencies
        queue = deque([op_id for op_id, degree in in_degree.items() if degree == 0])
        result = []
        
        while queue:
            current = queue.popleft()
            result.append(current)
            
            # Remove this node and update in-degrees of operations that depend on it
            for op_id, deps in dependencies.items():
                if current in deps and op_id in in_degree:
                    in_degree[op_id] -= 1
                    if in_degree[op_id] == 0:
                        queue.append(op_id)
        
        if len(result) != len(self.pure_operations):
            # Debug information
            logger.error("Topological sort failed:")
            logger.error(f"Pure operations: {len(self.pure_operations)}")
            logger.error(f"Sorted result: {len(result)}")
            remaining = set(self.pure_operations.keys()) - set(result)
            logger.error(f"Remaining operations: {[op_id[:8] + '...' for op_id in remaining]}")
            for op_id in remaining:
                if op_id in dependencies:
                    logger.error(f"  {op_id[:8]}... depends on: {[dep_id[:8] + '...' for dep_id in dependencies[op_id]]}")
            raise ValueError("Cycle detected in dependencies")
        
        return result

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

    def _store_constants(self):
        """Store all ConstantValue nodes in storage so they can be retrieved by goals"""
        for node_id, node in self.workplan.nodes.items():
            if isinstance(node, ConstantValue):
                # Check if already stored to avoid duplicate work
                if not self.storage.exists(node_id):
                    # Store the constant value
                    self.storage.store(node_id, node.value)
                    logger.debug(f"Stored constant {node_id[:8]}... = {node.value}")

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

# Thread-local execution context for dynamic compilation
_execution_context = threading.local()

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

def set_execution_context(session: Optional['ExecutionSession']):
    """Set the current execution context for thread-local access"""
    _execution_context.session = session

def get_execution_context() -> Optional['ExecutionSession']:
    """Get the current execution context"""
    return getattr(_execution_context, 'session', None)

def get_execution_environment():
    """Get the environment from the current execution context"""
    session = get_execution_context()
    if session:
        return session.environment
    return None

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
