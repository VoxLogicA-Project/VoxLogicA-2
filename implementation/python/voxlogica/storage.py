"""
VoxLogica-2 Storage Backend

Provides persistent, content-addressed storage for computation results using SQLite
with Write-Ahead Logging (WAL) mode for thread-safe operations.
"""

import sqlite3
import json
import pickle
from pathlib import Path
from typing import Any, Optional, Dict, Set, Union, List, Callable
from contextlib import contextmanager
import hashlib
import os
import logging
import time
import threading
import queue
import uuid
import atexit

logger = logging.getLogger("voxlogica.storage")


class StorageBackend:
    """
    Content-addressed storage backend using SQLite with WAL mode.
    
    Features:
    - Immutable, content-addressed storage using SHA256 keys
    - Thread-safe operations with WAL mode
    - Automatic serialization/deserialization
    - Metadata tracking
    - Cross-platform compatibility
    """
    
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        """
        Initialize storage backend with single-connection architecture.
        
        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Default to ~/.voxlogica/storage.db
            home_dir = Path.home()
            voxlogica_dir = home_dir / ".voxlogica"
            voxlogica_dir.mkdir(exist_ok=True)
            db_path = voxlogica_dir / "storage.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Single persistent database connection
        self._connection: Optional[sqlite3.Connection] = None
        self._connection_lock = threading.RLock()  # Protect connection access
        
        # Memory cache for non-serializable results
        self._memory_cache: Dict[str, Any] = {}
        
        # Active operations tracking
        self._active_operations: Set[str] = set()  # Track running operation UUIDs
        self._completion_callbacks: Dict[str, List[Callable[[], None]]] = {}  # Waiters for operations
        
        # Background result writer
        self._result_write_queue: queue.Queue = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None
        self._notification_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        
        # Initialize database and start background services
        self._create_connection()
        self._init_database()
        self._cleanup_stale_operations()
        self._start_background_writer()
        self._setup_update_hooks()
        
        # Register cleanup on exit
        atexit.register(self._shutdown)
        
        logger.debug(f"Storage backend initialized: {self.db_path}")
        logger.debug(f"Active operations at startup: {len(self._active_operations)}")
    
    def _create_connection(self) -> sqlite3.Connection:
        """Create and configure the single persistent database connection."""
        with self._connection_lock:
            if self._connection is not None:
                return self._connection
                
            self._connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode for WAL
                timeout=5.0  # 5 second timeout for database locks
            )
            
            # Enable WAL mode for better concurrency
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("PRAGMA cache_size=10000")
            self._connection.execute("PRAGMA temp_store=MEMORY")
            self._connection.execute("PRAGMA busy_timeout=5000")  # 5 second busy timeout
            
            return self._connection
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get the single persistent database connection."""
        with self._connection_lock:
            if self._connection is None:
                self._create_connection()
            assert self._connection is not None
            return self._connection
    
    def _init_database(self):
        """Initialize database schema."""
        conn = self._get_connection()
        with self._connection_lock:
            # Results table for storing computation results
            conn.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    operation_id TEXT PRIMARY KEY,
                    data BLOB NOT NULL,
                    data_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    size_bytes INTEGER,
                    metadata TEXT
                )
            """)
            
            # Execution state table for tracking running computations
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_state (
                    operation_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,  -- 'running', 'completed', 'failed'
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    worker_id TEXT
                )
            """)
            
            # Session state table for tracking active operations across restarts
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_state (
                    operation_id TEXT PRIMARY KEY,
                    worker_uuid TEXT NOT NULL,
                    session_started TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_results_created_at ON results(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_status ON execution_state(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_heartbeat ON session_state(last_heartbeat)")
            
            conn.commit()
    
    def store(self, operation_id: str, data: Any, metadata: Optional[Dict] = None) -> bool:
        """
        Store computation result with content-addressed key.
        
        Queues serializable results for background writing to prevent blocking.
        Stores non-serializable results in memory cache immediately.
        
        Args:
            operation_id: SHA256 operation ID
            data: Result data to store
            metadata: Optional metadata dict
            
        Returns:
            True if stored/queued, False if already exists
        """
        try:
            # Check if result already exists (fast check before expensive serialization)
            if self.exists(operation_id):
                logger.debug(f"Result already exists for operation {operation_id[:8]}... (early detection)")
                return False
            
            # Attempt to serialize the data with pickle
            try:
                serialized_data = pickle.dumps(data)
                data_type = type(data).__name__
                size_bytes = len(serialized_data)
                metadata_json = json.dumps(metadata) if metadata else None
                
                # Queue for background writing (non-blocking)
                write_request = (operation_id, serialized_data, data_type, size_bytes, metadata_json)
                self._result_write_queue.put(write_request)
                
                logger.debug(f"Queued serializable result for operation {operation_id[:8]}... ({size_bytes} bytes)")
                
            except (pickle.PicklingError, TypeError, AttributeError):
                # Serialization failed - store in memory cache instead
                self._memory_cache[operation_id] = data
                logger.debug(f"Stored non-serializable result for operation {operation_id[:8]}... in memory cache: {type(data).__name__}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to store result for operation {operation_id[:8]}...: {e}")
            raise
    
    def retrieve(self, operation_id: str) -> Optional[Any]:
        """
        Retrieve computation result by operation ID.
        
        Checks persistent storage first, then memory cache for non-serializable results.
        
        Args:
            operation_id: SHA256 operation ID
            
        Returns:
            Stored data or None if not found
        """
        try:
            # First check persistent storage
            conn = self._get_connection()
            with self._connection_lock:
                cursor = conn.execute("""
                    SELECT data FROM results WHERE operation_id = ?
                """, (operation_id,))
                
                row = cursor.fetchone()
                if row is not None:
                    # Deserialize data from persistent storage
                    data = pickle.loads(row[0])
                    logger.debug(f"Retrieved serializable result for operation {operation_id[:8]}...")
                    return data
            
            # Check memory cache for non-serializable results
            if operation_id in self._memory_cache:
                data = self._memory_cache[operation_id]
                logger.debug(f"Retrieved non-serializable result for operation {operation_id[:8]}... from memory cache")
                return data
            
            # Not found in either location
            return None
                
        except Exception as e:
            logger.error(f"Failed to retrieve result for operation {operation_id[:8]}...: {e}")
            raise
    
    def exists(self, operation_id: str) -> bool:
        """
        Check if result exists for operation ID.
        
        Checks both persistent storage and memory cache.
        """
        try:
            # Check persistent storage first
            conn = self._get_connection()
            with self._connection_lock:
                cursor = conn.execute("""
                    SELECT 1 FROM results WHERE operation_id = ? LIMIT 1
                """, (operation_id,))
                
                if cursor.fetchone() is not None:
                    return True
            
            # Check memory cache for non-serializable results
            return operation_id in self._memory_cache
                
        except Exception as e:
            logger.error(f"Failed to check existence for operation {operation_id[:8]}...: {e}")
            raise

    def wait_for_completion(self, operation_id: str, timeout: float = 300.0) -> Any:
        """
        Wait for operation to complete using event-driven notifications.
        
        Uses SQLite update hooks for real-time completion detection instead of polling.
        
        Args:
            operation_id: Operation ID to wait for
            timeout: Maximum time to wait in seconds
            
        Returns:
            Result of the operation
            
        Raises:
            TimeoutError: If timeout is reached
            Exception: If operation failed
        """
        # Check if result already exists
        if self.exists(operation_id):
            return self.retrieve(operation_id)
        
        # Check if operation failed
        conn = self._get_connection()
        with self._connection_lock:
            cursor = conn.execute("""
                SELECT status, error_message FROM execution_state 
                WHERE operation_id = ?
            """, (operation_id,))
            row = cursor.fetchone()
            if row and row[0] == 'failed':
                raise Exception(f"Operation failed: {row[1]}")
        
        # Set up completion event and callback
        completion_event = threading.Event()
        
        def completion_callback():
            completion_event.set()
        
        # Register callback for completion notification
        if operation_id not in self._completion_callbacks:
            self._completion_callbacks[operation_id] = []
        self._completion_callbacks[operation_id].append(completion_callback)
        
        try:
            # Wait for completion with timeout
            if completion_event.wait(timeout):
                # Check for failure one more time
                with self._connection_lock:
                    cursor = conn.execute("""
                        SELECT status, error_message FROM execution_state 
                        WHERE operation_id = ?
                    """, (operation_id,))
                    row = cursor.fetchone()
                    if row and row[0] == 'failed':
                        raise Exception(f"Operation failed: {row[1]}")
                
                # Return the result
                result = self.retrieve(operation_id)
                if result is not None:
                    return result
                else:
                    raise Exception(f"Operation completed but result not found for {operation_id[:8]}...")
            else:
                # Timeout occurred
                raise TimeoutError(f"Timeout waiting for operation {operation_id[:8]}... to complete")
                
        finally:
            # Clean up callback if still registered
            if operation_id in self._completion_callbacks:
                callbacks = self._completion_callbacks[operation_id]
                if completion_callback in callbacks:
                    callbacks.remove(completion_callback)
                if not callbacks:
                    del self._completion_callbacks[operation_id]
    
    def mark_running(self, operation_id: str, worker_id: str|None = None) -> bool:
        """
        Mark operation as running using atomic claim verification.
        
        Each worker generates a unique UUID and tries to claim the operation.
        Only the worker whose UUID is actually stored wins the claim.
        Also tracks the UUID in memory and session state for persistence.
        
        Args:
            operation_id: SHA256 operation ID
            worker_id: Optional worker identifier (ignored, UUID generated instead)
            
        Returns:
            True if successfully claimed, False if already claimed by another worker
        """
        # Generate unique UUID for this specific claim attempt
        claim_uuid = str(uuid.uuid4())
        
        try:
            conn = self._get_connection()
            with self._connection_lock:
                # Try to insert our claim atomically (first writer wins)
                conn.execute("""
                    INSERT OR IGNORE INTO execution_state (operation_id, status, worker_id)
                    VALUES (?, 'running', ?)
                """, (operation_id, claim_uuid))
                conn.commit()
                
                # Read back what's actually stored to verify our claim
                cursor = conn.execute("""
                    SELECT worker_id, status FROM execution_state WHERE operation_id = ?
                """, (operation_id,))
                
                row = cursor.fetchone()
                if row is None:
                    # This shouldn't happen, but handle gracefully
                    logger.warning(f"Failed to read back execution state for operation {operation_id[:8]}...")
                    return False
                    
                stored_worker_id, status = row
                
                # We win if our UUID is the one that got stored
                if stored_worker_id == claim_uuid:
                    # Track in memory and session state
                    self._active_operations.add(claim_uuid)
                    
                    # Track in session state for persistence
                    conn.execute("""
                        INSERT OR REPLACE INTO session_state (operation_id, worker_uuid)
                        VALUES (?, ?)
                    """, (operation_id, claim_uuid))
                    conn.commit()
                    
                    logger.debug(f"Successfully claimed operation {operation_id[:8]}... with UUID {claim_uuid[:8]}...")
                    return True
                else:
                    # Someone else's claim was stored first
                    logger.debug(f"Operation {operation_id[:8]}... already claimed by {stored_worker_id[:8]}... (status: {status})")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to claim operation {operation_id[:8]}...: {e}")
            raise
    
    def mark_completed(self, operation_id: str) -> None:
        """Mark operation as completed and clean up session state."""
        try:
            conn = self._get_connection()
            with self._connection_lock:
                # Get the worker UUID before marking complete
                cursor = conn.execute("""
                    SELECT worker_id FROM execution_state WHERE operation_id = ?
                """, (operation_id,))
                row = cursor.fetchone()
                worker_uuid = row[0] if row else None
                
                # Mark as completed
                conn.execute("""
                    UPDATE execution_state 
                    SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                    WHERE operation_id = ?
                """, (operation_id,))
                
                # Clean up session state
                conn.execute("""
                    DELETE FROM session_state WHERE operation_id = ?
                """, (operation_id,))
                
                conn.commit()
                
                # Clean up memory tracking
                if worker_uuid and worker_uuid in self._active_operations:
                    self._active_operations.remove(worker_uuid)
                
            logger.debug(f"Marked operation {operation_id[:8]}... as completed")
            
        except Exception as e:
            logger.error(f"Failed to mark operation {operation_id[:8]}... as completed: {e}")
            raise
    
    def mark_failed(self, operation_id: str, error_message: str) -> None:
        """Mark operation as failed with error message and clean up session state."""
        try:
            conn = self._get_connection()
            with self._connection_lock:
                # Get the worker UUID before marking failed
                cursor = conn.execute("""
                    SELECT worker_id FROM execution_state WHERE operation_id = ?
                """, (operation_id,))
                row = cursor.fetchone()
                worker_uuid = row[0] if row else None
                
                # Mark as failed
                conn.execute("""
                    UPDATE execution_state 
                    SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ?
                    WHERE operation_id = ?
                """, (error_message, operation_id))
                
                # Clean up session state
                conn.execute("""
                    DELETE FROM session_state WHERE operation_id = ?
                """, (operation_id,))
                
                conn.commit()
                
                # Clean up memory tracking
                if worker_uuid and worker_uuid in self._active_operations:
                    self._active_operations.remove(worker_uuid)
                
            logger.debug(f"Marked operation {operation_id[:8]}... as failed: {error_message}")
            
        except Exception as e:
            logger.error(f"Failed to mark operation {operation_id[:8]}... as failed: {e}")
            raise
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            conn = self._get_connection()
            with self._connection_lock:
                # Results statistics
                cursor = conn.execute("""
                    SELECT 
                        COUNT(*) as total_results,
                        SUM(size_bytes) as total_size_bytes,
                        AVG(size_bytes) as avg_size_bytes
                    FROM results
                """)
                results_stats = cursor.fetchone()
                
                # Execution state statistics
                cursor = conn.execute("""
                    SELECT status, COUNT(*) as count
                    FROM execution_state
                    GROUP BY status
                """)
                execution_stats = dict(cursor.fetchall())
                
                # Session state statistics
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM session_state
                """)
                active_sessions = cursor.fetchone()[0]
                
                # Memory cache statistics
                memory_cache_count = len(self._memory_cache)
                active_operations_count = len(self._active_operations)
                pending_writes = self._result_write_queue.qsize()
                
                return {
                    "total_results": results_stats[0] or 0,
                    "total_size_bytes": results_stats[1] or 0,
                    "avg_size_bytes": results_stats[2] or 0,
                    "memory_cache_count": memory_cache_count,
                    "active_operations_count": active_operations_count,
                    "active_sessions": active_sessions,
                    "pending_writes": pending_writes,
                    "execution_states": execution_stats,
                    "database_path": str(self.db_path),
                    "architecture": "single-connection with background writer"
                }
                
        except Exception as e:
            logger.error(f"Failed to get storage statistics: {e}")
            raise
    
    def cleanup_failed_executions(self, max_age_hours: int = 24) -> int:
        """
        Clean up stale 'running' executions that may have failed without cleanup.
        
        Args:
            max_age_hours: Maximum age in hours for running executions
            
        Returns:
            Number of cleaned up executions
        """
        try:
            conn = self._get_connection()
            with self._connection_lock:
                # Get UUIDs of operations being cleaned up
                cursor = conn.execute("""
                    SELECT worker_id FROM execution_state
                    WHERE status = 'running' 
                    AND datetime(started_at) < datetime('now', '-{} hours')
                """.format(max_age_hours))
                stale_uuids = [row[0] for row in cursor.fetchall()]
                
                # Update stale executions
                cursor = conn.execute("""
                    UPDATE execution_state 
                    SET status = 'failed', 
                        completed_at = CURRENT_TIMESTAMP,
                        error_message = 'Cleanup: execution timed out'
                    WHERE status = 'running' 
                    AND datetime(started_at) < datetime('now', '-{} hours')
                """.format(max_age_hours))
                
                cleaned_count = cursor.rowcount
                
                # Clean up corresponding session state
                if stale_uuids:
                    placeholders = ','.join(['?' for _ in stale_uuids])
                    conn.execute(f"""
                        DELETE FROM session_state 
                        WHERE worker_uuid IN ({placeholders})
                    """, stale_uuids)
                    
                    # Clean up memory tracking
                    for uuid_str in stale_uuids:
                        self._active_operations.discard(uuid_str)
                
                conn.commit()
                
                if cleaned_count > 0:
                    logger.info(f"Cleaned up {cleaned_count} stale running executions")
                
                return cleaned_count
                
        except Exception as e:
            logger.error(f"Failed to cleanup failed executions: {e}")
            raise

    def close(self):
        """Close storage backend and clean up resources."""
        self._shutdown()

    def _cleanup_stale_operations(self):
        """Clean up stale operations from previous sessions and load active ones."""
        conn = self._get_connection()
        with self._connection_lock:
            # Clean up stale session entries (older than 1 hour)
            conn.execute("""
                DELETE FROM session_state 
                WHERE datetime(last_heartbeat) < datetime('now', '-1 hour')
            """)
            
            # Load currently active operations from session_state
            cursor = conn.execute("""
                SELECT operation_id, worker_uuid FROM session_state
            """)
            for operation_id, worker_uuid in cursor.fetchall():
                self._active_operations.add(worker_uuid)
                logger.debug(f"Restored active operation {operation_id[:8]}... with UUID {worker_uuid[:8]}...")
            
            conn.commit()
    
    def _start_background_writer(self):
        """Start the background thread for result writes."""
        self._writer_thread = threading.Thread(
            target=self._background_writer,
            name="StorageWriter",
            daemon=True
        )
        self._writer_thread.start()
        logger.debug("Background result writer thread started")
    
    def _background_writer(self):
        """Background thread that processes result writes."""
        while not self._shutdown_event.is_set():
            try:
                # Wait for write requests with timeout
                try:
                    write_request = self._result_write_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                if write_request is None:  # Shutdown signal
                    break
                
                # Unpack write request
                operation_id, serialized_data, data_type, size_bytes, metadata_json = write_request
                
                # Perform the actual write
                conn = self._get_connection()
                with self._connection_lock:
                    try:
                        cursor = conn.execute("""
                            INSERT OR IGNORE INTO results (operation_id, data, data_type, size_bytes, metadata)
                            VALUES (?, ?, ?, ?, ?)
                        """, (operation_id, serialized_data, data_type, size_bytes, metadata_json))
                        
                        conn.commit()
                        
                        if cursor.rowcount > 0:
                            logger.debug(f"Background writer stored result for operation {operation_id[:8]}... ({size_bytes} bytes)")
                        else:
                            logger.debug(f"Background writer: result already exists for operation {operation_id[:8]}...")
                            
                    except sqlite3.Error as e:
                        logger.error(f"Background writer failed to store operation {operation_id[:8]}...: {e}")
                
                self._result_write_queue.task_done()
                
            except Exception as e:
                logger.error(f"Background writer error: {e}")
        
        logger.debug("Background result writer thread stopped")
    
    def _setup_update_hooks(self):
        """Set up notification mechanism (polling fallback since update hooks not available)."""
        # SQLite update hooks are not available in standard Python sqlite3
        # We'll use a polling-based notification system as fallback
        logger.debug("Using polling-based notification (SQLite update hooks not available)")
        
        # Start a background notification thread
        self._notification_thread = threading.Thread(
            target=self._notification_poller,
            name="StorageNotifier",
            daemon=True
        )
        self._notification_thread.start()
        logger.debug("Background notification thread started")
    
    def _notification_poller(self):
        """Background thread that polls for execution state changes and notifies waiters."""
        last_check_time = time.time()
        
        while not self._shutdown_event.is_set():
            try:
                # Poll every 100ms for completion
                time.sleep(0.1)
                
                if not self._completion_callbacks:
                    continue  # No waiters, skip polling
                
                # Check for completed/failed operations
                current_time = time.time()
                operations_to_check = list(self._completion_callbacks.keys())
                
                if operations_to_check:
                    conn = self._get_connection()
                    with self._connection_lock:
                        placeholders = ','.join(['?' for _ in operations_to_check])
                        cursor = conn.execute(f"""
                            SELECT operation_id, status FROM execution_state 
                            WHERE operation_id IN ({placeholders}) 
                            AND status IN ('completed', 'failed')
                        """, operations_to_check)
                        
                        completed_ops = cursor.fetchall()
                        
                        for operation_id, status in completed_ops:
                            # Notify all waiters for this operation
                            callbacks = self._completion_callbacks.get(operation_id, [])
                            for callback in callbacks:
                                try:
                                    callback()
                                except Exception as e:
                                    logger.error(f"Error in completion callback for {operation_id[:8]}...: {e}")
                            
                            # Clean up callbacks
                            if operation_id in self._completion_callbacks:
                                del self._completion_callbacks[operation_id]
                            
                            logger.debug(f"Notified {len(callbacks)} waiters for operation {operation_id[:8]}... (status: {status})")
                
            except Exception as e:
                logger.error(f"Error in notification poller: {e}")
        
        logger.debug("Background notification thread stopped")
    
    def _shutdown(self):
        """Shutdown background services."""
        logger.debug("Shutting down storage backend...")
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Stop background writer
        if self._writer_thread and self._writer_thread.is_alive():
            # Send shutdown signal
            self._result_write_queue.put(None)
            self._writer_thread.join(timeout=5.0)
        
        # Stop notification thread
        if self._notification_thread and self._notification_thread.is_alive():
            self._notification_thread.join(timeout=5.0)
        
        # Close database connection
        with self._connection_lock:
            if self._connection:
                self._connection.close()
                self._connection = None
        
        logger.debug("Storage backend shutdown complete")


class NoCacheStorageBackend(StorageBackend):
    """
    Storage backend that uses in-memory SQLite for temporary storage.
    
    This backend provides all the functionality of StorageBackend but 
    uses an in-memory SQLite database that doesn't persist to disk.
    Results are available during execution but are lost when the 
    backend is destroyed, ensuring no caching between runs.
    """
    
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        """Initialize no-cache storage backend with in-memory SQLite."""
        # Use a shared in-memory database with a unique name
        # This allows multiple connections to access the same in-memory database
        db_name = f"no_cache_{uuid.uuid4().hex}"
        db_path = f"file:{db_name}?mode=memory&cache=shared"
        
        # Initialize with shared in-memory database using parent's single-connection architecture
        super().__init__(db_path)
        
        logger.debug(f"No-cache storage backend initialized with shared in-memory SQLite - results will not persist to disk")
    
    def wait_for_completion(self, operation_id: str, timeout: float = 300.0) -> Any:
        """Wait for operation completion (works normally with in-memory storage)."""
        return super().wait_for_completion(operation_id, timeout)
    
    def mark_running(self, operation_id: str, worker_id: str|None = None) -> bool:
        """Mark operation as running (works normally with in-memory storage)."""
        return super().mark_running(operation_id, worker_id)
    
    def mark_completed(self, operation_id: str) -> None:
        """Mark operation as completed (works normally with in-memory storage)."""
        super().mark_completed(operation_id)
    
    def mark_failed(self, operation_id: str, error_message: str) -> None:
        """Mark operation as failed (works normally with in-memory storage)."""
        super().mark_failed(operation_id, error_message)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get storage statistics (from in-memory database)."""
        stats = super().get_statistics()
        stats["type"] = "no-cache (in-memory)"
        stats["description"] = "No-cache storage backend using in-memory SQLite - results will not persist to disk"
        return stats
    
    def cleanup_failed_executions(self, max_age_hours: float = 24.0) -> int:
        """Cleanup failed executions (works normally with in-memory storage)."""
        return super().cleanup_failed_executions(int(max_age_hours))
    
    def close(self):
        """Close storage backend (in-memory database will be automatically destroyed)."""
        super().close()
        logger.debug("No-cache: in-memory database closed and destroyed")


# Global storage instance
_storage_instance: Optional[StorageBackend] = None


def get_storage(db_path: Optional[Union[str, Path]] = None) -> StorageBackend:
    """Get global storage instance."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = StorageBackend(db_path)
    return _storage_instance


def set_storage(storage: StorageBackend) -> None:
    """Set global storage instance (for testing)."""
    global _storage_instance
    _storage_instance = storage
