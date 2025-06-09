# API Specification

## Overview

This document defines the complete API interface for the Dynamic Scheduler system. The API is designed to integrate seamlessly with VoxLogica-2's existing CLI and API infrastructure while providing zero-copy, high-performance execution capabilities.

## Storage Layer API

### Interface Definition

```python
from abc import ABC, abstractmethod
from typing import Optional, Iterator, Dict, Any
from dataclasses import dataclass
from enum import Enum

class DataType(Enum):
    PRIMITIVE = "primitive"
    BINARY = "binary" 
    NESTED = "nested"
    DATASET = "dataset"
    LARGE_OBJECT = "large_object"

@dataclass
class StorageMetadata:
    node_id: str
    data_type: DataType
    content_hash: str
    size: int
    compression: Optional[str] = None
    created_at: str = None
    metadata: Dict[str, Any] = None

class StorageInterface(ABC):
    """Abstract interface for storage backends"""
    
    @abstractmethod
    async def store_result(self, node_id: str, data: bytes, 
                          metadata: StorageMetadata) -> bool:
        """Store node execution result with zero-copy if possible"""
        pass
    
    @abstractmethod
    async def retrieve_result(self, node_id: str) -> Optional[bytes]:
        """Retrieve complete result for small objects"""
        pass
    
    @abstractmethod
    async def stream_result(self, node_id: str, 
                           chunk_size: int = 1024*1024) -> Iterator[bytes]:
        """Stream result for large objects"""
        pass
    
    @abstractmethod
    async def exists(self, node_id: str) -> bool:
        """Check if result exists"""
        pass
    
    @abstractmethod
    async def delete_result(self, node_id: str) -> bool:
        """Delete stored result"""
        pass
    
    @abstractmethod
    async def list_results(self, dag_id: Optional[str] = None) -> List[str]:
        """List stored results, optionally filtered by DAG"""
        pass
    
    @abstractmethod
    async def get_metadata(self, node_id: str) -> Optional[StorageMetadata]:
        """Get result metadata without data"""
        pass
    
    @abstractmethod
    async def update_metadata(self, node_id: str, 
                             metadata: Dict[str, Any]) -> bool:
        """Update result metadata"""
        pass
```

### SQLite Implementation

```python
import sqlite3
import aiosqlite
from typing import List

class SQLiteStorage(StorageInterface):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.chunk_threshold = 1024 * 1024  # 1MB
    
    async def store_result(self, node_id: str, data: bytes, 
                          metadata: StorageMetadata) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                # Store metadata
                await db.execute("""
                    INSERT INTO node_results 
                    (node_id, data_type, content_hash, data_size, compression, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    node_id, metadata.data_type.value, metadata.content_hash,
                    metadata.size, metadata.compression, 
                    json.dumps(metadata.metadata)
                ))
                
                # Store data
                if len(data) < self.chunk_threshold:
                    await db.execute(
                        "INSERT INTO node_data (node_id, data) VALUES (?, ?)",
                        (node_id, data)
                    )
                else:
                    # Store in chunks for large objects
                    chunk_size = 64 * 1024  # 64KB chunks
                    for i, chunk_start in enumerate(range(0, len(data), chunk_size)):
                        chunk = data[chunk_start:chunk_start + chunk_size]
                        await db.execute("""
                            INSERT INTO node_data_chunks 
                            (node_id, chunk_id, data) VALUES (?, ?, ?)
                        """, (node_id, i, chunk))
                
                await db.commit()
                return True
                
            except Exception as e:
                await db.rollback()
                raise StorageError(f"Failed to store result: {e}")
    
    async def retrieve_result(self, node_id: str) -> Optional[bytes]:
        async with aiosqlite.connect(self.db_path) as db:
            # Try single blob first
            cursor = await db.execute(
                "SELECT data FROM node_data WHERE node_id = ?", (node_id,)
            )
            row = await cursor.fetchone()
            if row:
                return row[0]
            
            # Try chunked data
            cursor = await db.execute("""
                SELECT data FROM node_data_chunks 
                WHERE node_id = ? ORDER BY chunk_id
            """, (node_id,))
            
            chunks = await cursor.fetchall()
            if chunks:
                return b''.join(chunk[0] for chunk in chunks)
            
            return None
    
    async def stream_result(self, node_id: str, 
                           chunk_size: int = 1024*1024) -> Iterator[bytes]:
        async with aiosqlite.connect(self.db_path) as db:
            # Check if stored as chunks
            cursor = await db.execute("""
                SELECT data FROM node_data_chunks 
                WHERE node_id = ? ORDER BY chunk_id
            """, (node_id,))
            
            async for row in cursor:
                yield row[0]
```

### Filesystem Implementation

```python
import aiofiles
import json
from pathlib import Path

class FilesystemStorage(StorageInterface):
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.metadata_path = self.base_path / "metadata" / "nodes"
        self.data_path = self.base_path / "data"
        
        # Ensure directories exist
        self.metadata_path.mkdir(parents=True, exist_ok=True)
        self.data_path.mkdir(parents=True, exist_ok=True)
    
    async def store_result(self, node_id: str, data: bytes, 
                          metadata: StorageMetadata) -> bool:
        try:
            # Determine storage path based on data type
            if metadata.data_type == DataType.PRIMITIVE:
                data_file = self.data_path / "primitives" / f"{node_id}.json"
            elif metadata.data_type == DataType.BINARY:
                data_file = self.data_path / "binary" / f"{node_id}.bin"
            else:
                data_file = self.data_path / "large" / node_id / "data.bin"
                data_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Store data file
            async with aiofiles.open(data_file, 'wb') as f:
                await f.write(data)
            
            # Store metadata
            metadata_file = self.metadata_path / f"{node_id}.json"
            metadata_dict = {
                "node_id": node_id,
                "data_type": metadata.data_type.value,
                "content_hash": metadata.content_hash,
                "size": metadata.size,
                "compression": metadata.compression,
                "storage": {"path": str(data_file.relative_to(self.base_path))},
                "metadata": metadata.metadata
            }
            
            async with aiofiles.open(metadata_file, 'w') as f:
                await f.write(json.dumps(metadata_dict, indent=2))
            
            return True
            
        except Exception as e:
            raise StorageError(f"Failed to store result: {e}")
    
    async def stream_result(self, node_id: str, 
                           chunk_size: int = 1024*1024) -> Iterator[bytes]:
        metadata = await self.get_metadata(node_id)
        if not metadata:
            return
        
        metadata_file = self.metadata_path / f"{node_id}.json"
        async with aiofiles.open(metadata_file, 'r') as f:
            metadata_dict = json.loads(await f.read())
        
        data_file = self.base_path / metadata_dict["storage"]["path"]
        async with aiofiles.open(data_file, 'rb') as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
```

## Node Execution API

### Node Definition

```python
from typing import List, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum

class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class NodeDefinition:
    node_id: str
    operation: str
    arguments: List[str]  # Node IDs of dependencies
    parameters: Dict[str, Any]
    metadata: Dict[str, Any] = None

@dataclass
class ExecutionResult:
    node_id: str
    status: NodeStatus
    result_data: Optional[bytes] = None
    error_message: Optional[str] = None
    execution_time_ms: Optional[int] = None
    memory_usage_bytes: Optional[int] = None

class NodeExecutor:
    """Executes individual DAG nodes"""
    
    def __init__(self, storage: StorageInterface):
        self.storage = storage
        self.operation_registry: Dict[str, Callable] = {}
    
    def register_operation(self, name: str, func: Callable):
        """Register a node operation function"""
        self.operation_registry[name] = func
    
    async def execute_node(self, node_def: NodeDefinition) -> ExecutionResult:
        """Execute a single node and store result"""
        start_time = time.time()
        
        try:
            # Get operation function
            operation_func = self.operation_registry.get(node_def.operation)
            if not operation_func:
                raise NodeExecutionError(f"Unknown operation: {node_def.operation}")
            
            # Retrieve dependency results
            dependency_data = {}
            for dep_node_id in node_def.arguments:
                dep_data = await self.storage.retrieve_result(dep_node_id)
                if dep_data is None:
                    raise NodeExecutionError(f"Dependency {dep_node_id} not found")
                dependency_data[dep_node_id] = dep_data
            
            # Execute operation with zero-copy where possible
            result_data = await operation_func(dependency_data, node_def.parameters)
            
            # Store result
            metadata = StorageMetadata(
                node_id=node_def.node_id,
                data_type=self._infer_data_type(result_data),
                content_hash=self._compute_hash(result_data),
                size=len(result_data),
                metadata=node_def.metadata
            )
            
            await self.storage.store_result(node_def.node_id, result_data, metadata)
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return ExecutionResult(
                node_id=node_def.node_id,
                status=NodeStatus.COMPLETED,
                result_data=result_data,
                execution_time_ms=execution_time
            )
            
        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                node_id=node_def.node_id,
                status=NodeStatus.FAILED,
                error_message=str(e),
                execution_time_ms=execution_time
            )
```

### Streaming Node Execution

```python
class StreamingNodeExecutor(NodeExecutor):
    """Node executor that supports streaming operations"""
    
    async def execute_streaming_node(self, node_def: NodeDefinition) -> ExecutionResult:
        """Execute node with streaming I/O for large data"""
        
        try:
            operation_func = self.operation_registry.get(f"{node_def.operation}_streaming")
            if not operation_func:
                # Fallback to regular execution
                return await self.execute_node(node_def)
            
            # Create streaming context
            stream_context = StreamingContext(
                node_id=node_def.node_id,
                storage=self.storage
            )
            
            # Execute with streaming
            async for result_chunk in operation_func(stream_context, node_def.parameters):
                await stream_context.write_chunk(result_chunk)
            
            await stream_context.finalize()
            
            return ExecutionResult(
                node_id=node_def.node_id,
                status=NodeStatus.COMPLETED
            )
            
        except Exception as e:
            return ExecutionResult(
                node_id=node_def.node_id,
                status=NodeStatus.FAILED,
                error_message=str(e)
            )

class StreamingContext:
    """Context for streaming node execution"""
    
    def __init__(self, node_id: str, storage: StorageInterface):
        self.node_id = node_id
        self.storage = storage
        self.chunks: List[bytes] = []
        self.total_size = 0
    
    async def read_dependency_stream(self, dep_node_id: str) -> Iterator[bytes]:
        """Stream dependency data"""
        async for chunk in self.storage.stream_result(dep_node_id):
            yield chunk
    
    async def write_chunk(self, chunk: bytes):
        """Write result chunk"""
        self.chunks.append(chunk)
        self.total_size += len(chunk)
    
    async def finalize(self):
        """Finalize streaming execution and store result"""
        result_data = b''.join(self.chunks)
        
        metadata = StorageMetadata(
            node_id=self.node_id,
            data_type=DataType.BINARY,
            content_hash=hashlib.sha256(result_data).hexdigest(),
            size=self.total_size
        )
        
        await self.storage.store_result(self.node_id, result_data, metadata)
```

## Scheduler API

### DAG Execution Management

```python
from typing import Set
import asyncio
from dataclasses import dataclass

@dataclass
class DAGDefinition:
    dag_id: str
    nodes: List[NodeDefinition]
    dependencies: Dict[str, List[str]]  # node_id -> [dependency_node_ids]
    metadata: Dict[str, Any] = None

@dataclass
class ExecutionConfig:
    max_concurrent_nodes: int = 4
    storage_backend: str = "sqlite"
    storage_path: str = "./voxlogica_storage"
    cache_size: str = "512MB"
    compression: bool = True
    streaming_threshold: int = 100 * 1024 * 1024  # 100MB
    retry_failed_nodes: bool = True
    max_retries: int = 3

@dataclass
class ExecutionProgress:
    dag_id: str
    total_nodes: int
    completed_nodes: int
    failed_nodes: int
    running_nodes: int
    pending_nodes: int
    progress_percentage: float
    estimated_completion: Optional[str] = None

class DynamicScheduler:
    """Dynamic scheduler for DAG execution"""
    
    def __init__(self, storage: StorageInterface, config: ExecutionConfig):
        self.storage = storage
        self.config = config
        self.executor = NodeExecutor(storage)
        self.active_executions: Dict[str, asyncio.Task] = {}
        self.execution_states: Dict[str, ExecutionProgress] = {}
    
    async def submit_dag(self, dag: DAGDefinition) -> str:
        """Submit DAG for execution"""
        # Validate DAG
        self._validate_dag(dag)
        
        # Create execution task
        execution_task = asyncio.create_task(self._execute_dag(dag))
        self.active_executions[dag.dag_id] = execution_task
        
        # Initialize progress tracking
        self.execution_states[dag.dag_id] = ExecutionProgress(
            dag_id=dag.dag_id,
            total_nodes=len(dag.nodes),
            completed_nodes=0,
            failed_nodes=0,
            running_nodes=0,
            pending_nodes=len(dag.nodes),
            progress_percentage=0.0
        )
        
        return dag.dag_id
    
    async def get_execution_progress(self, dag_id: str) -> Optional[ExecutionProgress]:
        """Get current execution progress"""
        return self.execution_states.get(dag_id)
    
    async def cancel_execution(self, dag_id: str) -> bool:
        """Cancel DAG execution"""
        if dag_id in self.active_executions:
            self.active_executions[dag_id].cancel()
            del self.active_executions[dag_id]
            return True
        return False
    
    async def _execute_dag(self, dag: DAGDefinition):
        """Execute DAG with dynamic scheduling"""
        # Build dependency graph
        dependency_graph = self._build_dependency_graph(dag)
        
        # Track node states
        node_states = {node.node_id: NodeStatus.PENDING for node in dag.nodes}
        ready_queue = asyncio.Queue()
        running_tasks: Set[asyncio.Task] = set()
        
        # Find initially ready nodes (no dependencies)
        for node in dag.nodes:
            if not dag.dependencies.get(node.node_id, []):
                await ready_queue.put(node)
        
        try:
            while not self._is_execution_complete(node_states):
                # Start new tasks if we have capacity and ready nodes
                while (len(running_tasks) < self.config.max_concurrent_nodes 
                       and not ready_queue.empty()):
                    
                    node = await ready_queue.get()
                    if node_states[node.node_id] == NodeStatus.PENDING:
                        task = asyncio.create_task(self._execute_node_with_tracking(
                            node, node_states, dependency_graph, ready_queue
                        ))
                        running_tasks.add(task)
                        node_states[node.node_id] = NodeStatus.RUNNING
                
                # Wait for at least one task to complete
                if running_tasks:
                    done, running_tasks = await asyncio.wait(
                        running_tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    # Update progress
                    self._update_execution_progress(dag.dag_id, node_states)
                
                # Small delay to prevent busy waiting
                await asyncio.sleep(0.01)
                
        except Exception as e:
            # Cancel all running tasks
            for task in running_tasks:
                task.cancel()
            raise
    
    async def _execute_node_with_tracking(self, node: NodeDefinition, 
                                         node_states: Dict[str, NodeStatus],
                                         dependency_graph: Dict[str, Set[str]],
                                         ready_queue: asyncio.Queue):
        """Execute node and update scheduling state"""
        try:
            result = await self.executor.execute_node(node)
            node_states[node.node_id] = result.status
            
            if result.status == NodeStatus.COMPLETED:
                # Check if any dependent nodes are now ready
                for dependent_node_id in dependency_graph.get(node.node_id, set()):
                    if self._all_dependencies_completed(dependent_node_id, node_states):
                        dependent_node = next(n for n in dag.nodes if n.node_id == dependent_node_id)
                        await ready_queue.put(dependent_node)
            
        except Exception as e:
            node_states[node.node_id] = NodeStatus.FAILED
            # TODO: Implement retry logic if configured
```

## Integration API

### VoxLogica-2 Integration

```python
class VoxLogicaIntegration:
    """Integration layer with VoxLogica-2 core system"""
    
    def __init__(self, scheduler: DynamicScheduler):
        self.scheduler = scheduler
    
    async def execute_program_dynamic(self, program: str, 
                                    config: ExecutionConfig) -> Dict[str, Any]:
        """Execute VoxLogica program using dynamic scheduler"""
        
        # Parse program and generate DAG (using existing VoxLogica-2 logic)
        dag = await self._generate_dag_from_program(program)
        
        # Submit for execution
        execution_id = await self.scheduler.submit_dag(dag)
        
        # Wait for completion or return progress handle
        if config.async_execution:
            return {"execution_id": execution_id, "status": "submitted"}
        else:
            return await self._wait_for_completion(execution_id)
    
    async def _generate_dag_from_program(self, program: str) -> DAGDefinition:
        """Generate DAG from VoxLogica program"""
        # This would integrate with existing VoxLogica-2 parsing/analysis
        # For now, return a placeholder
        pass
    
    async def _wait_for_completion(self, execution_id: str) -> Dict[str, Any]:
        """Wait for execution completion and return results"""
        while True:
            progress = await self.scheduler.get_execution_progress(execution_id)
            if progress.progress_percentage >= 100.0:
                break
            await asyncio.sleep(1.0)
        
        # Collect final results
        return await self._collect_execution_results(execution_id)
```

### CLI Integration

```python
import click

@click.group()
def dynamic():
    """Dynamic scheduler commands"""
    pass

@dynamic.command()
@click.argument('program_file')
@click.option('--storage-backend', default='sqlite', 
              help='Storage backend (sqlite, filesystem, hybrid)')
@click.option('--storage-path', default='./voxlogica_storage',
              help='Storage directory path')
@click.option('--max-concurrent', default=4, type=int,
              help='Maximum concurrent node executions')
@click.option('--progress', is_flag=True,
              help='Show execution progress')
async def execute(program_file: str, storage_backend: str, storage_path: str,
                 max_concurrent: int, progress: bool):
    """Execute program using dynamic scheduler"""
    
    # Read program
    with open(program_file, 'r') as f:
        program = f.read()
    
    # Configure execution
    config = ExecutionConfig(
        max_concurrent_nodes=max_concurrent,
        storage_backend=storage_backend,
        storage_path=storage_path
    )
    
    # Initialize components
    storage = create_storage_backend(storage_backend, storage_path)
    scheduler = DynamicScheduler(storage, config)
    integration = VoxLogicaIntegration(scheduler)
    
    # Execute
    try:
        if progress:
            result = await execute_with_progress(integration, program, config)
        else:
            result = await integration.execute_program_dynamic(program, config)
        
        click.echo(f"Execution completed: {result}")
        
    except Exception as e:
        click.echo(f"Execution failed: {e}", err=True)
        return 1

async def execute_with_progress(integration: VoxLogicaIntegration, 
                               program: str, config: ExecutionConfig):
    """Execute with real-time progress display"""
    import asyncio
    
    # Start execution
    config.async_execution = True
    result = await integration.execute_program_dynamic(program, config)
    execution_id = result["execution_id"]
    
    # Monitor progress
    with click.progressbar(length=100, label='Executing DAG') as bar:
        last_progress = 0
        while True:
            progress = await integration.scheduler.get_execution_progress(execution_id)
            if progress:
                current_progress = int(progress.progress_percentage)
                bar.update(current_progress - last_progress)
                last_progress = current_progress
                
                if progress.progress_percentage >= 100.0:
                    break
            
            await asyncio.sleep(0.5)
    
    return await integration._wait_for_completion(execution_id)
```

### REST API Integration

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="VoxLogica Dynamic Scheduler API")

class ExecutionRequest(BaseModel):
    program: str
    storage_backend: str = "sqlite"
    storage_path: str = "./voxlogica_storage"
    max_concurrent_nodes: int = 4
    async_execution: bool = False

class ExecutionResponse(BaseModel):
    execution_id: str
    status: str
    result: Optional[Dict[str, Any]] = None

@app.post("/api/v1/execute-dynamic", response_model=ExecutionResponse)
async def execute_dynamic(request: ExecutionRequest):
    """Execute program using dynamic scheduler"""
    
    try:
        config = ExecutionConfig(
            max_concurrent_nodes=request.max_concurrent_nodes,
            storage_backend=request.storage_backend,
            storage_path=request.storage_path
        )
        config.async_execution = request.async_execution
        
        storage = create_storage_backend(request.storage_backend, request.storage_path)
        scheduler = DynamicScheduler(storage, config)
        integration = VoxLogicaIntegration(scheduler)
        
        result = await integration.execute_program_dynamic(request.program, config)
        
        return ExecutionResponse(
            execution_id=result.get("execution_id", "sync"),
            status=result.get("status", "completed"),
            result=result if not request.async_execution else None
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/execution/{execution_id}/progress")
async def get_execution_progress(execution_id: str):
    """Get execution progress"""
    
    # TODO: Get scheduler instance (would need to be managed globally)
    progress = await scheduler.get_execution_progress(execution_id)
    
    if not progress:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    return progress

@app.delete("/api/v1/execution/{execution_id}")
async def cancel_execution(execution_id: str):
    """Cancel execution"""
    
    success = await scheduler.cancel_execution(execution_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    return {"status": "cancelled"}
```

## Error Handling

### Exception Hierarchy

```python
class DynamicSchedulerError(Exception):
    """Base exception for dynamic scheduler"""
    pass

class StorageError(DynamicSchedulerError):
    """Storage-related errors"""
    pass

class NodeExecutionError(DynamicSchedulerError):
    """Node execution errors"""
    pass

class SchedulingError(DynamicSchedulerError):
    """Scheduling-related errors"""
    pass

class ValidationError(DynamicSchedulerError):
    """DAG validation errors"""
    pass
```

### Error Recovery

```python
class ErrorRecoveryManager:
    """Manages error recovery and retry policies"""
    
    def __init__(self, max_retries: int = 3, backoff_factor: float = 2.0):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.retry_counts: Dict[str, int] = {}
    
    async def should_retry(self, node_id: str, error: Exception) -> bool:
        """Determine if node execution should be retried"""
        current_retries = self.retry_counts.get(node_id, 0)
        
        if current_retries >= self.max_retries:
            return False
        
        # Don't retry certain types of errors
        if isinstance(error, ValidationError):
            return False
        
        return True
    
    async def get_retry_delay(self, node_id: str) -> float:
        """Get delay before retry"""
        retry_count = self.retry_counts.get(node_id, 0)
        return min(60.0, self.backoff_factor ** retry_count)
```

This comprehensive API specification provides a complete interface for the Dynamic Scheduler system while maintaining integration with VoxLogica-2's existing infrastructure and supporting the zero-copy, high-performance execution requirements.
