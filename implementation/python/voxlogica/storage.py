"""
VoxLogica-2 Storage Backend

Provides persistent, content-addressed storage for computation results using SQLite
with Write-Ahead Logging (WAL) mode for thread-safe operations.
"""

import sqlite3
import json
import pickle
import threading
from pathlib import Path
from typing import Any, Optional, Dict, Set, Union
from contextlib import contextmanager
import hashlib
import os
import logging

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
        Initialize storage backend.
        
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
        
        # Thread-local storage for connections
        self._local = threading.local()
        
        # Memory cache for non-serializable results
        self._memory_cache: Dict[str, Any] = {}
        self._memory_cache_lock = threading.Lock()
        
        # Notification system for task completion (lock-free for WIP)
        self._completion_events: Dict[str, threading.Event] = {}
        
        # Initialize database schema
        self._init_database()
        
        logger.debug(f"Storage backend initialized: {self.db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'connection'):
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None  # Autocommit mode for WAL
            )
            
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=10000")
            conn.execute("PRAGMA temp_store=MEMORY")
            
            self._local.connection = conn
        
        return self._local.connection
    
    def _init_database(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
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
            
            # Indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_results_created_at ON results(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_status ON execution_state(status)")
            
            conn.commit()
    
    def store(self, operation_id: str, data: Any, metadata: Optional[Dict] = None) -> bool:
        """
        Store computation result with content-addressed key.
        
        Attempts to serialize the result with pickle for persistent storage.
        If serialization fails (e.g., for closures or non-serializable objects),
        stores the result in memory cache instead.
        
        Args:
            operation_id: SHA256 operation ID
            data: Result data to store
            metadata: Optional metadata dict
            
        Returns:
            True if stored, False if already exists
        """
        if self.exists(operation_id):
            logger.debug(f"Result already exists for operation {operation_id[:8]}...")
            return False

        try:
            # First attempt to serialize the data with pickle
            try:
                serialized_data = pickle.dumps(data)
                data_type = type(data).__name__
                size_bytes = len(serialized_data)
                metadata_json = json.dumps(metadata) if metadata else None
                
                # Store in persistent database
                with self._get_connection() as conn:
                    conn.execute("""
                        INSERT INTO results (operation_id, data, data_type, size_bytes, metadata)
                        VALUES (?, ?, ?, ?, ?)
                    """, (operation_id, serialized_data, data_type, size_bytes, metadata_json))
                    
                    conn.commit()
                
                logger.debug(f"Stored serializable result for operation {operation_id[:8]}... ({size_bytes} bytes)")
                
            except (pickle.PicklingError, TypeError, AttributeError) as e:
                # Serialization failed - store in memory cache instead
                with self._memory_cache_lock:
                    self._memory_cache[operation_id] = data
                
                logger.debug(f"Stored non-serializable result for operation {operation_id[:8]}... in memory cache: {type(data).__name__}")
            
            # Notify any threads waiting for this operation
            self._notify_completion(operation_id)
            
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
            with self._get_connection() as conn:
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
            with self._memory_cache_lock:
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
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT 1 FROM results WHERE operation_id = ? LIMIT 1
                """, (operation_id,))
                
                if cursor.fetchone() is not None:
                    return True
            
            # Check memory cache for non-serializable results
            with self._memory_cache_lock:
                return operation_id in self._memory_cache
                
        except Exception as e:
            logger.error(f"Failed to check existence for operation {operation_id[:8]}...: {e}")
            raise

    def wait_for_completion(self, operation_id: str, timeout: float = 300.0) -> Any:
        """
        Wait for operation to complete using event notification.
        
        More efficient than polling - uses threading events for immediate
        notification when results become available.
        
        Args:
            operation_id: Operation ID to wait for
            timeout: Maximum time to wait in seconds
            
        Returns:
            Result of the operation
            
        Raises:
            TimeoutError: If timeout is reached
            Exception: If operation failed
        """
        # First check if result already exists
        if self.exists(operation_id):
            return self.retrieve(operation_id)
        
        # Create or get completion event (lock-free for WIP)
        if operation_id not in self._completion_events:
            self._completion_events[operation_id] = threading.Event()
        event = self._completion_events[operation_id]
        
        # Wait for completion
        if event.wait(timeout):
            # Event was set - check if we have a result or error
            if self.exists(operation_id):
                return self.retrieve(operation_id)
            else:
                # Check if operation failed
                try:
                    with self._get_connection() as conn:
                        cursor = conn.execute("""
                            SELECT status, error_message FROM execution_state 
                            WHERE operation_id = ?
                        """, (operation_id,))
                        row = cursor.fetchone()
                        if row and row[0] == 'failed':
                            raise Exception(f"Operation failed: {row[1]}")
                except sqlite3.Error:
                    pass  # Ignore DB errors, fall back to generic error
                
                raise Exception(f"Operation {operation_id[:8]}... completed but no result found")
        else:
            # Timeout occurred - cleanup event (lock-free)
            self._completion_events.pop(operation_id, None)
            raise TimeoutError(f"Timeout waiting for operation {operation_id[:8]}... to complete")
    
    def mark_running(self, operation_id: str, worker_id: str|None = None) -> bool:
        """
        Mark operation as running to prevent duplicate computation.
        
        Args:
            operation_id: SHA256 operation ID
            worker_id: Optional worker identifier
            
        Returns:
            True if successfully marked, False if already running/completed
        """
        try:
            worker_id = worker_id or f"worker_{os.getpid()}"
            
            with self._get_connection() as conn:
                # Try to insert new running state
                try:
                    conn.execute("""
                        INSERT INTO execution_state (operation_id, status, worker_id)
                        VALUES (?, 'running', ?)
                    """, (operation_id, worker_id))
                    conn.commit()
                    logger.debug(f"Marked operation {operation_id[:8]}... as running")
                    return True
                    
                except sqlite3.IntegrityError:
                    # Already exists, check status
                    cursor = conn.execute("""
                        SELECT status FROM execution_state WHERE operation_id = ?
                    """, (operation_id,))
                    
                    row = cursor.fetchone()
                    if row and row[0] in ('running', 'completed'):
                        logger.debug(f"Operation {operation_id[:8]}... already {row[0]}")
                        return False
                    
                    # Failed state, allow retry
                    conn.execute("""
                        UPDATE execution_state 
                        SET status = 'running', worker_id = ?, started_at = CURRENT_TIMESTAMP
                        WHERE operation_id = ?
                    """, (worker_id, operation_id))
                    conn.commit()
                    logger.debug(f"Marked failed operation {operation_id[:8]}... as running (retry)")
                    return True
                    
        except Exception as e:
            logger.error(f"Failed to mark operation {operation_id[:8]}... as running: {e}")
            raise
    
    def mark_completed(self, operation_id: str) -> None:
        """Mark operation as completed."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE execution_state 
                    SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                    WHERE operation_id = ?
                """, (operation_id,))
                conn.commit()
                
            logger.debug(f"Marked operation {operation_id[:8]}... as completed")
            
        except Exception as e:
            logger.error(f"Failed to mark operation {operation_id[:8]}... as completed: {e}")
            raise
    
    def mark_failed(self, operation_id: str, error_message: str) -> None:
        """Mark operation as failed with error message."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE execution_state 
                    SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ?
                    WHERE operation_id = ?
                """, (error_message, operation_id))
                conn.commit()
                
            logger.debug(f"Marked operation {operation_id[:8]}... as failed: {error_message}")
            
            # Notify any threads waiting for this operation
            self._notify_failure(operation_id)
            
        except Exception as e:
            logger.error(f"Failed to mark operation {operation_id[:8]}... as failed: {e}")
            raise
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            with self._get_connection() as conn:
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
                
                # Memory cache statistics
                with self._memory_cache_lock:
                    memory_cache_count = len(self._memory_cache)
                
                return {
                    "total_results": results_stats[0] or 0,
                    "total_size_bytes": results_stats[1] or 0,
                    "avg_size_bytes": results_stats[2] or 0,
                    "memory_cache_count": memory_cache_count,
                    "execution_states": execution_stats,
                    "database_path": str(self.db_path)
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
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    UPDATE execution_state 
                    SET status = 'failed', 
                        completed_at = CURRENT_TIMESTAMP,
                        error_message = 'Cleanup: execution timed out'
                    WHERE status = 'running' 
                    AND datetime(started_at) < datetime('now', '-{} hours')
                """.format(max_age_hours))
                
                cleaned_count = cursor.rowcount
                conn.commit()
                
                if cleaned_count > 0:
                    logger.info(f"Cleaned up {cleaned_count} stale running executions")
                
                return cleaned_count
                
        except Exception as e:
            logger.error(f"Failed to cleanup failed executions: {e}")
            raise

    def _notify_completion(self, operation_id: str):
        """
        Notify threads waiting for operation completion.
        
        Args:
            operation_id: Operation that completed
        """
        # Lock-free notification for WIP system
        if operation_id in self._completion_events:
            event = self._completion_events[operation_id]
            event.set()
            # Note: Don't delete the event here - let the waiter clean it up
            # This avoids race conditions where the event is deleted before
            # the waiter can check the result

    def _notify_failure(self, operation_id: str):
        """
        Notify threads waiting for operation that it failed.
        
        Args:
            operation_id: Operation that failed
        """
        # Lock-free notification for WIP system
        if operation_id in self._completion_events:
            event = self._completion_events[operation_id]
            event.set()
            # Note: Don't delete the event here - let the waiter clean it up
    
    def close(self):
        """Close database connections."""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()
            del self._local.connection


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
        import uuid
        db_name = f"no_cache_{uuid.uuid4().hex}"
        db_path = f"file:{db_name}?mode=memory&cache=shared"
        
        # Initialize with shared in-memory database
        self.db_path = db_path
        
        # Thread-local storage for connections
        self._local = threading.local()
        
        # Memory cache for non-serializable results (same as parent)
        self._memory_cache: Dict[str, Any] = {}
        self._memory_cache_lock = threading.Lock()
        
        # Notification system for task completion (lock-free for WIP)
        self._completion_events: Dict[str, threading.Event] = {}
        
        # Initialize database schema (same as parent but in memory)
        self._init_database()
        
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
