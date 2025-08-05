# Semantic Execution Engine Documentation

## Overview

The Semantic Execution Engine is the core component responsible for executing VoxLogicA-2 workplans in a distributed, fault-tolerant manner. It orchestrates task scheduling, manages dependencies, and coordinates execution across multiple workers using Dask.

## Architecture

### Core Components

#### 1. Task Scheduling System
- **Dependency Resolution**: Automatic topological sorting of operation dependencies
- **Work Stealing**: Dynamic load balancing across workers
- **Priority Scheduling**: Critical path optimization for faster completion
- **Resource Management**: Memory and CPU usage monitoring and limits

#### 2. Distributed Execution
- **Dask Integration**: Leverage Dask for distributed computing capabilities
- **Fault Tolerance**: Automatic retry and recovery from worker failures
- **Result Aggregation**: Efficient collection and combination of partial results
- **Progress Monitoring**: Real-time tracking of execution progress

#### 3. Memory Management
- **Lazy Data Loading**: Data is loaded only when needed for computation
- **Out-of-Core Processing**: Handle datasets larger than available memory
- **Garbage Collection**: Automatic cleanup of intermediate results
- **Memory Pressure Handling**: Graceful degradation under memory constraints

### Execution Flow

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   WorkPlan      │    │  Task Queue     │    │  Worker Pool    │
│   Compilation   │───▶│  Management     │───▶│  Execution      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Dependency     │    │  Lazy Task      │    │  Result         │
│  Analysis       │    │  Scheduling     │    │  Aggregation    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Task Queue Management

### Queue Architecture

The execution engine uses a multi-level queue system:

```python
@dataclass
class TaskQueue:
    pending_tasks: Deque[Task]           # Tasks waiting for dependencies
    ready_tasks: PriorityQueue[Task]     # Tasks ready for execution
    running_tasks: Dict[str, Task]       # Currently executing tasks
    completed_tasks: Dict[str, Any]      # Completed task results
    failed_tasks: Dict[str, Exception]   # Failed tasks for retry
    
    def enqueue_task(self, task: Task) -> None:
        """Add task to appropriate queue based on readiness."""
        
    def get_ready_task(self) -> Optional[Task]:
        """Get next ready task for execution."""
        
    def mark_task_complete(self, task_id: str, result: Any) -> None:
        """Mark task as complete and update dependent tasks."""
```

### Task Types

#### Computation Tasks
```python
@dataclass
class ComputationTask(Task):
    operation: Operation                 # Operation to execute
    input_data: Dict[str, Any]          # Input data for operation
    dependencies: Set[str]              # Task dependencies
    priority: int                       # Execution priority
    retry_count: int = 0                # Number of retry attempts
    
    def can_execute(self, completed_tasks: Set[str]) -> bool:
        """Check if all dependencies are satisfied."""
        return self.dependencies.issubset(completed_tasks)
```

#### IO Tasks
```python
@dataclass
class IOTask(Task):
    operation_type: str                 # "load" or "save"
    file_path: Path                     # File to read/write
    data_format: str                    # Data format (json, pickle, etc.)
    compression: Optional[str] = None   # Optional compression
    
    def estimate_duration(self) -> float:
        """Estimate task execution time for scheduling."""
        file_size = self.file_path.stat().st_size if self.file_path.exists() else 0
        return estimate_io_time(file_size, self.operation_type)
```

#### Aggregation Tasks
```python
@dataclass
class AggregationTask(Task):
    aggregation_type: str               # "sum", "concat", "merge", etc.
    input_tasks: List[str]              # Tasks to aggregate
    output_schema: Optional[Dict] = None # Expected output structure
    
    def execute(self, input_results: List[Any]) -> Any:
        """Execute aggregation operation."""
        if self.aggregation_type == "sum":
            return sum(input_results)
        elif self.aggregation_type == "concat":
            return list(itertools.chain(*input_results))
        # ... other aggregation types
```

## Lazy Data Structures

### Lazy Tensor Implementation

```python
class LazyTensor:
    """Lazy-evaluated tensor for spatial data operations."""
    
    def __init__(self, shape: Tuple[int, ...], dtype: np.dtype, loader: Callable):
        self.shape = shape
        self.dtype = dtype
        self._loader = loader
        self._data = None
        self._loaded = False
    
    def __getitem__(self, key) -> 'LazyTensor':
        """Create lazy slice without loading data."""
        return LazyTensor(
            shape=self._compute_slice_shape(key),
            dtype=self.dtype,
            loader=lambda: self._get_data()[key]
        )
    
    def _get_data(self) -> np.ndarray:
        """Load data if not already loaded."""
        if not self._loaded:
            self._data = self._loader()
            self._loaded = True
        return self._data
    
    def compute(self) -> np.ndarray:
        """Force computation and return actual data."""
        return self._get_data()
```

### Lazy Sequence for Temporal Data

```python
class LazySequence:
    """Lazy-evaluated sequence for temporal operations."""
    
    def __init__(self, length: int, generator: Callable[[int], Any]):
        self.length = length
        self._generator = generator
        self._cache = {}
    
    def __getitem__(self, index: int) -> Any:
        """Get item at index, generating if needed."""
        if index not in self._cache:
            self._cache[index] = self._generator(index)
        return self._cache[index]
    
    def __iter__(self):
        """Iterate over sequence, generating items lazily."""
        for i in range(self.length):
            yield self[i]
    
    def batch_compute(self, indices: List[int]) -> List[Any]:
        """Compute multiple items efficiently."""
        missing_indices = [i for i in indices if i not in self._cache]
        
        if missing_indices:
            # Batch generation for efficiency
            batch_results = self._generator(missing_indices)
            for i, result in zip(missing_indices, batch_results):
                self._cache[i] = result
        
        return [self._cache[i] for i in indices]
```

### Lazy Expression Evaluation

```python
class LazyExpression:
    """Lazy-evaluated expression with dependency tracking."""
    
    def __init__(self, expression: Expression, environment: Environment):
        self.expression = expression
        self.environment = environment
        self._result = None
        self._computed = False
        self._dependencies = None
    
    def get_dependencies(self) -> Set[str]:
        """Get expression dependencies without evaluation."""
        if self._dependencies is None:
            self._dependencies = analyze_expression_dependencies(
                self.expression, self.environment
            )
        return self._dependencies
    
    def can_compute(self, available_results: Dict[str, Any]) -> bool:
        """Check if expression can be computed with available results."""
        deps = self.get_dependencies()
        return all(dep in available_results for dep in deps)
    
    def compute(self, available_results: Dict[str, Any]) -> Any:
        """Compute expression result."""
        if not self._computed:
            if not self.can_compute(available_results):
                missing = self.get_dependencies() - available_results.keys()
                raise DependencyError(f"Missing dependencies: {missing}")
            
            self._result = evaluate_expression(
                self.expression, self.environment, available_results
            )
            self._computed = True
        
        return self._result
```

## Dask Integration

### Dask Configuration

```python
def configure_dask_for_voxlogica():
    """Configure Dask for optimal VoxLogicA performance."""
    
    dask.config.set({
        # Memory management
        'array.chunk-size': '128MB',
        'array.slicing.split_large_chunks': True,
        
        # Serialization
        'serialization.compression': 'lz4',
        'serialization.pickle-protocol': 5,
        
        # Scheduling
        'optimization.fuse': {},
        'optimization.cull': True,
        
        # Distributed
        'distributed.worker.memory.target': 0.6,
        'distributed.worker.memory.spill': 0.7,
        'distributed.worker.memory.pause': 0.8,
        'distributed.worker.memory.terminate': 0.95,
        
        # Diagnostics
        'distributed.diagnostics.bokeh': True,
        'distributed.admin.bokeh': True
    })
```

### Custom Dask Scheduler

```python
class VoxLogicAScheduler:
    """Custom Dask scheduler optimized for VoxLogicA workloads."""
    
    def __init__(self, client: Client):
        self.client = client
        self.task_priorities = {}
        self.resource_usage = {}
    
    def schedule_workplan(self, workplan: WorkPlan) -> Dict[str, Future]:
        """Schedule workplan execution with optimization."""
        
        # Analyze workplan for optimization opportunities
        critical_path = find_critical_path(workplan)
        parallelizable_groups = find_parallelizable_operations(workplan)
        
        # Assign priorities based on critical path
        for i, op_id in enumerate(critical_path):
            self.task_priorities[op_id] = 1000 - i  # Higher priority for critical path
        
        # Submit tasks with resource requirements
        futures = {}
        for op_id, operation in workplan.operations.items():
            requirements = estimate_resource_requirements(operation)
            
            future = self.client.submit(
                execute_operation,
                operation,
                priority=self.task_priorities.get(op_id, 0),
                resources=requirements
            )
            
            futures[op_id] = future
        
        return futures
    
    def optimize_task_placement(self, tasks: List[Task]) -> Dict[str, str]:
        """Optimize task placement across workers."""
        
        worker_loads = self.client.scheduler_info()['workers']
        placement = {}
        
        for task in tasks:
            # Choose worker with lowest load and sufficient resources
            best_worker = min(
                worker_loads.keys(),
                key=lambda w: self._compute_worker_score(w, task, worker_loads)
            )
            placement[task.id] = best_worker
        
        return placement
```

### Workplan to Dask Graph Conversion

```python
def workplan_to_dask_graph(workplan: WorkPlan) -> Dict[str, Any]:
    """Convert VoxLogicA workplan to Dask task graph."""
    
    dask_graph = {}
    
    for op_id, operation in workplan.operations.items():
        # Convert operation to Dask delayed computation
        dependencies = workplan.dependencies.get(op_id, set())
        
        if dependencies:
            # Operation with dependencies
            dep_keys = list(dependencies)
            dask_graph[op_id] = (
                execute_operation_with_deps,
                operation,
                *dep_keys
            )
        else:
            # Independent operation
            dask_graph[op_id] = (execute_operation, operation)
    
    return dask_graph

def execute_operation_with_deps(operation: Operation, *dep_results) -> Any:
    """Execute operation with dependency results."""
    
    # Map dependency results to operation arguments
    resolved_args = resolve_operation_arguments(operation, dep_results)
    
    # Execute operation
    return execute_operation(operation, **resolved_args)
```

## Performance Optimization

### Adaptive Chunking

```python
class AdaptiveChunker:
    """Adaptive chunking for large datasets."""
    
    def __init__(self, target_chunk_size: int = 128 * 1024 * 1024):  # 128MB
        self.target_chunk_size = target_chunk_size
        self.chunk_stats = {}
    
    def compute_optimal_chunks(self, data_shape: Tuple[int, ...], dtype: np.dtype) -> Tuple[int, ...]:
        """Compute optimal chunk size for given data."""
        
        element_size = np.dtype(dtype).itemsize
        total_elements = np.prod(data_shape)
        total_size = total_elements * element_size
        
        if total_size <= self.target_chunk_size:
            return data_shape  # Single chunk
        
        # Compute chunks along each dimension
        chunk_shape = list(data_shape)
        current_size = total_size
        
        for i in range(len(chunk_shape)):
            if current_size <= self.target_chunk_size:
                break
            
            reduction_factor = min(
                chunk_shape[i] // 2,
                int(np.ceil(current_size / self.target_chunk_size))
            )
            
            chunk_shape[i] = max(1, chunk_shape[i] // reduction_factor)
            current_size = np.prod(chunk_shape) * element_size
        
        return tuple(chunk_shape)
```

### Memory Pressure Management

```python
class MemoryManager:
    """Manage memory pressure during execution."""
    
    def __init__(self, memory_limit: int = 4 * 1024**3):  # 4GB default
        self.memory_limit = memory_limit
        self.current_usage = 0
        self.cached_results = {}
        self.lru_order = []
    
    def check_memory_pressure(self) -> float:
        """Check current memory pressure (0.0 to 1.0)."""
        return self.current_usage / self.memory_limit
    
    def ensure_memory_available(self, required_memory: int) -> None:
        """Ensure sufficient memory is available."""
        
        if self.current_usage + required_memory > self.memory_limit:
            # Need to free memory
            memory_to_free = (self.current_usage + required_memory) - self.memory_limit
            self._free_memory(memory_to_free)
    
    def _free_memory(self, amount: int) -> None:
        """Free specified amount of memory by evicting cached results."""
        
        freed = 0
        while freed < amount and self.lru_order:
            # Evict least recently used result
            key = self.lru_order.pop(0)
            if key in self.cached_results:
                result_size = self._estimate_size(self.cached_results[key])
                del self.cached_results[key]
                self.current_usage -= result_size
                freed += result_size
    
    def cache_result(self, key: str, result: Any) -> None:
        """Cache result with memory management."""
        
        result_size = self._estimate_size(result)
        self.ensure_memory_available(result_size)
        
        self.cached_results[key] = result
        self.current_usage += result_size
        
        # Update LRU order
        if key in self.lru_order:
            self.lru_order.remove(key)
        self.lru_order.append(key)
```

## Error Handling and Recovery

### Fault Tolerance

```python
class FaultTolerantExecutor:
    """Executor with comprehensive fault tolerance."""
    
    def __init__(self, max_retries: int = 3, backoff_factor: float = 2.0):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.failed_tasks = {}
        self.worker_health = {}
    
    def execute_with_retry(self, task: Task) -> Any:
        """Execute task with automatic retry on failure."""
        
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:
                    # Exponential backoff
                    delay = self.backoff_factor ** (attempt - 1)
                    time.sleep(delay)
                
                result = self._execute_task(task)
                
                # Clear failure record on success
                if task.id in self.failed_tasks:
                    del self.failed_tasks[task.id]
                
                return result
                
            except Exception as e:
                last_exception = e
                
                # Record failure
                if task.id not in self.failed_tasks:
                    self.failed_tasks[task.id] = []
                self.failed_tasks[task.id].append({
                    'attempt': attempt,
                    'exception': str(e),
                    'timestamp': time.time()
                })
                
                # Check if error is retryable
                if not self._is_retryable_error(e):
                    break
        
        # All retries exhausted
        raise TaskExecutionError(f"Task {task.id} failed after {self.max_retries + 1} attempts") from last_exception
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if error is worth retrying."""
        
        retryable_types = (
            ConnectionError,
            TimeoutError,
            MemoryError,
            OSError
        )
        
        non_retryable_types = (
            SyntaxError,
            TypeError,
            ValueError,
            AttributeError
        )
        
        if isinstance(error, non_retryable_types):
            return False
        elif isinstance(error, retryable_types):
            return True
        else:
            # Unknown error type, be conservative
            return True
```

### Progress Monitoring

```python
class ExecutionMonitor:
    """Monitor execution progress and performance."""
    
    def __init__(self):
        self.start_time = None
        self.task_times = {}
        self.progress_callbacks = []
        self.metrics = {
            'tasks_completed': 0,
            'tasks_failed': 0,
            'total_tasks': 0,
            'memory_usage': 0,
            'cpu_usage': 0
        }
    
    def start_monitoring(self, total_tasks: int) -> None:
        """Start monitoring execution."""
        self.start_time = time.time()
        self.metrics['total_tasks'] = total_tasks
    
    def update_progress(self, completed_tasks: int, failed_tasks: int = 0) -> None:
        """Update execution progress."""
        
        self.metrics['tasks_completed'] = completed_tasks
        self.metrics['tasks_failed'] = failed_tasks
        
        progress = completed_tasks / self.metrics['total_tasks'] if self.metrics['total_tasks'] > 0 else 0
        
        # Notify progress callbacks
        for callback in self.progress_callbacks:
            callback(progress, self.metrics)
    
    def estimate_completion_time(self) -> Optional[float]:
        """Estimate remaining execution time."""
        
        if not self.start_time or self.metrics['tasks_completed'] == 0:
            return None
        
        elapsed = time.time() - self.start_time
        completed_ratio = self.metrics['tasks_completed'] / self.metrics['total_tasks']
        
        if completed_ratio > 0:
            estimated_total = elapsed / completed_ratio
            return estimated_total - elapsed
        
        return None
```

## Integration Points

### With Storage System

```python
def execute_with_caching(operation: Operation, storage: StorageBackend) -> Any:
    """Execute operation with result caching."""
    
    # Check cache first
    cache_key = operation.content_hash
    cached_result = storage.get(cache_key)
    
    if cached_result is not None:
        logger.debug(f"Cache hit for operation {operation.node_id}")
        return cached_result
    
    # Execute operation
    logger.debug(f"Executing operation {operation.node_id}")
    result = execute_operation(operation)
    
    # Cache result
    storage.put(cache_key, result)
    
    return result
```

### With Feature System

```python
def register_execution_features():
    """Register execution engine features."""
    
    features = [
        Feature(
            name="execute_workplan",
            description="Execute VoxLogicA workplan",
            handler=handle_workplan_execution,
            cli_options={
                "arguments": [
                    {"name": "workplan_file", "help": "Workplan JSON file"},
                    {"name": "--parallel", "action": "store_true", "help": "Enable parallel execution"},
                    {"name": "--workers", "type": int, "default": 4, "help": "Number of workers"}
                ]
            }
        ),
        Feature(
            name="monitor_execution",
            description="Monitor workplan execution progress",
            handler=handle_execution_monitoring,
            api_endpoint={"method": "GET", "path": "/execution/status"}
        )
    ]
    
    for feature in features:
        FeatureRegistry.register(feature)
```
