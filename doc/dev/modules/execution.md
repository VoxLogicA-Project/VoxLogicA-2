# execution.py - Distributed Execution Engine

## Purpose

The `execution.py` module is the core distributed execution engine for VoxLogicA-2. It provides scalable, fault-tolerant execution of workplans using Dask for distributed computing, with content-addressed deduplication and persistent storage.

## Architecture

### Core Components

#### 1. Dask Integration
- **Shared Client Management**: Single threaded Dask client shared across all workplan executions
- **Resource Control**: Configurable memory limits and thread pools
- **Dashboard Support**: Optional web dashboard for debugging and monitoring

#### 2. Operation Coordination
- **Global Futures Table**: Thread-safe coordination of operation execution
- **Lock-Free Design**: Atomic operations for setting and retrieving futures
- **Deduplication**: Content-addressed execution prevents redundant computations

#### 3. Execution Pipeline
- **Workplan Compilation**: Converts workplans to Dask delayed graphs
- **Dependency Resolution**: Topological sorting of operation dependencies
- **Result Aggregation**: Combines partial results into final outputs

### Key Classes and Functions

#### `ExecutionEngine`
Main execution engine class that orchestrates workplan execution.

```python
class ExecutionEngine:
    def __init__(self, storage_backend: StorageBackend)
    def execute_workplan(self, workplan: WorkPlan) -> Dict[str, Any]
    def compile_to_dask(self, workplan: WorkPlan) -> Dict[str, Any]
```

#### Shared Client Management
```python
def get_shared_dask_client(enable_dashboard: bool = False) -> Optional[Client]
def get_operation_future(operation_id: str) -> Optional[Any]
def set_operation_future(operation_id: str, future: Any) -> bool
```

## Implementation Details

### Task Scheduling Strategy

1. **Content-Addressed Deduplication**: Operations with identical content hashes are executed only once
2. **Lazy Evaluation**: Computations are deferred until results are actually needed
3. **Dependency-Driven**: Tasks are scheduled based on data dependencies
4. **Resource Aware**: Memory and CPU limits prevent resource exhaustion

### Dask Configuration

```python
# Default configuration for optimal performance
Client(
    processes=False,        # Use threads for shared memory
    threads_per_worker=4,   # Controlled parallelism
    n_workers=1,           # Single worker for simplicity
    memory_limit='2GB',    # Memory limit per worker
    silence_logs=True      # Reduce log noise
)
```

### Error Handling

- **Automatic Retry**: Failed operations are retried with exponential backoff
- **Graceful Degradation**: System continues operation when non-critical tasks fail
- **Comprehensive Logging**: Detailed error reporting and debugging information

## Dependencies

### Internal Dependencies
- `voxlogica.reducer` - WorkPlan and Operation definitions
- `voxlogica.storage` - StorageBackend for persistent caching
- `voxlogica.converters.json_converter` - JSON serialization
- `voxlogica.main` - Global configuration and logging

### External Dependencies
- `dask.delayed` - Lazy evaluation and task graphs
- `dask.distributed` - Distributed computing client
- `concurrent.futures` - Thread pool execution
- `threading` - Thread-safe operations

## Usage Examples

### Basic Execution
```python
from voxlogica.execution import ExecutionEngine
from voxlogica.storage import get_storage

# Initialize engine with storage backend
storage = get_storage()
engine = ExecutionEngine(storage)

# Execute a workplan
results = engine.execute_workplan(workplan)
print(f"Execution completed with {len(results)} results")
```

### With Dashboard Monitoring
```python
from voxlogica.execution import get_shared_dask_client

# Enable Dask dashboard for debugging
client = get_shared_dask_client(enable_dashboard=True)
print(f"Dashboard available at: {client.dashboard_link}")
```

### Manual Future Coordination
```python
from voxlogica.execution import get_operation_future, set_operation_future

# Check if operation is already running
operation_id = "compute_statistics_xyz123"
existing_future = get_operation_future(operation_id)

if existing_future is None:
    # Start new computation
    future = delayed(expensive_computation)(data)
    if set_operation_future(operation_id, future):
        result = future.compute()
```

## Performance Considerations

### Memory Management
- **Streaming Processing**: Large datasets are processed in chunks
- **Memory Limits**: Configurable limits prevent out-of-memory errors
- **Garbage Collection**: Automatic cleanup of completed operations

### Scalability Features
- **Horizontal Scaling**: Can be extended to multi-machine deployments
- **Load Balancing**: Even distribution of tasks across workers
- **Fault Tolerance**: Automatic recovery from worker failures

### Optimization Strategies
- **Result Caching**: Content-addressed storage prevents redundant computation
- **Lazy Loading**: Data and computations are loaded only when needed
- **Batch Processing**: Multiple small operations are batched for efficiency

## Configuration Options

### Environment Variables
- `VOXLOGICA_DASK_THREADS`: Number of threads per worker (default: 4)
- `VOXLOGICA_DASK_MEMORY`: Memory limit per worker (default: 2GB)
- `VOXLOGICA_ENABLE_DASHBOARD`: Enable Dask dashboard (default: False)

### Runtime Configuration
```python
# Custom Dask client configuration
config = {
    'threads_per_worker': 8,
    'memory_limit': '4GB',
    'enable_dashboard': True
}
```

## Debugging and Monitoring

### Logging
The module provides comprehensive logging at multiple levels:
- `DEBUG`: Detailed operation tracking
- `INFO`: Execution progress and milestones
- `WARNING`: Performance issues and retries
- `ERROR`: Execution failures and exceptions

### Dask Dashboard
When enabled, provides real-time monitoring of:
- Task execution status
- Memory usage and resource utilization
- Worker performance and load distribution
- Task dependencies and execution graph

### Performance Metrics
- Execution time per operation
- Memory usage patterns
- Cache hit rates
- Task completion statistics

## Future Enhancements

### Planned Features
- **Multi-Machine Support**: Full distributed execution across multiple machines
- **Dynamic Scaling**: Automatic worker scaling based on workload
- **Advanced Optimization**: Machine learning-based task scheduling
- **GPU Support**: Integration with GPU-accelerated operations

### Extension Points
- **Custom Schedulers**: Pluggable scheduling algorithms
- **Storage Backends**: Alternative storage systems (Redis, S3, etc.)
- **Monitoring Integrations**: Custom metrics and alerting systems
