"""
VoxLogica-2 Execution Engine

This module provides distributed execution semantics for VoxLogica-2 workplans.
It compiles workplans to Dask lazy delayed graphs and handles actual execution
of DAG nodes with content-addressed deduplication and persistent storage.
"""

import logging
from typing import Dict, Set, Any, Optional, List, Callable, Union, Type
from collections import defaultdict, deque
from dataclasses import dataclass
from dask.delayed import delayed
from dask.base import compute
from dask.distributed import Client, as_completed, Future
import time
import threading
from concurrent.futures import ThreadPoolExecutor, Future as ConcurrentFuture
import traceback
import importlib
import sys
from pathlib import Path

from voxlogica.reducer import WorkPlan, Operation, ConstantValue, ClosureValue, Goal, NodeId
from voxlogica.storage import StorageBackend, get_storage
from voxlogica.converters.json_converter import WorkPlanJSONEncoder
from voxlogica.main import VERBOSE_LEVEL

logger = logging.getLogger("voxlogica.execution")

# Global shared Dask client for all executions
_shared_dask_client: Optional[Client] = None

# Global futures table for lock-free operation coordination
_operation_futures: Dict[str, Any] = {}  # Any to handle both Dask and concurrent.futures.Future
_operation_futures_lock = threading.RLock()

def get_operation_future(operation_id: str) -> Optional[Any]:
    """Get the Dask future for an operation if it exists."""
    with _operation_futures_lock:
        return _operation_futures.get(operation_id)

def set_operation_future(operation_id: str, future: Any) -> bool:
    """
    Set the Dask future for an operation atomically.
    
    Returns:
        True if future was set, False if one already existed
    """
    with _operation_futures_lock:
        if operation_id in _operation_futures:
            return False
        _operation_futures[operation_id] = future
        return True

def remove_operation_future(operation_id: str) -> None:
    """Remove the Dask future for an operation after completion."""
    with _operation_futures_lock:
        _operation_futures.pop(operation_id, None)

def get_shared_dask_client(enable_dashboard: bool = False) -> Optional[Client]:
    """
    Get or create the shared Dask client for all workplan executions.
    
    Uses a single threaded Dask client that all workplans share, enabling
    coordinated resource management and task scheduling across multiple
    concurrent workplan executions.
    
    Args:
        enable_dashboard: Whether to enable the Dask web dashboard for debugging
    
    Returns:
        Shared Dask client or None if creation fails
    """
    global _shared_dask_client
    
    if _shared_dask_client is None:
        try:
            # Configure Dask to disable diagnostics and dashboard by default
            from dask import config
            if not enable_dashboard:
                config.set({'distributed.diagnostics.enabled': False})
                config.set({'distributed.admin.bokeh': False})
            
            # Suppress the jupyter-server-proxy warning specifically
            import logging
            proxy_logger = logging.getLogger('distributed.http.proxy')
            original_level = proxy_logger.level
            proxy_logger.setLevel(logging.WARNING)  # Suppress INFO messages from proxy logger
            
            try:
                # Create local threaded client with controlled resources
                dashboard_address = ":8787" if enable_dashboard else None
                _shared_dask_client = Client(
                    processes=False,  # Use threads, not processes
                    threads_per_worker=4,  # Limit threads per worker
                    n_workers=1,  # Single worker for simplicity
                    memory_limit='2GB',  # Memory limit per worker
                    silence_logs=True,  # Reduce log noise
                    dashboard_address=dashboard_address  # Enable dashboard if requested
                )
                
                if enable_dashboard:
                    logger.info(f"Dask dashboard enabled at: http://localhost:8787")
                    
            finally:
                # Restore original logging level
                proxy_logger.setLevel(original_level)
                
            logger.debug(f"Created shared Dask client: {_shared_dask_client.scheduler_info()['address']}")
        except Exception as e:
            logger.warning(f"Failed to create shared Dask client, using local compute: {e}")
            _shared_dask_client = None
    
    return _shared_dask_client

def close_shared_dask_client():
    """Close the shared Dask client if it exists."""
    global _shared_dask_client
    if _shared_dask_client is not None:
        _shared_dask_client.close()
        _shared_dask_client = None
        logger.debug("Closed shared Dask client")

# Type aliases for custom serializers
SerializerFunc = Callable[[Any, Path], None]
"""Type alias for serializer functions that take a result and filepath."""

TypeSerializerMap = Dict[Type, SerializerFunc]
"""Type alias for mapping Python types to their serializer functions."""

SerializerRegistry = Dict[str, TypeSerializerMap]
"""Type alias for registry mapping file suffixes to type-serializer mappings."""

class SuffixMatcher:
    """
    Handles suffix matching for custom serializers.
    
    Provides logic to match file paths against available suffix patterns,
    supporting longest-match-first resolution for overlapping patterns.
    """
    
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
    """
    Registry for custom file format serializers.
    
    Manages a collection of type-specific serializers for different file formats,
    providing lazy loading from primitive modules and inheritance-based matching.
    Supports automatic discovery of serializers from the primitives system.
    """
    
    def __init__(self):
        self._serializers: SerializerRegistry = {}
        self._loaded = False
    
    def register_serializers(self, suffix: str, type_serializers: TypeSerializerMap) -> None:
        """
        Register serializers for a file suffix.
        
        Args:
            suffix: File suffix pattern (e.g., '.nii.gz', '.png')
            type_serializers: Mapping from Python types to serializer functions
        """
        if suffix not in self._serializers:
            self._serializers[suffix] = {}
        
        self._serializers[suffix].update(type_serializers)
    
    def get_serializer(self, suffix: str, obj_type: Type) -> Optional[SerializerFunc]:
        """
        Get serializer for suffix and object type.
        
        Uses exact type matching first, then inheritance-based matching.
        
        Args:
            suffix: File suffix to match
            obj_type: Python type of object to serialize
            
        Returns:
            Matching serializer function or None
        """
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
        """
        Get all registered suffixes.
        
        Returns:
            Set of all file suffix patterns that have registered serializers
        """
        self._ensure_loaded()
        return set(self._serializers.keys())
    
    def _ensure_loaded(self) -> None:
        """
        Lazy load serializers from primitive modules.
        
        Ensures serializers are loaded exactly once, discovering them from
        all available primitive modules in the system.
        """
        if self._loaded:
            return
            
        # Discover and load serializers from all primitive modules
        self._load_from_primitives()
        self._loaded = True
    
    def _load_from_primitives(self) -> None:
        """
        Load serializers from all loaded primitive modules.
        
        Discovers and loads custom serializers from primitive modules,
        currently focusing on SimpleITK as the primary use case.
        """
        # TODO: this should be provided by modules not hardcoded
        try:
            # Import primitive modules and collect serializers
            # For now, specifically handle SimpleITK since it's the main use case
            self._load_simpleitk_serializers()
        except Exception as e:
            logger.warning(f"Failed to load some serializers: {e}")
    
    def _load_simpleitk_serializers(self) -> None:        
        """
        Load serializers from SimpleITK primitive module.
        
        Attempts to load custom serializers for medical imaging formats
        (NIFTI, NRRD, etc.) from the SimpleITK primitives module.
        """        
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

@dataclass
class ExecutionResult:
    """
    Result of workplan execution.
    
    Contains comprehensive information about the execution outcome,
    including success status, completed/failed operations, timing,
    and total operation count.
    """
    success: bool
    completed_operations: Set[NodeId]
    failed_operations: Dict[NodeId, str]  # operation_id -> error message
    execution_time: float
    total_operations: int

@dataclass 
class ExecutionStatus:
    """
    Status of ongoing execution.
    
    Provides real-time information about execution progress,
    including running state, completion/failure counts, and
    overall progress percentage.
    """
    running: bool
    completed: Set[NodeId]
    failed: Dict[NodeId, str]
    total: int
    progress: float  # 0.0 to 1.0

class PrimitivesLoader:
    """
    Namespace-aware loader for primitive operations.
    
    Manages loading and resolution of primitive operations from multiple namespaces,
    supporting both static (file-based) and dynamic (programmatically registered)
    primitives. Provides qualified and unqualified name resolution with proper
    precedence rules.
    """
    
    def __init__(self, primitives_dir: Optional[Path] = None):
        """
        Initialize primitives loader.
        
        Args:
            primitives_dir: Directory containing primitive modules.
                          Defaults to 'primitives' directory next to this module.
        """
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
        """
        Ensure primitives directory exists and has __init__.py.
        
        Creates the primitives directory and its __init__.py file if they
        don't exist, making it a proper Python module.
        """
        self.primitives_dir.mkdir(exist_ok=True)
        init_file = self.primitives_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("# VoxLogica-2 Primitives Directory")
    
    def _discover_namespaces(self):
        """
        Discover and initialize all namespaces.
        
        Automatically discovers all namespace directories and loads them.
        Always imports the 'default' namespace for backward compatibility.
        """
        # Always import default namespace for backward compatibility
        self._import_namespace('default')
        
        # Discover all namespace directories
        for item in self.primitives_dir.iterdir():
            if item.is_dir() and not item.name.startswith('_'):
                namespace_name = item.name
                if namespace_name not in self._namespaces:
                    self._load_namespace(namespace_name)
    
    def _load_namespace(self, namespace_name: str):
        """
        Load a namespace and its primitives.
        
        Loads both static primitives (from .py files in the namespace directory)
        and dynamic primitives (from the namespace module's register_primitives function).
        
        Args:
            namespace_name: Name of the namespace to load
        """
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
        """
        Mark a namespace as imported (available for unqualified lookups).
        
        Args:
            namespace_name: Name of the namespace to import
        """
        if namespace_name not in self._namespaces:
            self._load_namespace(namespace_name)
        self._imported_namespaces.add(namespace_name)
        logger.debug(f"Imported namespace: {namespace_name}")
    
    def load_primitive(self, operator_name: str) -> Optional[Callable]:
        """
        Load a primitive operation by name (qualified or unqualified).
        
        Supports both qualified names (namespace.primitive) and unqualified names.
        Unqualified names are resolved using namespace precedence: default first,
        then imported namespaces in import order.
        
        Args:
            operator_name: Name of the primitive to load
            
        Returns:
            Callable primitive function or None if not found
        """
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
        """
        Load a primitive from a specific namespace.
        
        Args:
            namespace_name: Target namespace name
            primitive_name: Name of primitive within the namespace
            
        Returns:
            Callable primitive function or None if not found
        """
        if namespace_name not in self._namespaces:
            self._load_namespace(namespace_name)
        
        if namespace_name in self._namespaces:
            primitives = self._namespaces[namespace_name]
            return primitives.get(primitive_name)
        
        return None
    
    def _load_unqualified_primitive(self, operator_name: str) -> Optional[Callable]:
        """
        Load an unqualified primitive following resolution order.
        
        Resolution order: default namespace -> imported namespaces in order.
        
        Args:
            operator_name: Unqualified primitive name
            
        Returns:
            Callable primitive function or None if not found
        """
        # Resolution order: default namespace -> imported namespaces
        search_order = ['default'] + [ns for ns in self._imported_namespaces if ns != 'default']
        
        for namespace_name in search_order:
            if namespace_name in self._namespaces:
                primitives = self._namespaces[namespace_name]
                
                # Check direct name match
                if operator_name in primitives:
                    return primitives[operator_name]
        
        return None
 
    
    def import_namespace(self, namespace_name: str):
        """
        Import a namespace for unqualified access.
        
        Args:
            namespace_name: Name of the namespace to import
        """
        self._import_namespace(namespace_name)
    
    def list_namespaces(self) -> List[str]:
        """
        List all available namespaces.
        
        Returns:
            List of namespace names that have been discovered or loaded
        """
        return list(self._namespaces.keys())
    
    def list_primitives(self, namespace_name: Optional[str] = None) -> Dict[str, str]:
        """
        List primitives in a namespace or all namespaces.
        
        Args:
            namespace_name: Specific namespace to list, or None for all namespaces
            
        Returns:
            Dictionary mapping primitive names to their descriptions
        """
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
    Execution engine that uses a shared Dask client for coordinated resource management.
    
    All workplan executions share the same Dask scheduler/queue, enabling optimal
    resource utilization and preventing the "1000 concurrent workplans competing
    for CPU cores" problem. Tasks from all workplans are submitted to a single
    shared queue and executed with coordinated scheduling.
    """
    
    def __init__(self, storage_backend: Optional[StorageBackend] = None, 
                 primitives_loader: Optional[PrimitivesLoader] = None,
                 auto_cleanup_stale_operations: bool = True):
        """
        Initialize execution engine with shared Dask client.
        
        Args:
            storage_backend: Storage backend for results. Uses default if None.
            primitives_loader: Primitives loader. Creates default if None.
            auto_cleanup_stale_operations: If True, cleanup stale operations on startup
        """
        self.storage = storage_backend or get_storage()
        self.primitives = primitives_loader or PrimitivesLoader()
        
        # Get or create shared Dask client for coordinated execution
        self.dask_client = get_shared_dask_client()
        
        # Clean up any dangling operations from previous crashes
        if auto_cleanup_stale_operations:
            try:
                cleaned_count = self.storage.cleanup_failed_executions(max_age_hours=1)
                if cleaned_count > 0:
                    logger.info(f"Cleaned up {cleaned_count} stale operations from previous sessions")
            except Exception as e:
                logger.warning(f"Failed to cleanup stale operations on startup: {e}")
    
    def execute_workplan(self, workplan: WorkPlan, execution_id: Optional[str] = None, dask_dashboard: bool = False) -> ExecutionResult:
        """
        Execute a workplan and return results.
        
        Each execution is independent. Coordination between executions happens
        automatically through the content-addressed storage backend.
        
        Args:
            workplan: The workplan to execute
            execution_id: Optional ID for this execution (defaults to hash of goals) (currently never set explicitly by callers)
            dask_dashboard: Whether to enable the Dask web dashboard for real-time debugging

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
            
            # Create execution session with shared Dask client
            # Recreate client if dashboard setting changed
            if dask_dashboard:
                # Close existing client if it exists
                close_shared_dask_client()
                # Create new client with dashboard enabled
                self.dask_client = get_shared_dask_client(enable_dashboard=True)
            else:
                # Use existing client or create new one without dashboard
                self.dask_client = get_shared_dask_client(enable_dashboard=False)
            
            session = ExecutionSession(execution_id, workplan, self.storage, self.primitives, self.dask_client)
            
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
    
    def _generate_execution_id(self, workplan: WorkPlan) -> str:
        """
        Generate execution ID from workplan goals.
        
        Creates a deterministic hash based on the workplan's goals,
        ensuring consistent IDs for identical workplans.
        
        Args:
            workplan: WorkPlan to generate ID for
            
        Returns:
            SHA256 hash as hexadecimal string
        """
        import hashlib
        goals_str = ",".join(sorted(f"{goal.operation}:{goal.id}:{goal.name}" for goal in workplan.goals))
        return hashlib.sha256(goals_str.encode()).hexdigest()

class ExecutionSession:
    """
    Individual execution session that handles the actual compilation
    and execution of a workplan using Dask delayed.
    
    Manages the complete lifecycle of a single workplan execution:
    - Compiles all operations to unified Dask delayed graph
    - Handles distributed execution with content-addressed storage
    - All operations (including print, save) are treated uniformly with deterministic caching
    - Provides execution status and cancellation support
    """
    
    def __init__(self, execution_id: str, workplan: WorkPlan, 
                 storage: StorageBackend, primitives: PrimitivesLoader,
                 dask_client: Optional[Client] = None):
        """
        Initialize execution session.
        
        Args:
            execution_id: Unique identifier for this execution
            workplan: WorkPlan to execute
            storage: Storage backend for results
            primitives: Primitives loader for operations
            dask_client: Optional shared Dask client for coordinated execution
        """
        self.execution_id = execution_id
        self.workplan = workplan
        self.storage = storage
        self.primitives = primitives
        self.dask_client = dask_client
        
        # Execution state
        self.completed: Set[NodeId] = set()
        self.failed: Dict[NodeId, str] = {}
        self.cancelled = False
        
        # Dask delayed graph
        self.delayed_graph: Dict[NodeId, Any] = {}
        
        # Custom serializer infrastructure
        self._serializer_registry = CustomSerializerRegistry()
        self._suffix_matcher = SuffixMatcher()
        
        # All operations are treated uniformly
        self.operations: Dict[NodeId, Operation] = {}
        self._collect_operations()
    
    def execute(self) -> tuple[Set[NodeId], Dict[NodeId, str]]:
        """
        Execute the workplan and return completed/failed operation sets.
        
        Orchestrates the complete execution process:
        1. Store constants in storage for goal access
        2. Build dependency graph for topological ordering
        3. Compile all operations to unified Dask delayed graph  
        4. Execute all computations (let bindings) using Dask with deterministic caching
        5. Execute goal commands (print, save) separately to ensure side effects always run
        
        Returns:
            Tuple of (completed_operations, failed_operations)
        """
        
        # Store constants in storage first so they can be retrieved by goals
        self._store_constants()
        
        # Build dependency graph for topological ordering
        dependencies = self._build_dependency_graph()
        
        # Compile all operations to unified Dask delayed graph
        self._compile_operations_to_dask(dependencies)
        
        # Execute all computations referenced by goals (let bindings)
        computation_operations = [self.delayed_graph[goal.id] for goal in self.workplan.goals 
                                if goal.id in self.delayed_graph]
        
        if computation_operations:
            try:
                logger.log(VERBOSE_LEVEL,f"Executing {len(computation_operations)} computation operations with Dask")
                # Use local threaded scheduler to avoid serialization issues while still
                # benefiting from the shared client's resource coordination
                from dask.threaded import get as threaded_get
                compute(*computation_operations, scheduler=threaded_get)
            except Exception as e:
                logger.error(f"Dask computation failed: {e}")
                self.failed["dask_computation"] = str(e)
                return self.completed.copy(), self.failed.copy()
        
        # Execute goal commands (observations/side effects) separately - these always run
        self._execute_goal_commands()
        
        # Return final results
        return self.completed.copy(), self.failed.copy()
    
    def _collect_operations(self):
        """
        Collect all operations from the workplan.
        
        All operations are treated uniformly - no categorization into pure vs side-effect.
        Even operations like print and save are deterministic and can be cached.
        """
        for node_id, node in self.workplan.nodes.items():
            if isinstance(node, Operation):
                self.operations[node_id] = node
        # constants are not added to operations

    def _execute_goal_commands(self):
        """
        Execute goal commands (print, save) separately from computations.
        
        Goal commands are observations/side effects that should always execute,
        even if their referenced computation results are cached. This respects
        the semantic distinction between let bindings (cacheable computations)
        and goal commands (observations that must always run).
        """
        for goal in self.workplan.goals:
            try:
                # Get the computed result from storage
                if self.storage.exists(goal.id):
                    result = self.storage.retrieve(goal.id)
                else:
                    raise Exception(f"Missing computed result for goal '{goal.name}' (operation {goal.id})")
                
                # Unwrap ConstantValue if present
                if isinstance(result, ConstantValue):
                    result = result.value
                
                # Execute the goal command - these always run regardless of caching
                self._execute_goal_command(goal, result)
                
                logger.log(VERBOSE_LEVEL, f"Goal command '{goal.operation}' for '{goal.name}' executed successfully")
                
            except Exception as e:
                logger.error(f"Goal command '{goal.operation}' for '{goal.name}' failed: {e}")
                self.failed[f"goal_{goal.operation}_{goal.name}"] = str(e)

    def _execute_goal_command(self, goal: Goal, result: Any):
        """
        Execute a single goal command with its computed result.
        
        Args:
            goal: Goal object containing operation type and parameters
            result: The computed result to use for the goal command
        """
        if goal.operation == 'print':
            # Use the print primitive to execute the side effect
            try:
                print_primitive = self.primitives.load_primitive('print_primitive')
                if print_primitive:
                    # Call print primitive with proper arguments - this will print to console
                    output = print_primitive(**{'0': goal.name, '1': result})
                    logger.debug(f"Print primitive output: {output}")
                else:
                    # Fallback to direct printing if print primitive not available
                    output = f"{goal.name}={result}"
                    print(output)
                    logger.debug(f"Fallback print output: {output}")
            except Exception as e:
                logger.warning(f"Print primitive failed, using fallback: {e}")
                output = f"{goal.name}={result}"
                print(output)
                logger.debug(f"Fallback print output: {output}")
                
        elif goal.operation == 'save':
            # Execute save operation
            self._save_result_to_file(result, goal.name, goal.id)
            
        else:
            raise Exception(f"Unknown goal operation: {goal.operation}")

    def _execute_operation(self, operation: Operation, operation_id: NodeId, *dependency_results) -> Any:
        """
        Execute a single operation with content-addressed deduplication.
        
        Implements the unified execution logic for all operations (including print, save):
        - Checks storage for existing results (deduplication)
        - Handles distributed execution coordination using global futures table
        - Loads and invokes primitive functions
        - Stores results in content-addressed storage
        - All operations are treated deterministically and can be cached
        
        Args:
            operation: Operation to execute
            operation_id: Content-addressed ID of the operation
            *dependency_results: Results from dependency operations
            
        Returns:
            Result of the operation execution
        """
        if self.cancelled:
            raise Exception("Execution cancelled")
        
        # Check if result already exists (deterministic caching for all operations)
        if self.storage.exists(operation_id):
            logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... found in storage, skipping")
            result = self.storage.retrieve(operation_id)
            self.completed.add(operation_id)
            return result
        
        # Check if another worker is already computing this operation
        existing_future = get_operation_future(operation_id)
        if existing_future is not None:
            logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... has existing future, awaiting...")
            return self._await_operation_future(operation_id, existing_future)
        
        # Try to claim the operation atomically
        if not self.storage.mark_running(operation_id):
            # Someone else claimed it, check for their future
            existing_future = get_operation_future(operation_id)
            if existing_future is not None:
                logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... claimed by another worker, awaiting future...")
                return self._await_operation_future(operation_id, existing_future)
            else:
                # Fall back to storage-based waiting (should be rare)
                logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... claimed but no future found, falling back to storage wait...")
                return self._wait_for_result(operation_id)
        
        # We won the claim - execute with appropriate coordination
        logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... executing (claimed)")
        
        # Create a process-local future for coordination within this process
        # This enables lock-free waiting for other operations that depend on this one
        from concurrent.futures import Future as LocalFuture
        local_future = LocalFuture()
        
        # Register the future in the global table for coordination
        if set_operation_future(operation_id, local_future):
            logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... registered local future for coordination")
        
        try:
            result = self._execute_operation_inner(operation, operation_id, list(dependency_results))
            
            # Complete the future with the result
            local_future.set_result(result)
            
            self.completed.add(operation_id)
            return result
        except Exception as e:
            # Complete the future with the exception
            local_future.set_exception(e)
            
            self.failed[operation_id] = str(e)
            raise e
        finally:
            # Clean up the future from the global table
            remove_operation_future(operation_id)
    
    def _execute_operation_inner(self, operation: Operation, operation_id: NodeId, dependency_results: List[Any]) -> Any:
        """
        Inner operation execution logic (can be called directly or via Dask future).
        
        Args:
            operation: Operation to execute
            operation_id: Content-addressed ID of the operation
            dependency_results: Results from dependency operations
            
        Returns:
            Result of the operation execution
        """
        try:
            logger.log(VERBOSE_LEVEL, f"[START] Executing operation {operation_id[:8]}... ({operation.operator})")
            
            if self._is_literal_operation(operation):
                result = operation.operator.value if isinstance(operation.operator, ConstantValue) else operation.operator
                logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... is literal: {result}")
            else:
                operator_name = str(operation.operator.value) if isinstance(operation.operator, ConstantValue) else str(operation.operator)
                primitive_func = self.primitives.load_primitive(operator_name)
                if primitive_func is None:
                    raise Exception(f"No primitive implementation for operator: {operation.operator}")
                resolved_args = self._resolve_arguments(operation, dependency_results)
                if logger.isEnabledFor(logging.DEBUG):  # REMOVEME instrumentation
                    try:
                        preview = {k: (type(v).__name__, repr(v)[:60]) for k, v in resolved_args.items()}
                        logger.debug("REMOVEME about to invoke primitive %s with args preview=%s", operator_name, preview)
                    except Exception as _dbg:  # noqa: BLE001
                        logger.debug("REMOVEME failed to preview args for %s: %s", operator_name, _dbg)
                logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... resolved args: {list(resolved_args.keys())}")
                result = primitive_func(**resolved_args)
            
            # Unwrap ConstantValue if returned as result
            if isinstance(result, ConstantValue):
                result = result.value
            
            self.storage.store(operation_id, result, ensure_persisted=True)
            
            # Only mark as completed in database if result is persistable
            # Non-persistable results (memory cache only) should not be marked as completed
            # to avoid race conditions when other processes try to access them
            if self.storage.is_persistable(operation_id):
                self.storage.mark_completed(operation_id)
                logger.log(VERBOSE_LEVEL, f"[DONE] Operation {operation_id[:8]}... completed and persisted")
            else:
                logger.log(VERBOSE_LEVEL, f"[DONE] Operation {operation_id[:8]}... completed (memory cache only - not marked in database)")
                
            # Always add to local completed set for this execution session
            self.completed.add(operation_id)
            logger.log(VERBOSE_LEVEL, f"[DONE] Operation {operation_id[:8]}... completed successfully")
            return result
            
        except Exception as e:
            try:  # REMOVEME instrumentation
                op_preview = str(operation.operator)
                error_msg = f"Operation {operation_id[:8]}... ({op_preview}) failed: {e}"
            except Exception:  # noqa: BLE001
                error_msg = f"Operation {operation_id[:8]}... failed: {e}"
            logger.error(error_msg)
            logger.debug(traceback.format_exc())
            self.storage.mark_failed(operation_id, str(e))
            raise e
    
    def _await_operation_future(self, operation_id: NodeId, future: Any) -> Any:
        """
        Await a Dask future for an operation being computed by another worker.
        
        This is the lock-free, timeout-free mechanism for waiting on other workers.
        
        Args:
            operation_id: ID of operation to wait for
            future: Dask future to await
            
        Returns:
            Result of the operation
        """
        try:
            # Wait for the future to complete (no timeout needed)
            result = future.result()
            
            # Retrieve from storage (the winning worker should have stored it)
            if self.storage.exists(operation_id):
                stored_result = self.storage.retrieve(operation_id)
                self.completed.add(operation_id)
                return stored_result
            else:
                # Use the future result as fallback
                self.completed.add(operation_id)
                return result
                
        except Exception as e:
            # Future failed - check if operation was marked as failed in storage
            if hasattr(e, '__cause__') or 'failed' in str(e):
                self.failed[operation_id] = str(e)
            raise e

    def _execute_goal_with_result(self, goal):
        """
        Execute a goal operation with the result from storage.
        
        Retrieves the computed result for a goal and executes the appropriate
        action (print to console or save to file).
        
        Args:
            goal: Goal object containing operation type and parameters
        """
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
            # Use the print primitive to get user-friendly output
            try:
                print_primitive = self.primitives.load_primitive('print_primitive')
                if print_primitive:
                    # Call print primitive with proper arguments
                    output = print_primitive(**{'0': goal.name, '1': result})
                    # The print primitive already prints to console, so we just log for debugging
                    logger.debug(f"Print output: {output}")
                else:
                    # Fallback to direct logging if print primitive not available
                    logger.info(f"{goal.name}={result}")
            except Exception as e:
                logger.warning(f"Print primitive failed, using fallback: {e}")
                logger.info(f"{goal.name}={result}")
        elif goal.operation == 'save':            
            self._save_result_to_file(result, goal.name, goal.id)
        else:
            raise Exception(f"Unknown goal operation: {goal.operation}")
            
    def _save_result_to_file(self, result, filename: str, operation_id: Optional[str] = None):
        """
        Save a result to a file with custom serializer support.
        
        Supports multiple output formats with custom serializers for specialized
        data types (e.g., medical imaging formats via SimpleITK). Falls back to
        standard formats (JSON, pickle, text) if no custom serializer is available.
        
        Args:
            result: Result object to save
            filename: Target filename (extension determines format)
            operation_id: Optional operation ID for raw data access
        """
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
        """
        Cancel execution.
        
        Sets the cancellation flag, which will be checked by running operations
        to gracefully terminate execution.
        """
        self.cancelled = True
    
    def get_status(self) -> ExecutionStatus:
        """
        Get current execution status.
        
        Returns:
            ExecutionStatus with current progress and state information
        """
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
        """
        Build dependency graph (operation -> dependencies) for all operations.
        
        Creates a mapping from each operation to its direct dependencies,
        excluding constants.
        
        Returns:
            Dictionary mapping operation IDs to sets of their dependency IDs
        """
        dependencies: Dict[NodeId, Set[NodeId]] = defaultdict(set)
        for op_id, operation in self.operations.items():
            for arg_name, dep_id in operation.arguments.items():
                if dep_id in self.operations:
                    dependencies[op_id].add(dep_id)
        return dict(dependencies)
    
    def _compile_operations_to_dask(self, dependencies: Dict[NodeId, Set[NodeId]]):
        """
        Compile all operations to unified Dask delayed graph.
        
        Creates a Dask delayed computation graph from all operations,
        ensuring dependencies are properly handled and execution order is maintained.
        All operations (including print, save) are treated uniformly with deterministic caching.
        
        Args:
            dependencies: Dependency graph from _build_dependency_graph
        """
        # Compile all operations in topological order
        topo_order = self._topological_sort(dependencies)
        
        logger.log(VERBOSE_LEVEL, f"Compiling {len(self.operations)} operations to unified Dask delayed graph")
        
        for op_id in topo_order:
            if op_id not in self.operations:
                continue
                
            operation = self.operations[op_id]
            
            # Build dependency list in argument order
            dep_delayed = []
            for arg_name in sorted(operation.arguments.keys()):
                dep_id = operation.arguments[arg_name]
                if dep_id in dependencies.get(op_id, set()) and dep_id in self.delayed_graph:
                    dep_delayed.append(self.delayed_graph[dep_id])
                elif dep_id in self.delayed_graph:
                    dep_delayed.append(self.delayed_graph[dep_id])
            
            # Create delayed operation - all operations use the same execution path
            self.delayed_graph[op_id] = delayed(self._execute_operation)(
                operation, op_id, *dep_delayed
            )
            logger.log(VERBOSE_LEVEL, f"Added operation {op_id[:8]}... to delayed graph")

    def _topological_sort(self, dependencies: Dict[NodeId, Set[NodeId]]) -> List[NodeId]:
        """
        Topological sort of operations based on dependencies.
        
        Uses Kahn's algorithm to produce a dependency-respecting execution order.
        Ensures operations are compiled/executed after their dependencies.
        
        Args:
            dependencies: Dependency graph
            
        Returns:
            List of operation IDs in topological order
            
        Raises:
            ValueError: If a dependency cycle is detected
        """
        # Kahn's algorithm for topological sorting
        in_degree = {op_id: 0 for op_id in self.operations}
        
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
        
        if len(result) != len(self.operations):
            # Debug information
            logger.error("Topological sort failed:")
            logger.error(f"Operations: {len(self.operations)}")
            logger.error(f"Sorted result: {len(result)}")
            remaining = set(self.operations.keys()) - set(result)
            logger.error(f"Remaining operations: {[op_id[:8] + '...' for op_id in remaining]}")
            for op_id in remaining:
                if op_id in dependencies:
                    logger.error(f"  {op_id[:8]}... depends on: {[dep_id[:8] + '...' for dep_id in dependencies[op_id]]}")
            raise ValueError("Cycle detected in dependencies")
        
        return result

    def _topological_sort_subset(self, dependencies: Dict[NodeId, Set[NodeId]], subset: Set[NodeId]) -> List[NodeId]:
        """
        Topological sort of a subset of operations based on dependencies.
        
        Args:
            dependencies: Full dependency graph
            subset: Set of operation IDs to sort
            
        Returns:
            List of operation IDs in topological order
            
        Raises:
            ValueError: If a dependency cycle is detected
        """
        # Create subgraph with only subset operations
        subset_deps = {}
        for op_id in subset:
            subset_deps[op_id] = dependencies.get(op_id, set()).intersection(subset)
        
        # Kahn's algorithm for topological sorting
        in_degree = {op_id: len(subset_deps[op_id]) for op_id in subset}
        queue = deque([op_id for op_id in subset if in_degree[op_id] == 0])
        result = []
        
        while queue:
            current = queue.popleft()
            result.append(current)
            
            # Update in-degrees for nodes that depend on current
            for op_id in subset:
                if current in subset_deps[op_id]:
                    in_degree[op_id] -= 1
                    if in_degree[op_id] == 0:
                        queue.append(op_id)
        
        if len(result) != len(subset):
            remaining = subset - set(result)
            raise ValueError(f"Dependency cycle detected in subset: {remaining}")
        
        return result
        
    def _is_literal_operation(self, operation: Operation) -> bool:
        """
        Check if an operation represents a literal value (constant).
        
        Args:
            operation: Operation to check
            
        Returns:
            True if operation is a literal constant, False otherwise
        """
        # Literal operations have no arguments and their operator is a ConstantValue or primitive
        return not operation.arguments and isinstance(operation.operator, ConstantValue)
    
    def _resolve_arguments(self, operation: Operation, dependency_results: List[Any]) -> Dict[str, Any]:
        """
        Resolve operation arguments, substituting dependency results from pure operations.
        
        Maps operation argument references to actual values, either from dependency
        results or from constant values in the workplan nodes.
        
        Args:
            operation: Operation whose arguments to resolve
            dependency_results: Results from dependency operations
            
        Returns:
            Dictionary mapping argument names to resolved values
        """
        resolved = {}
        dep_results_map = {}
        
        # Map dependency IDs to their results
        dep_idx = 0
        for arg_name, arg_value in operation.arguments.items():
            if arg_value in self.operations:
                # This is a dependency reference to an operation
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
                # Check if it's a ConstantValue, ClosureValue, or Operation node
                node = self.workplan.nodes[arg_value]
                if isinstance(node, ConstantValue):
                    dep_results_map[arg_value] = node.value
                elif isinstance(node, ClosureValue):
                    dep_results_map[arg_value] = node
                elif isinstance(node, Operation):
                    # It's an Operation node that's a dependency - try to get from storage
                    if self.storage.exists(arg_value):
                        dep_results_map[arg_value] = self.storage.retrieve(arg_value)
                    else:
                        raise Exception(f"Missing dependency result for {arg_value}")
                else:
                    raise Exception(f"Unhandled node type for argument {arg_value}: {type(node)}")
            elif isinstance(arg_value, str) and arg_value.startswith("temp_"):
                # Handle temporary storage IDs (used for non-serializable values in for-loops)
                if self.storage.exists(arg_value):
                    dep_results_map[arg_value] = self.storage.retrieve(arg_value)
                else:
                    raise Exception(f"Missing temporary storage result for {arg_value}")
            else:
                raise Exception(f"Unknown argument reference: {arg_value}")
        
        # Resolve all arguments
        for arg_name, arg_value in operation.arguments.items():
            if arg_value in dep_results_map:
                resolved[arg_name] = dep_results_map[arg_value]
            else:
                # This branch should not be reached with content-addressed IDs
                raise RuntimeError(f"Unexpected direct value for argument '{arg_name}': {arg_value}. This indicates a logic error in argument resolution.")
        
        # Map numeric string keys to semantic argument names for known operators
        resolved = self._map_arguments_to_semantic_names(operation.operator, resolved)
        # REMOVEME instrumentation start
        try:
            op_name_lower = str(operation.operator).lower()
            if 'nnunet' in op_name_lower and 'train_directory' in op_name_lower:
                if '4' in resolved:
                    v = resolved.get('4')
                    if v is not None:
                        try:
                            coerced = int(float(v))
                            if coerced != v:  # type: ignore
                                logging.getLogger(__name__).debug("REMOVEME coerced dataset_id %r -> %r (type %s)", v, coerced, type(v).__name__)
                            resolved['4'] = coerced
                        except Exception as _ce:  # noqa: BLE001
                            logging.getLogger(__name__).debug("REMOVEME failed to coerce dataset_id %r: %s", v, _ce)
                logging.getLogger(__name__).debug("REMOVEME resolve_arguments nnunet.train_directory keys=%s", sorted(resolved.keys()))
        except Exception as _outer_dbg:  # noqa: BLE001
            logging.getLogger(__name__).debug("REMOVEME resolve_arguments instrumentation failed: %s", _outer_dbg)
        # REMOVEME instrumentation end
        return resolved
    
    def _map_arguments_to_semantic_names(self, operator: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map numeric argument keys to semantic names based on operator.
        
        Converts positional argument keys ('0', '1', etc.) to meaningful names
        like 'left'/'right' for binary operators, improving primitive function signatures.
        
        Note: SimpleITK functions are excluded from mapping as they expect numeric argument keys.
        
        Args:
            operator: Operator to map arguments for
            args: Arguments with numeric keys
            
        Returns:
            Arguments with semantic keys where applicable
        """
        operator_str = str(operator).lower()
        
        # Check if this is a SimpleITK function - if so, don't map arguments
        # SimpleITK functions need numeric keys ('0', '1') for their *args handling
        if self._is_simpleitk_function(operator_str):
            return args
        
        # Binary operators mapping (only for built-in VoxLogicA primitives)
        if operator_str in ['+', 'add', 'addition', '-', 'sub', 'subtract', 'subtraction',
                           '*', 'mul', 'multiply', 'multiplication', '/', 'div', 'divide', 'division']:
            if '0' in args and '1' in args:
                return {'left': args['0'], 'right': args['1']}
        
        # If no mapping found, return original args
        return args
    
    def _is_simpleitk_function(self, operator_str: str) -> bool:
        """Check if an operator is a SimpleITK function."""
        try:
            # Import SimpleITK primitives and check if operator is registered
            from voxlogica.primitives.simpleitk import register_primitives
            simpleitk_primitives = register_primitives()
            
            # Check both exact name and capitalized versions
            return (operator_str in simpleitk_primitives or 
                    operator_str.capitalize() in simpleitk_primitives or
                    operator_str.upper() in simpleitk_primitives)
        except ImportError:
            # SimpleITK not available
            return False
        except Exception:
            # Any other error, assume not SimpleITK
            return False
    
    def _wait_for_result(self, operation_id: NodeId, timeout: float = 300.0) -> Any:
        """
        Wait for another worker to complete the operation.
        
        Uses process-local futures for lock-free waiting.
        For non-serializable results, does not use database completion waiting.
        
        Args:
            operation_id: ID of operation to wait for
            timeout: Maximum time to wait (only used for serializable results)
            
        Returns:
            Result of the operation
            
        Raises:
            Exception: If execution is cancelled or operation failed
        """
        if self.cancelled:
            raise Exception("Execution cancelled")
        
        # First try to get the operation's future for lock-free waiting
        future = get_operation_future(operation_id)
        if future is not None:
            logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... found future, awaiting without timeout...")
            return self._await_operation_future(operation_id, future)
        
        # Check if result exists in memory cache or storage
        if self.storage.exists(operation_id):
            result = self.storage.retrieve(operation_id)
            if result is not None:
                self.completed.add(operation_id)
                return result
        
        # For threaded execution, use storage-based waiting
        logger.log(VERBOSE_LEVEL, f"Operation {operation_id[:8]}... no future found, using storage wait...")
        try:
            # Use storage's efficient wait mechanism
            result = self.storage.wait_for_completion(operation_id, timeout, allow_non_persistable=False)
            self.completed.add(operation_id)
            return result
            
        except TimeoutError as e:
            raise Exception(str(e))
        except Exception as e:
            # Handle operation failure
            self.failed[operation_id] = str(e)
            raise e


    def _store_constants(self):
        """
        Store all ConstantValue nodes in storage so they can be retrieved by goals.
        
        Pre-populates storage with constant values to ensure they're available
        when goals need to access them during execution.
        """
        for node_id, node in self.workplan.nodes.items():
            if isinstance(node, ConstantValue):
                # Check if already stored to avoid duplicate work
                if not self.storage.exists(node_id):
                    # Store the constant value
                    self.storage.store(node_id, node.value)
                    logger.debug(f"Stored constant {node_id[:8]}... = {node.value}")

    def _execute_missing_dependency(self, dep_id: NodeId, dep_node: Operation):
        """
        Execute a missing dependency for a dask_map operation.
        
        This method recursively executes dependencies that are not in storage
        but are needed by dask_map operations.
        
        Args:
            dep_id: The ID of the missing dependency operation
            dep_node: The operation node that needs to be executed
        """
        # Skip if already executed
        if self.storage.exists(dep_id):
            return
        
        # First, execute any dependencies of this operation
        dependency_results = []
        for arg_name in sorted(dep_node.arguments.keys()):
            arg_dep_id = dep_node.arguments[arg_name]
            
            if self.storage.exists(arg_dep_id):
                dependency_results.append(self.storage.retrieve(arg_dep_id))
            elif arg_dep_id in self.workplan.nodes:
                arg_dep_node = self.workplan.nodes[arg_dep_id]
                if isinstance(arg_dep_node, ConstantValue):
                    dependency_results.append(arg_dep_node.value)
                elif isinstance(arg_dep_node, ClosureValue):
                    dependency_results.append(arg_dep_node)
                elif isinstance(arg_dep_node, Operation):
                    # Recursively execute this dependency
                    self._execute_missing_dependency(arg_dep_id, arg_dep_node)
                    dependency_results.append(self.storage.retrieve(arg_dep_id))
                else:
                    raise Exception(f"Unsupported dependency type for {arg_dep_id[:8]}...")
            else:
                raise Exception(f"Dependency {arg_dep_id[:8]}... not found")
        
        # Execute the operation
        result = self._execute_operation_inner(dep_node, dep_id, dependency_results)
        self.completed.add(dep_id)
        logger.log(VERBOSE_LEVEL, f"Executed missing dependency {dep_id[:8]}... successfully")

def unwrap_node(obj):
    """
    Recursively unwrap dataclass objects to plain dictionaries.
    
    Utility function for converting complex dataclass structures to plain
    Python data structures for serialization or debugging purposes.
    
    Args:
        obj: Object to unwrap (can be dataclass, dict, list, etc.)
        
    Returns:
        Unwrapped object with dataclasses converted to dictionaries
    """
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
"""Global singleton execution engine instance."""

def get_execution_engine() -> ExecutionEngine:
    """
    Get the global execution engine instance.
    
    Creates a new engine with default configuration if none exists.
    Simple singleton pattern for WIP system.
    
    Returns:
        Global ExecutionEngine instance
    """
    global _execution_engine
    
    if _execution_engine is None:
        _execution_engine = ExecutionEngine()
    return _execution_engine

def set_execution_engine(engine: ExecutionEngine):
    """
    Set the global execution engine instance.
    
    Allows replacement of the global engine for testing or configuration.
    Simple assignment for WIP system.
    
    Args:
        engine: ExecutionEngine instance to set as global
    """
    global _execution_engine
    _execution_engine = engine

# Convenience functions
def execute_workplan(workplan: WorkPlan, execution_id: Optional[str] = None, dask_dashboard: bool = False) -> ExecutionResult:
    """
    Execute a workplan using the global execution engine.
    
    Convenience function that uses the global engine instance.
    
    Args:
        workplan: WorkPlan to execute
        execution_id: Optional execution ID (currently unused, defaults to None)
        dask_dashboard: Whether to enable the Dask web dashboard for real-time debugging
        
    Returns:
        ExecutionResult with execution outcome
    """
    return get_execution_engine().execute_workplan(workplan, execution_id, dask_dashboard)
