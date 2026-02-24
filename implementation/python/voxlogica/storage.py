"""Storage contracts for symbolic definitions, runtime materialization, and result persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union
import json
import pickle
import sqlite3
import threading
import time


MATERIALIZED_STATUS = "materialized"
FAILED_STATUS = "failed"


def _default_db_path() -> Path:
    base = Path.home() / ".voxlogica"
    base.mkdir(parents=True, exist_ok=True)
    return base / "results.db"


@dataclass(frozen=True)
class ResultRecord:
    """Persistent result database record."""

    node_id: str
    status: str
    value: Any = None
    error: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    runtime_version: str = "unknown"


class ResultsDatabase(ABC):
    """Stable backend contract for durable and/or ephemeral result storage."""

    @abstractmethod
    def has(self, node_id: str) -> bool:
        """Return True when a materialized value exists for node_id."""

    @abstractmethod
    def get_record(self, node_id: str) -> ResultRecord | None:
        """Get persisted record or None when absent."""

    @abstractmethod
    def put_success(
        self,
        node_id: str,
        value: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a materialized value."""

    @abstractmethod
    def put_failure(
        self,
        node_id: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist an execution failure."""

    @abstractmethod
    def delete(self, node_id: str) -> None:
        """Delete any stored record for node_id."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all stored records."""

    @abstractmethod
    def close(self) -> None:
        """Release backend resources."""


class SQLiteResultsDatabase(ResultsDatabase):
    """SQLite-backed durable result database with deterministic schema."""

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        runtime_version: str | None = None,
    ):
        if db_path is None:
            db_path = _default_db_path()

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_version = runtime_version or self._detect_runtime_version()

        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
            timeout=5.0,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._initialize_schema()

    def _detect_runtime_version(self) -> str:
        try:
            from voxlogica.version import __version__

            return str(__version__)
        except Exception:
            return "unknown"

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS results (
                    node_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload BLOB,
                    payload_encoding TEXT NOT NULL,
                    error TEXT,
                    metadata_json TEXT NOT NULL,
                    runtime_version TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_results_status ON results(status)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_results_runtime ON results(runtime_version)"
            )

    def has(self, node_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT 1
                FROM results
                WHERE node_id = ?
                  AND runtime_version = ?
                  AND status = ?
                LIMIT 1
                """,
                (node_id, self.runtime_version, MATERIALIZED_STATUS),
            )
            return cursor.fetchone() is not None

    def get_record(self, node_id: str) -> ResultRecord | None:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT node_id, status, payload, payload_encoding, error, metadata_json,
                       created_at, updated_at, runtime_version
                FROM results
                WHERE node_id = ? AND runtime_version = ?
                """,
                (node_id, self.runtime_version),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        payload = row[2]
        encoding = row[3]
        value = None
        if row[1] == MATERIALIZED_STATUS:
            value = self._deserialize_payload(payload, encoding)

        return ResultRecord(
            node_id=row[0],
            status=row[1],
            value=value,
            error=row[4],
            metadata=self._decode_metadata(row[5]),
            created_at=float(row[6]),
            updated_at=float(row[7]),
            runtime_version=row[8],
        )

    def put_success(
        self,
        node_id: str,
        value: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload, encoding = self._serialize_payload(value)
        now = time.time()
        metadata_json = self._encode_metadata(metadata)

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO results (
                    node_id, status, payload, payload_encoding, error,
                    metadata_json, runtime_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    payload_encoding = excluded.payload_encoding,
                    error = excluded.error,
                    metadata_json = excluded.metadata_json,
                    runtime_version = excluded.runtime_version,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    MATERIALIZED_STATUS,
                    payload,
                    encoding,
                    None,
                    metadata_json,
                    self.runtime_version,
                    now,
                    now,
                ),
            )

    def put_failure(
        self,
        node_id: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        metadata_json = self._encode_metadata(metadata)

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO results (
                    node_id, status, payload, payload_encoding, error,
                    metadata_json, runtime_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    payload_encoding = excluded.payload_encoding,
                    error = excluded.error,
                    metadata_json = excluded.metadata_json,
                    runtime_version = excluded.runtime_version,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    FAILED_STATUS,
                    None,
                    "none",
                    str(error),
                    metadata_json,
                    self.runtime_version,
                    now,
                    now,
                ),
            )

    def delete(self, node_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM results WHERE node_id = ?", (node_id,)
            )

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM results")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _serialize_payload(self, value: Any) -> tuple[bytes, str]:
        try:
            return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), "pickle"
        except Exception as exc:
            raise ValueError(
                f"Value for persistence is not serializable: {type(value).__name__}: {exc}"
            ) from exc

    def _deserialize_payload(self, payload: bytes | None, encoding: str) -> Any:
        if encoding == "none" or payload is None:
            return None
        if encoding == "pickle":
            return pickle.loads(payload)
        raise ValueError(f"Unknown payload encoding: {encoding}")

    def _encode_metadata(self, metadata: Optional[Dict[str, Any]]) -> str:
        if metadata is None:
            return "{}"
        try:
            return json.dumps(metadata, sort_keys=True)
        except Exception:
            return "{}"

    def _decode_metadata(self, metadata_json: str) -> Dict[str, Any]:
        try:
            value = json.loads(metadata_json)
            if isinstance(value, dict):
                return value
            return {}
        except Exception:
            return {}


class InMemoryResultsDatabase(ResultsDatabase):
    """Ephemeral in-process backend for tests and no-persistence operation."""

    def __init__(self):
        self._records: Dict[str, ResultRecord] = {}
        self._lock = threading.RLock()

    def has(self, node_id: str) -> bool:
        with self._lock:
            record = self._records.get(node_id)
            return record is not None and record.status == MATERIALIZED_STATUS

    def get_record(self, node_id: str) -> ResultRecord | None:
        with self._lock:
            return self._records.get(node_id)

    def put_success(
        self,
        node_id: str,
        value: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        with self._lock:
            existing = self._records.get(node_id)
            created_at = existing.created_at if existing is not None else now
            self._records[node_id] = ResultRecord(
                node_id=node_id,
                status=MATERIALIZED_STATUS,
                value=value,
                metadata=dict(metadata or {}),
                created_at=created_at,
                updated_at=now,
                runtime_version="in-memory",
            )

    def put_failure(
        self,
        node_id: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        with self._lock:
            existing = self._records.get(node_id)
            created_at = existing.created_at if existing is not None else now
            self._records[node_id] = ResultRecord(
                node_id=node_id,
                status=FAILED_STATUS,
                value=None,
                error=str(error),
                metadata=dict(metadata or {}),
                created_at=created_at,
                updated_at=now,
                runtime_version="in-memory",
            )

    def delete(self, node_id: str) -> None:
        with self._lock:
            self._records.pop(node_id, None)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def close(self) -> None:
        return None


class StorageBackend(SQLiteResultsDatabase):
    """Backward-compatible alias for durable result storage."""


class NoCacheStorageBackend(InMemoryResultsDatabase):
    """Compatibility backend that disables persistent/read-through caching."""

    def has(self, node_id: str) -> bool:
        return False

    def get_record(self, node_id: str) -> ResultRecord | None:
        return None

    def put_success(
        self,
        node_id: str,
        value: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        return None

    def put_failure(
        self,
        node_id: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        return None


_storage_instance: ResultsDatabase | None = None


def get_storage(db_path: Optional[Union[str, Path]] = None) -> ResultsDatabase:
    """Get shared storage backend instance."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = StorageBackend(db_path=db_path)
    return _storage_instance


def set_storage(storage: ResultsDatabase) -> None:
    """Replace shared storage backend (mainly for tests)."""
    global _storage_instance
    _storage_instance = storage


def close_storage() -> None:
    """Close and clear the shared storage backend."""
    global _storage_instance
    if _storage_instance is not None:
        _storage_instance.close()
        _storage_instance = None


@dataclass
class MaterializationRecord:
    """Runtime materialization entry for a symbolic node."""

    status: str
    value: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class DefinitionStore:
    """Immutable symbolic definition store: NodeId -> NodeSpec."""

    def __init__(self, definitions: Optional[Dict[str, Any]] = None):
        self._definitions: Dict[str, Any] = dict(definitions or {})

    def get(self, node_id: str) -> Any:
        return self._definitions[node_id]

    def contains(self, node_id: str) -> bool:
        return node_id in self._definitions

    def items(self):
        return self._definitions.items()

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._definitions)


class MaterializationStore:
    """Runtime artifact store with optional read/write-through result backend."""

    def __init__(
        self,
        backend: ResultsDatabase | None = None,
        *,
        read_through: bool = False,
        write_through: bool = True,
    ):
        self._records: Dict[str, MaterializationRecord] = {}
        self._backend = backend
        self._read_through = read_through
        self._write_through = write_through
        self._lock = threading.RLock()

    def _materialize_from_backend(self, node_id: str) -> MaterializationRecord | None:
        if not self._read_through or self._backend is None:
            return None

        record = self._backend.get_record(node_id)
        if record is None:
            return None

        if record.status == MATERIALIZED_STATUS:
            materialized = MaterializationRecord(
                status=MATERIALIZED_STATUS,
                value=record.value,
                metadata={**record.metadata, "source": "results-db"},
            )
            self._records[node_id] = materialized
            return materialized

        failed = MaterializationRecord(
            status=FAILED_STATUS,
            value=None,
            metadata={**record.metadata, "error": record.error or "unknown"},
        )
        self._records[node_id] = failed
        return failed

    def has(self, node_id: str) -> bool:
        with self._lock:
            record = self._records.get(node_id)
            if record is not None and record.status == MATERIALIZED_STATUS:
                return True

            loaded = self._materialize_from_backend(node_id)
            return loaded is not None and loaded.status == MATERIALIZED_STATUS

    def get(self, node_id: str) -> Any:
        with self._lock:
            record = self._records.get(node_id)
            if record is None:
                record = self._materialize_from_backend(node_id)

            if record is None or record.status != MATERIALIZED_STATUS:
                raise KeyError(f"No materialized record for node {node_id}")
            return record.value

    def put(self, node_id: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            record_metadata = dict(metadata or {})
            self._records[node_id] = MaterializationRecord(
                status=MATERIALIZED_STATUS,
                value=value,
                metadata=record_metadata,
            )

            if self._write_through and self._backend is not None:
                try:
                    self._backend.put_success(node_id, value, metadata=record_metadata)
                    self._records[node_id].metadata.setdefault("persisted", True)
                except Exception as exc:  # noqa: BLE001
                    self._records[node_id].metadata["persisted"] = False
                    self._records[node_id].metadata["persist_error"] = str(exc)

    def fail(self, node_id: str, message: str) -> None:
        with self._lock:
            self._records[node_id] = MaterializationRecord(
                status=FAILED_STATUS,
                value=None,
                metadata={"error": message},
            )

            if self._write_through and self._backend is not None:
                try:
                    self._backend.put_failure(node_id, message, metadata={"error": message})
                except Exception:
                    # Runtime failure metadata remains authoritative even if persistence fails.
                    pass

    def metadata(self, node_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._records.get(node_id)
            if record is None:
                return {}
            return dict(record.metadata)

    def snapshot(self) -> Dict[str, MaterializationRecord]:
        with self._lock:
            return dict(self._records)

    @property
    def completed_nodes(self) -> set[str]:
        with self._lock:
            return {
                node_id
                for node_id, record in self._records.items()
                if record.status == MATERIALIZED_STATUS
            }
