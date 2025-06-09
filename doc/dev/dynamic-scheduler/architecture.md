# Dynamic Scheduler Architecture Design

## System Overview

The Dynamic Scheduler is an **alternative execution engine** for VoxLogica-2 that implements actual DAG node execution using database-backed memory allocation. Unlike VoxLogica-2's current sequential execution model that only computes DAG structure, this system executes DAG nodes and stores intermediate results persistently.

## Design Principles

### 1. Alternative Integration
- **Coexistence**: Operates alongside existing sequential execution model
- **Non-Replacement**: Does not replace current buffer allocation system
- **Optional**: Can be enabled/disabled based on execution requirements

### 2. Zero-Copy Architecture
- **Direct Storage**: Nodes write results directly to storage system
- **Minimal Memory**: Reduces memory footprint during execution
- **Streaming I/O**: Supports large object processing without memory buffers

### 3. Peer-to-Peer Readiness
- **Distributed Storage**: Storage format supports network distribution
- **Content Addressing**: Results identified by content hash for P2P scenarios
- **Location Independence**: Storage abstraction enables remote/local transparency

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    VoxLogica-2 Core                        │
├─────────────────────────────────────────────────────────────┤
│  CLI Interface          │        API Interface              │
├─────────────────────────┼───────────────────────────────────┤
│      Sequential         │     Dynamic Scheduler             │
│      Execution          │     (Alternative Engine)         │
│      (Current)          │                                   │
└─────────────────────────┴───────────────────────────────────┘
                                        │
                          ┌─────────────┴─────────────┐
                          │   Execution Coordinator   │
                          └─────────────┬─────────────┘
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
┌───────▼────────┐            ┌────────▼────────┐            ┌────────▼────────┐
│   Scheduler    │            │ Node Execution  │            │ Storage Layer   │
│   Engine       │            │    Engine       │            │   Interface     │
├────────────────┤            ├─────────────────┤            ├─────────────────┤
│• Dependency    │            │• Node Runner    │            │• Get/Set/Delete │
│  Resolution    │◄───────────►│• Result Cache   │◄───────────►│• Serialization  │
│• Task Queue    │            │• Error Handler  │            │• Compression    │
│• Progress      │            │• State Manager  │            │• Streaming I/O  │
│  Tracking      │            └─────────────────┘            └─────────────────┘
└────────────────┘                                                      │
                                                                        │
                                                   ┌────────────────────┼────────────────────┐
                                                   │                    │                    │
                                           ┌───────▼────────┐  ┌────────▼────────┐  ┌───────▼────────┐
                                           │   SQLite/      │  │   Filesystem     │  │    Hybrid      │
                                           │   DuckDB       │  │   Storage        │  │   Approach     │
                                           │   Backend      │  │   Backend        │  │   Backend      │
                                           └────────────────┘  └─────────────────┘  └────────────────┘
```

## Core Components

### 1. Execution Coordinator
**Responsibility**: Main orchestration component that manages the execution flow.

**Key Functions**:
- Receives DAG from VoxLogica-2 core
- Initializes scheduler and storage systems
- Coordinates between scheduler and execution engine
- Reports progress and results back to core system

**Interface**:
```python
class ExecutionCoordinator:
    def execute_dag(self, dag: DAG, config: ExecutionConfig) -> ExecutionResult
    def get_progress(self, execution_id: str) -> ExecutionProgress
    def cancel_execution(self, execution_id: str) -> bool
```

### 2. Scheduler Engine
**Responsibility**: Dynamic scheduling of DAG nodes based on dependencies and resource availability.

**Key Functions**:
- Dependency resolution and topological ordering
- Dynamic task queue management
- Concurrent execution coordination
- Progress tracking and reporting

**Components**:
- **Dependency Resolver**: Analyzes DAG structure and determines execution order
- **Task Queue**: Priority queue for ready-to-execute nodes
- **Progress Tracker**: Monitors execution state and completion status
- **Resource Manager**: Manages execution resources and concurrency

### 3. Node Execution Engine
**Responsibility**: Executes individual DAG nodes and manages their results.

**Key Functions**:
- Node execution with proper isolation
- Result serialization and storage
- Error handling and recovery
- State management

**Components**:
- **Node Runner**: Executes node operations
- **Result Cache**: In-memory cache for frequently accessed results
- **Error Handler**: Manages execution errors and retries
- **State Manager**: Tracks node execution states

### 4. Storage Layer Interface
**Responsibility**: Abstract interface for persistent storage of node results.

**Key Functions**:
- CRUD operations for node results
- Data serialization/deserialization
- Compression and optimization
- Streaming I/O for large objects

**Multiple Backend Support**:
- **Database Backend**: SQLite, DuckDB, or other embedded databases
- **Filesystem Backend**: JSON metadata + binary files
- **Hybrid Backend**: Database metadata + filesystem large objects

## Data Flow

### 1. Execution Initialization
```
DAG Input → Execution Coordinator → Storage Backend Selection → Scheduler Initialization
```

### 2. Node Execution Flow
```
Ready Node → Node Runner → Result Computation → Direct Storage Write → Dependency Update
```

### 3. Result Retrieval
```
Result Request → Storage Interface → Backend Query → Deserialization → Result Return
```

## Storage Architecture

### Data Type Handling

**Primitives** (strings, numbers, booleans):
- Stored as native database types or JSON
- Direct serialization without compression
- Immediate availability for dependent nodes

**Binary Data** (files, images):
- Stored as BLOBs or separate binary files
- Optional compression (gzip, lz4)
- Streaming I/O for large objects

**Large Objects**:
- Memory-mapped files for efficient access
- Metadata stored separately
- Lazy loading for memory efficiency

**Nested Records**:
- JSON or MessagePack serialization
- Queryable metadata extraction
- Structured access patterns

**Datasets**:
- Metadata-driven lazy loading
- Chunked storage for large datasets
- Progressive loading strategies

### Zero-Copy Implementation

**Write Path**:
```python
# Node writes directly to storage without intermediate buffering
node_result = execute_node(node_definition)
storage.write_direct(node_id, node_result)  # Zero-copy write
```

**Read Path**:
```python
# Streaming read for large objects
result_stream = storage.read_stream(node_id)
for chunk in result_stream:
    process_chunk(chunk)  # Process without loading entire result
```

## Concurrency Model

### Thread Safety
- **Storage Layer**: Thread-safe operations with minimal locking
- **Execution Engine**: Concurrent node execution with isolation
- **Result Cache**: Lock-free data structures where possible

### Resource Management
- **Connection Pooling**: Database connection management
- **Memory Bounds**: Configurable memory limits for execution
- **Disk I/O**: Asynchronous I/O for storage operations

## Integration with VoxLogica-2

### CLI Integration
```bash
# Enable dynamic scheduler
voxlogica run program.imgql --execution-engine dynamic

# Configure storage backend
voxlogica run program.imgql --execution-engine dynamic --storage-backend sqlite

# Monitor execution progress
voxlogica run program.imgql --execution-engine dynamic --progress
```

### API Integration
```python
# API endpoint for dynamic execution
POST /api/v1/execute-dynamic
{
    "program": "...",
    "execution_engine": "dynamic",
    "storage_backend": "sqlite",
    "config": {
        "max_concurrent_nodes": 4,
        "cache_size": "1GB"
    }
}
```

### Configuration Options
```python
@dataclass
class DynamicExecutionConfig:
    storage_backend: str = "sqlite"  # sqlite, filesystem, hybrid
    storage_path: str = "./voxlogica_storage"
    max_concurrent_nodes: int = 4
    cache_size: str = "512MB"
    compression: bool = True
    streaming_threshold: str = "100MB"
    enable_p2p: bool = False
```

## Error Handling and Recovery

### Node Execution Errors
- **Retry Logic**: Configurable retry policies for transient failures
- **Error Isolation**: Failed nodes don't affect independent branches
- **Partial Results**: Save intermediate results before failure

### Storage Errors
- **Transaction Support**: ACID compliance where supported
- **Backup Strategies**: Automatic backup of critical metadata
- **Corruption Detection**: Checksums and integrity validation

### System Recovery
- **Graceful Shutdown**: Clean shutdown with state preservation
- **Resume Capability**: Resume execution from last checkpoint
- **Rollback Support**: Rollback to previous consistent state

## Performance Characteristics

### Scalability Targets
- **Node Count**: Efficiently handle 10K+ node DAGs
- **Memory Usage**: Constant memory usage regardless of DAG size
- **Concurrent Execution**: Support 4-16 concurrent node executions
- **Storage Size**: Handle TBs of intermediate results

### Optimization Strategies
- **Lazy Loading**: Load results only when needed
- **Compression**: Configurable compression for space/time tradeoffs
- **Caching**: Intelligent caching of frequently accessed results
- **Parallel I/O**: Concurrent storage operations

## Security Considerations

### Data Protection
- **Encryption**: Optional encryption for sensitive datasets
- **Access Control**: Fine-grained access control for stored results
- **Audit Logging**: Comprehensive logging of all operations

### Network Security (P2P Readiness)
- **Authentication**: Peer authentication for distributed scenarios
- **Transport Security**: Encrypted communication channels
- **Content Verification**: Cryptographic verification of shared results

## Extensibility

### Backend Plugins
- **Storage Backends**: Pluggable storage implementations
- **Execution Engines**: Customizable node execution strategies
- **Serialization**: Custom serializers for specific data types

### Integration Points
- **Monitoring**: Hooks for external monitoring systems
- **Metrics**: Comprehensive metrics collection
- **Callbacks**: Event callbacks for external integration

## Future Enhancements

### Distributed Execution
- **Peer-to-Peer**: Share results across network peers
- **Load Balancing**: Distribute execution across multiple nodes
- **Fault Tolerance**: Redundant execution for critical paths

### Advanced Scheduling
- **Priority Scheduling**: Priority-based node execution
- **Resource Awareness**: Consider resource requirements in scheduling
- **Adaptive Scheduling**: Learn from execution patterns

This architecture provides a solid foundation for implementing the dynamic scheduler while maintaining clear separation from existing VoxLogica-2 components and enabling future distributed execution capabilities.
