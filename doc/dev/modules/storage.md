# storage.py - Content-Addressed Storage System

## Purpose

The `storage.py` module implements a persistent, content-addressed storage backend for VoxLogicA-2 using SQLite with Write-Ahead Logging (WAL) mode. It provides thread-safe caching of computation results, enabling deduplication and performance optimization across workplan executions.

## Architecture

### Core Components

#### 1. Content-Addressed Storage
- **SHA256 Keys**: All data is stored using content-derived keys for deduplication
- **Immutable Storage**: Once stored, data never changes (append-only semantics)
- **Cross-Execution Persistence**: Results persist across program runs and workplan executions

#### 2. Thread-Safe Database Operations
- **WAL Mode**: SQLite Write-Ahead Logging for concurrent read/write access
- **Single Connection**: Persistent connection with proper locking for thread safety
- **Transaction Management**: Automatic transaction handling for consistency

#### 3. Multi-Tier Caching
- **Memory Cache**: Non-serializable objects cached in memory
- **Database Cache**: Serializable objects persisted to SQLite
- **Background Writing**: Asynchronous result writing for performance

#### 4. Operation Coordination
- **Active Operation Tracking**: Prevents duplicate execution of identical operations
- **Completion Callbacks**: Notification system for waiting operations
- **Stale Operation Cleanup**: Automatic cleanup of interrupted operations

### Key Classes and Interfaces

#### `StorageBackend`
Main storage interface providing content-addressed caching.

```python
class StorageBackend:
    def __init__(self, db_path: Optional[Union[str, Path]] = None)
    
    # Core storage operations
    def get(self, key: str) -> Optional[Any]
    def put(self, key: str, value: Any) -> None
    def has(self, key: str) -> bool
    def delete(self, key: str) -> bool
    
    # Batch operations
    def get_batch(self, keys: List[str]) -> Dict[str, Any]
    def put_batch(self, items: Dict[str, Any]) -> None
    
    # Operation coordination
    def mark_operation_running(self, operation_uuid: str) -> bool
    def mark_operation_complete(self, operation_uuid: str) -> None
    def is_operation_running(self, operation_uuid: str) -> bool
    def wait_for_operation(self, operation_uuid: str, timeout: float = None) -> bool
```

#### Storage Factory Functions
```python
def get_storage(db_path: Optional[Union[str, Path]] = None) -> StorageBackend:
    """Get shared storage backend instance."""

def create_temp_storage() -> StorageBackend:
    """Create temporary storage for testing."""

def clear_storage(storage: StorageBackend) -> None:
    """Clear all stored data."""
```

## Implementation Details

### Content-Addressed Key Generation

```python
def compute_content_key(data: Any) -> str:
    """Generate SHA256 hash for content-addressed storage."""
    if isinstance(data, (str, int, float, bool)):
        content = json.dumps(data, sort_keys=True)
    elif isinstance(data, dict):
        content = json.dumps(data, sort_keys=True, default=str)
    elif isinstance(data, (list, tuple)):
        content = json.dumps(list(data), sort_keys=True, default=str)
    else:
        # Use pickle for complex objects
        content = pickle.dumps(data)
    
    return hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()
```

### Database Schema

```sql
-- Main results table
CREATE TABLE IF NOT EXISTS results (
    key TEXT PRIMARY KEY,
    value BLOB NOT NULL,
    serialization_method TEXT NOT NULL,
    created_at REAL NOT NULL,
    accessed_at REAL NOT NULL,
    access_count INTEGER DEFAULT 1
);

-- Active operations tracking
CREATE TABLE IF NOT EXISTS active_operations (
    operation_uuid TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    status TEXT DEFAULT 'running'
);

-- Metadata and statistics
CREATE TABLE IF NOT EXISTS storage_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_results_created_at ON results(created_at);
CREATE INDEX IF NOT EXISTS idx_results_accessed_at ON results(accessed_at);
CREATE INDEX IF NOT EXISTS idx_active_operations_started_at ON active_operations(started_at);
```

### Serialization Strategy

The storage system supports multiple serialization methods:

```python
def serialize_value(value: Any) -> Tuple[bytes, str]:
    """Serialize value with appropriate method."""
    
    if isinstance(value, (str, int, float, bool, list, dict, type(None))):
        # Use JSON for simple types
        serialized = json.dumps(value).encode('utf-8')
        return serialized, 'json'
    
    elif hasattr(value, '__dict__') and is_json_serializable(value):
        # Try JSON for custom objects
        try:
            serialized = json.dumps(value.__dict__).encode('utf-8')
            return serialized, 'json_object'
        except (TypeError, ValueError):
            pass
    
    # Fall back to pickle for complex objects
    try:
        serialized = pickle.dumps(value)
        return serialized, 'pickle'
    except Exception as e:
        raise StorageError(f"Cannot serialize value of type {type(value)}: {e}")

def deserialize_value(data: bytes, method: str) -> Any:
    """Deserialize value based on method."""
    
    if method == 'json':
        return json.loads(data.decode('utf-8'))
    elif method == 'json_object':
        return json.loads(data.decode('utf-8'))
    elif method == 'pickle':
        return pickle.loads(data)
    else:
        raise StorageError(f"Unknown serialization method: {method}")
```

### Background Writing

```python
class BackgroundWriter:
    """Asynchronous result writer for improved performance."""
    
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self.write_queue = queue.Queue()
        self.shutdown_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.worker_thread.start()
    
    def _writer_loop(self):
        """Main writer loop processing queued writes."""
        while not self.shutdown_event.is_set():
            try:
                # Get write request with timeout
                write_request = self.write_queue.get(timeout=1.0)
                
                if write_request is None:  # Shutdown signal
                    break
                
                key, value = write_request
                self.storage._write_to_database(key, value)
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Background writer error: {e}")
    
    def queue_write(self, key: str, value: Any):
        """Queue a write operation."""
        self.write_queue.put((key, value))
```

## Dependencies

### Internal Dependencies
- No internal VoxLogicA dependencies (storage is foundational)

### External Dependencies
- `sqlite3` - SQLite database for persistent storage
- `json` - JSON serialization for simple types
- `pickle` - Python object serialization for complex types
- `threading` - Thread synchronization and background operations
- `queue` - Thread-safe communication
- `hashlib` - SHA256 content hashing
- `pathlib` - File system operations

## Usage Examples

### Basic Storage Operations
```python
from voxlogica.storage import get_storage

# Get shared storage instance
storage = get_storage()

# Store and retrieve data
key = "result_12345"
data = {"computation": "complete", "value": 42}

storage.put(key, data)
retrieved = storage.get(key)
print(f"Retrieved: {retrieved}")

# Check existence
if storage.has(key):
    print("Data exists in storage")
```

### Content-Addressed Storage
```python
from voxlogica.storage import compute_content_key

# Generate content-addressed key
data = {"operation": "add", "args": [1, 2]}
content_key = compute_content_key(data)

# Store with content-addressed key
storage.put(content_key, {"result": 3})

# Same data always generates same key
duplicate_key = compute_content_key({"operation": "add", "args": [1, 2]})
assert content_key == duplicate_key

# Retrieve using content key
result = storage.get(content_key)
```

### Batch Operations
```python
# Batch get for efficiency
keys = ["key1", "key2", "key3"]
results = storage.get_batch(keys)
print(f"Retrieved {len(results)} items")

# Batch put for atomic writes
data_batch = {
    "key1": {"value": 100},
    "key2": {"value": 200}, 
    "key3": {"value": 300}
}
storage.put_batch(data_batch)
```

### Operation Coordination
```python
import uuid

# Start operation with unique ID
operation_id = str(uuid.uuid4())

if storage.mark_operation_running(operation_id):
    try:
        # Perform expensive computation
        result = expensive_computation()
        
        # Store result
        storage.put(f"result_{operation_id}", result)
        
    finally:
        # Mark operation complete
        storage.mark_operation_complete(operation_id)
else:
    # Operation already running, wait for completion
    if storage.wait_for_operation(operation_id, timeout=30.0):
        result = storage.get(f"result_{operation_id}")
    else:
        raise TimeoutError("Operation did not complete in time")
```

### Memory vs Database Storage
```python
# Non-serializable objects go to memory cache
class CustomObject:
    def __init__(self, data):
        self.data = data

obj = CustomObject([1, 2, 3])
storage.put("custom_obj", obj)  # Automatically uses memory cache

# Serializable objects go to database
serializable_data = {"numbers": [1, 2, 3], "name": "test"}
storage.put("serializable", serializable_data)  # Uses database storage
```

## Performance Considerations

### Caching Strategy
- **Memory First**: Non-serializable objects cached in memory for fastest access
- **Database Persistent**: Serializable objects persisted to database for durability
- **LRU Eviction**: Memory cache uses LRU eviction when size limits are reached

### Concurrency Optimization
- **WAL Mode**: SQLite WAL mode allows concurrent readers with single writer
- **Background Writing**: Asynchronous writes don't block computation
- **Connection Pooling**: Single persistent connection reduces overhead

### Storage Optimization
- **Content Deduplication**: Identical content stored only once
- **Compression**: Large objects can be compressed before storage
- **Garbage Collection**: Automatic cleanup of unused data

### Scalability Features
- **Batch Operations**: Reduce database round trips for multiple operations
- **Index Optimization**: Database indexes for fast key lookups
- **Memory Management**: Configurable memory cache size limits

## Configuration Options

### Environment Variables
```bash
# Database location
export VOXLOGICA_STORAGE_PATH="/path/to/storage.db"

# Memory cache size (MB)
export VOXLOGICA_MEMORY_CACHE_SIZE="512"

# Background writer queue size
export VOXLOGICA_WRITE_QUEUE_SIZE="1000"

# Database timeout (seconds)
export VOXLOGICA_DB_TIMEOUT="10.0"
```

### Programmatic Configuration
```python
# Custom storage location
storage = StorageBackend(db_path="/custom/path/storage.db")

# Configure cache size
storage.set_memory_cache_limit(1024 * 1024 * 1024)  # 1GB

# Configure write batching
storage.set_write_batch_size(100)
storage.set_write_flush_interval(5.0)  # 5 seconds
```

## Monitoring and Debugging

### Storage Statistics
```python
def get_storage_stats(storage: StorageBackend) -> Dict[str, Any]:
    """Get comprehensive storage statistics."""
    return {
        'database_size': storage.get_database_size(),
        'memory_cache_size': storage.get_memory_cache_size(),
        'total_keys': storage.count_keys(),
        'active_operations': storage.count_active_operations(),
        'cache_hit_rate': storage.get_cache_hit_rate(),
        'write_queue_size': storage.get_write_queue_size()
    }
```

### Performance Monitoring
```python
def monitor_storage_performance(storage: StorageBackend):
    """Monitor storage operation performance."""
    
    # Track operation times
    start_time = time.time()
    result = storage.get("test_key")
    get_time = time.time() - start_time
    
    start_time = time.time()
    storage.put("test_key", {"test": "data"})
    put_time = time.time() - start_time
    
    logger.info(f"Storage GET: {get_time:.3f}s, PUT: {put_time:.3f}s")
```

### Debugging Tools
```python
def debug_storage_state(storage: StorageBackend):
    """Print detailed storage state for debugging."""
    
    print(f"Database path: {storage.db_path}")
    print(f"Database size: {storage.get_database_size()} bytes")
    print(f"Memory cache entries: {len(storage._memory_cache)}")
    print(f"Active operations: {storage._active_operations}")
    print(f"Write queue size: {storage._result_write_queue.qsize()}")
```

## Integration Points

### With Execution Engine
The storage system provides caching for execution results:

```python
# In execution.py
def execute_operation(operation: Operation, storage: StorageBackend) -> Any:
    # Check cache first
    cached_result = storage.get(operation.content_hash)
    if cached_result is not None:
        return cached_result
    
    # Execute and cache result
    result = perform_computation(operation)
    storage.put(operation.content_hash, result)
    return result
```

### With Reducer
The reducer uses storage for workplan caching:

```python
# In reducer.py
def compile_workplan(program: Program, storage: StorageBackend) -> WorkPlan:
    # Check for cached workplan
    program_hash = compute_program_hash(program)
    cached_workplan = storage.get(f"workplan_{program_hash}")
    
    if cached_workplan is not None:
        return cached_workplan
    
    # Compile and cache workplan
    workplan = compile_program_to_workplan(program)
    storage.put(f"workplan_{program_hash}", workplan)
    return workplan
```

### With Features System
Features can register custom serialization handlers:

```python
# Custom serialization for domain-specific types
def register_custom_serializers(storage: StorageBackend):
    storage.register_serializer(ImageType, serialize_image, deserialize_image)
    storage.register_serializer(ModelType, serialize_model, deserialize_model)
```
