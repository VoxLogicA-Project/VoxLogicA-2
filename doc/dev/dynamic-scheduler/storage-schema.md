# Storage Schema Design

## Overview

This document defines the storage schema for the Dynamic Scheduler's database-backed memory allocation system. The design supports multiple storage backends while maintaining a consistent interface and zero-copy execution principles.

## Storage Backend Options

### Option 1: SQLite Database Backend

#### Schema Design

**Core Tables**:

```sql
-- Node execution results and metadata
CREATE TABLE node_results (
    node_id TEXT PRIMARY KEY,           -- Content-addressed ID (SHA256)
    dag_id TEXT NOT NULL,               -- DAG identifier
    node_type TEXT NOT NULL,            -- Node operation type
    status TEXT NOT NULL,               -- pending, running, completed, failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    execution_time_ms INTEGER,
    data_type TEXT NOT NULL,            -- primitive, binary, nested, dataset
    data_size INTEGER,                  -- Size in bytes
    content_hash TEXT,                  -- Hash of actual content
    compression TEXT,                   -- none, gzip, lz4
    metadata TEXT                       -- JSON metadata
);

-- Binary data storage (for smaller objects)
CREATE TABLE node_data (
    node_id TEXT PRIMARY KEY,
    data BLOB,
    FOREIGN KEY (node_id) REFERENCES node_results(node_id)
);

-- Large object references (stored in filesystem)
CREATE TABLE large_objects (
    node_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    chunk_size INTEGER,
    chunk_count INTEGER,
    FOREIGN KEY (node_id) REFERENCES node_results(node_id)
);

-- DAG execution tracking
CREATE TABLE dag_executions (
    dag_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,               -- pending, running, completed, failed
    total_nodes INTEGER,
    completed_nodes INTEGER,
    failed_nodes INTEGER,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    config TEXT                         -- JSON execution configuration
);

-- Node dependencies for scheduling
CREATE TABLE node_dependencies (
    node_id TEXT,
    depends_on TEXT,
    PRIMARY KEY (node_id, depends_on),
    FOREIGN KEY (node_id) REFERENCES node_results(node_id)
);

-- Execution errors and retry history
CREATE TABLE execution_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT,
    stack_trace TEXT,
    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    FOREIGN KEY (node_id) REFERENCES node_results(node_id)
);
```

**Indexes for Performance**:

```sql
CREATE INDEX idx_node_results_dag_id ON node_results(dag_id);
CREATE INDEX idx_node_results_status ON node_results(status);
CREATE INDEX idx_node_results_content_hash ON node_results(content_hash);
CREATE INDEX idx_dag_executions_status ON dag_executions(status);
CREATE INDEX idx_node_dependencies_depends_on ON node_dependencies(depends_on);
CREATE INDEX idx_execution_errors_node_id ON execution_errors(node_id);
```

**Triggers for Consistency**:

```sql
-- Update DAG execution progress
CREATE TRIGGER update_dag_progress 
AFTER UPDATE OF status ON node_results
WHEN NEW.status IN ('completed', 'failed')
BEGIN
    UPDATE dag_executions 
    SET completed_nodes = (
        SELECT COUNT(*) FROM node_results 
        WHERE dag_id = NEW.dag_id AND status = 'completed'
    ),
    failed_nodes = (
        SELECT COUNT(*) FROM node_results 
        WHERE dag_id = NEW.dag_id AND status = 'failed'
    )
    WHERE dag_id = NEW.dag_id;
END;
```

#### Data Type Storage Strategy

**Primitives** (strings, numbers, booleans):
```sql
-- Stored directly in metadata field as JSON
INSERT INTO node_results (node_id, data_type, metadata) 
VALUES ('node_123', 'primitive', '{"value": 42, "type": "int"}');
```

**Small Binary Data** (< 1MB):
```sql
-- Stored in node_data table as BLOB
INSERT INTO node_data (node_id, data) VALUES ('node_456', ?);
```

**Large Objects** (> 1MB):
```sql
-- Reference to filesystem storage
INSERT INTO large_objects (node_id, file_path, chunk_size, chunk_count)
VALUES ('node_789', '/storage/node_789.bin', 1048576, 15);
```

**Nested Records**:
```sql
-- JSON serialization in metadata
INSERT INTO node_results (node_id, data_type, metadata)
VALUES ('node_abc', 'nested', '{"schema": "record", "fields": {...}}');
```

### Option 2: Filesystem Backend

#### Directory Structure

```
voxlogica_storage/
├── metadata/
│   ├── dags/
│   │   ├── dag_12345.json          # DAG execution metadata
│   │   └── dag_67890.json
│   ├── nodes/
│   │   ├── node_abc123.json        # Node result metadata
│   │   └── node_def456.json
│   └── indexes/
│       ├── by_dag.json             # DAG -> nodes mapping
│       ├── by_status.json          # Status indexes
│       └── by_content_hash.json    # Content-addressed lookup
├── data/
│   ├── primitives/
│   │   ├── node_abc123.json        # Small data as JSON
│   │   └── node_def456.json
│   ├── binary/
│   │   ├── node_ghi789.bin         # Binary data files
│   │   └── node_jkl012.bin.gz      # Compressed binary
│   └── large/
│       ├── node_mno345/            # Chunked large objects
│       │   ├── chunk_000.bin
│       │   ├── chunk_001.bin
│       │   └── chunk_002.bin
│       └── node_pqr678/
└── temp/                           # Temporary files during execution
    └── uploads/
```

#### Metadata File Formats

**DAG Execution Metadata** (`dag_12345.json`):
```json
{
    "dag_id": "dag_12345",
    "status": "running",
    "total_nodes": 150,
    "completed_nodes": 45,
    "failed_nodes": 2,
    "started_at": "2025-06-09T10:30:00Z",
    "config": {
        "max_concurrent_nodes": 4,
        "storage_backend": "filesystem"
    },
    "nodes": [
        "node_abc123",
        "node_def456"
    ]
}
```

**Node Result Metadata** (`node_abc123.json`):
```json
{
    "node_id": "node_abc123",
    "dag_id": "dag_12345",
    "node_type": "arithmetic_operation",
    "status": "completed",
    "created_at": "2025-06-09T10:30:15Z",
    "completed_at": "2025-06-09T10:30:18Z",
    "execution_time_ms": 3420,
    "data_type": "binary",
    "data_size": 2048576,
    "content_hash": "sha256:abc123...",
    "compression": "gzip",
    "storage": {
        "type": "file",
        "path": "data/binary/node_abc123.bin.gz"
    },
    "dependencies": ["node_xyz789", "node_uvw456"],
    "metadata": {
        "operation": "+",
        "operands": ["node_xyz789", "node_uvw456"]
    }
}
```

#### Index Files

**By DAG Index** (`by_dag.json`):
```json
{
    "dag_12345": {
        "nodes": ["node_abc123", "node_def456"],
        "status": "running",
        "last_updated": "2025-06-09T10:30:18Z"
    }
}
```

**By Status Index** (`by_status.json`):
```json
{
    "completed": ["node_abc123", "node_xyz789"],
    "running": ["node_def456"],
    "pending": ["node_ghi789", "node_jkl012"],
    "failed": ["node_mno345"]
}
```

### Option 3: Hybrid Approach

#### Architecture
- **Metadata**: SQLite database for fast queries and transactions
- **Small Data**: Database BLOBs for objects < 1MB
- **Large Data**: Filesystem storage for objects > 1MB

#### Implementation Strategy

```python
class HybridStorage:
    def __init__(self, db_path: str, data_path: str):
        self.db = SQLiteStorage(db_path)
        self.fs = FilesystemStorage(data_path)
        self.size_threshold = 1024 * 1024  # 1MB
    
    def store_result(self, node_id: str, data: bytes) -> None:
        if len(data) < self.size_threshold:
            self.db.store_result(node_id, data)
        else:
            file_path = self.fs.store_result(node_id, data)
            self.db.store_metadata(node_id, {"file_path": file_path})
```

## Zero-Copy Implementation

### Write Operations

**Direct Database Write**:
```python
def execute_node_sqlite(node_def: NodeDefinition) -> None:
    # Node executes and streams result directly to database
    with sqlite_connection.cursor() as cursor:
        result_stream = node_def.execute()
        
        # Stream large objects in chunks
        if result_stream.size > CHUNK_THRESHOLD:
            for chunk in result_stream:
                cursor.execute(
                    "INSERT INTO node_data_chunks (node_id, chunk_id, data) VALUES (?, ?, ?)",
                    (node_def.id, chunk.id, chunk.data)
                )
        else:
            cursor.execute(
                "INSERT INTO node_data (node_id, data) VALUES (?, ?)",
                (node_def.id, result_stream.read_all())
            )
```

**Direct Filesystem Write**:
```python
def execute_node_filesystem(node_def: NodeDefinition) -> None:
    # Node writes directly to filesystem
    output_path = f"data/binary/{node_def.id}.bin"
    
    with open(output_path, 'wb') as f:
        result_stream = node_def.execute()
        for chunk in result_stream:
            f.write(chunk.data)  # Zero-copy write
    
    # Update metadata atomically
    metadata = create_node_metadata(node_def, output_path)
    write_metadata_atomic(f"metadata/nodes/{node_def.id}.json", metadata)
```

### Read Operations

**Streaming Read**:
```python
def read_result_stream(node_id: str) -> Iterator[bytes]:
    if storage_backend == "sqlite":
        cursor = connection.execute(
            "SELECT data FROM node_data_chunks WHERE node_id = ? ORDER BY chunk_id",
            (node_id,)
        )
        for row in cursor:
            yield row[0]
    
    elif storage_backend == "filesystem":
        metadata = load_node_metadata(node_id)
        with open(metadata["storage"]["path"], 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
```

## Performance Optimizations

### Database Optimizations

**Connection Pooling**:
```python
class SQLiteConnectionPool:
    def __init__(self, db_path: str, pool_size: int = 10):
        self.pool = queue.Queue(maxsize=pool_size)
        for _ in range(pool_size):
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")  # Enable WAL mode
            conn.execute("PRAGMA synchronous=NORMAL")  # Balanced durability
            self.pool.put(conn)
```

**Batch Operations**:
```python
def batch_insert_results(results: List[NodeResult]) -> None:
    with connection_pool.get() as conn:
        conn.executemany(
            "INSERT INTO node_results (...) VALUES (...)",
            [(r.node_id, r.data_type, ...) for r in results]
        )
        conn.commit()
```

### Filesystem Optimizations

**Atomic Writes**:
```python
def write_metadata_atomic(path: str, data: dict) -> None:
    temp_path = f"{path}.tmp"
    with open(temp_path, 'w') as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())  # Force write to disk
    
    os.rename(temp_path, path)  # Atomic on POSIX systems
```

**Memory-Mapped Files**:
```python
def read_large_object_mmap(file_path: str) -> memoryview:
    with open(file_path, 'rb') as f:
        mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        return memoryview(mmap_obj)
```

## Compression Strategy

### Compression Selection
- **Text Data**: gzip compression for good ratio
- **Binary Data**: lz4 compression for speed
- **Images**: Native format compression (JPEG, PNG)
- **Large Objects**: Chunked compression for streaming

### Implementation
```python
def compress_data(data: bytes, compression: str) -> bytes:
    if compression == "gzip":
        return gzip.compress(data, compresslevel=6)
    elif compression == "lz4":
        return lz4.compress(data)
    else:
        return data

def decompress_stream(stream: Iterator[bytes], compression: str) -> Iterator[bytes]:
    if compression == "gzip":
        decompressor = gzip.GzipFile(mode='rb')
        for chunk in stream:
            yield decompressor.decompress(chunk)
    elif compression == "lz4":
        for chunk in stream:
            yield lz4.decompress(chunk)
    else:
        for chunk in stream:
            yield chunk
```

## Cross-Platform Considerations

### Path Handling
```python
import os
from pathlib import Path

def normalize_storage_path(base_path: str, relative_path: str) -> Path:
    """Ensure cross-platform path compatibility"""
    return Path(base_path) / Path(relative_path).as_posix()
```

### File Locking
```python
import fcntl  # Unix
import msvcrt  # Windows

def lock_file_cross_platform(file_handle):
    if os.name == 'nt':  # Windows
        msvcrt.locking(file_handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # Unix/Linux/macOS
        fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
```

## Backup and Recovery

### Database Backup
```sql
-- SQLite backup using backup API
PRAGMA backup_db = 'backup.db';

-- Export to SQL dump
.backup backup.sql
```

### Filesystem Backup
```python
def create_storage_backup(storage_path: str, backup_path: str) -> None:
    """Create incremental backup of storage directory"""
    import shutil
    import time
    
    timestamp = int(time.time())
    backup_dir = f"{backup_path}/backup_{timestamp}"
    
    # Copy metadata first (smaller, critical)
    shutil.copytree(f"{storage_path}/metadata", f"{backup_dir}/metadata")
    
    # Copy data incrementally
    shutil.copytree(f"{storage_path}/data", f"{backup_dir}/data")
```

## Security Features

### Encryption at Rest
```python
from cryptography.fernet import Fernet

class EncryptedStorage:
    def __init__(self, key: bytes):
        self.fernet = Fernet(key)
    
    def encrypt_data(self, data: bytes) -> bytes:
        return self.fernet.encrypt(data)
    
    def decrypt_data(self, encrypted_data: bytes) -> bytes:
        return self.fernet.decrypt(encrypted_data)
```

### Access Control
```python
class AccessControlledStorage:
    def __init__(self, storage: Storage, permissions: dict):
        self.storage = storage
        self.permissions = permissions
    
    def can_access(self, user_id: str, node_id: str, operation: str) -> bool:
        user_perms = self.permissions.get(user_id, set())
        return f"{operation}:{node_id}" in user_perms or f"{operation}:*" in user_perms
```

This storage schema design provides flexible, efficient, and scalable storage solutions while maintaining the zero-copy principles essential for the dynamic scheduler's performance goals.
