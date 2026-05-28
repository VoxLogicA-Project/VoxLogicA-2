"""Persistent definition and materialization stores for DAG execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union
import logging
import queue
import sqlite3
import threading
import time

from voxlogica.lazy.hash import node_payload
from voxlogica.lazy.ir import NodeSpec, SymbolicPlan
from voxlogica.pod_codec import (
    can_serialize_value,
    decode_runtime_value,
    dumps_json,
    encode_for_storage,
    loads_json,
)
from voxlogica.value_model import VOX_FORMAT_VERSION


MATERIALIZED_STATUS = "materialized"
PLANNED_STATUS = "planned"
STORE_SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    base = Path.home() / ".voxlogica"
    base.mkdir(parents=True, exist_ok=True)
    return base / "results.db"


@dataclass(frozen=True)
class ResultRecord:
    """Persistent result database record."""

    node_id: str
    status: str
    format_version: str = VOX_FORMAT_VERSION
    vox_type: str | None = None
    descriptor: dict[str, Any] = field(default_factory=dict)
    payload_json: dict[str, Any] = field(default_factory=dict)
    value: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    expression: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    runtime_version: str = "unknown"


class SQLiteResultsDatabase:
    """SQLite plus payload-file storage keyed by stable node hashes."""

    def __init__(self, db_path: str | Path | None = None, runtime_version: str | None = None):
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.payload_dir = self.db_path.with_suffix(self.db_path.suffix + ".files")
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_version = runtime_version or "unknown"
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None, timeout=5.0)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            version = int((self._connection.execute("PRAGMA user_version").fetchone() or [0])[0])
            if version != STORE_SCHEMA_VERSION:
                self._connection.execute("DROP TABLE IF EXISTS results")
                self._connection.execute(
                    """
                    CREATE TABLE results (
                        node_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        format_version TEXT NOT NULL,
                        vox_type TEXT,
                        descriptor_json TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_file TEXT,
                        error TEXT,
                        metadata_json TEXT NOT NULL,
                        expression_json TEXT NOT NULL,
                        dependencies_json TEXT NOT NULL,
                        runtime_version TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                self._connection.execute("CREATE INDEX idx_results_status ON results(status)")
                self._connection.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")

    def has(self, node_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM results WHERE node_id = ? AND status = ? LIMIT 1",
                (node_id, MATERIALIZED_STATUS),
            ).fetchone()
            return row is not None

    def put_definition(self, node_id: str, node: NodeSpec) -> None:
        expression = node_payload(node)
        dependencies = list(node.args) + [value for _key, value in node.kwargs]
        now = time.time()
        with self._lock:
            row = self._connection.execute("SELECT status, created_at FROM results WHERE node_id = ?", (node_id,)).fetchone()
            if row is not None and str(row[0]) == MATERIALIZED_STATUS:
                return
            created_at = float(row[1]) if row is not None else now
            self._connection.execute(
                """
                INSERT INTO results (
                    node_id, status, format_version, vox_type, descriptor_json,
                    payload_json, payload_file, error, metadata_json, expression_json,
                    dependencies_json, runtime_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    status = CASE WHEN results.status = ? THEN results.status ELSE excluded.status END,
                    expression_json = excluded.expression_json,
                    dependencies_json = excluded.dependencies_json,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    PLANNED_STATUS,
                    VOX_FORMAT_VERSION,
                    None,
                    "{}",
                    "{}",
                    None,
                    None,
                    "{}",
                    dumps_json(expression),
                    dumps_json({"dependencies": dependencies}),
                    self.runtime_version,
                    created_at,
                    now,
                    MATERIALIZED_STATUS,
                ),
            )

    def put_plan_definitions(self, plan: SymbolicPlan) -> None:
        for node_id, node in plan.nodes.items():
            self.put_definition(node_id, node)

    def get_record(self, node_id: str) -> ResultRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT node_id, status, format_version, vox_type, descriptor_json,
                       payload_json, payload_file, error, metadata_json, expression_json,
                       dependencies_json, created_at, updated_at, runtime_version
                FROM results WHERE node_id = ?
                """,
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        payload_bin = None
        payload_file = row[6]
        if payload_file:
            path = self.payload_dir / str(payload_file)
            if path.exists():
                payload_bin = path.read_bytes()
        payload_json = loads_json(row[5])
        vox_type = str(row[3] or "")
        value = None
        if row[1] == MATERIALIZED_STATUS and vox_type:
            value = decode_runtime_value(vox_type, payload_json, payload_bin)
        dependencies_payload = loads_json(row[10])
        return ResultRecord(
            node_id=str(row[0]),
            status=str(row[1]),
            format_version=str(row[2] or VOX_FORMAT_VERSION),
            vox_type=vox_type or None,
            descriptor=loads_json(row[4]),
            payload_json=payload_json,
            value=value,
            error=row[7],
            metadata=loads_json(row[8]),
            expression=loads_json(row[9]),
            dependencies=list(dependencies_payload.get("dependencies") or []),
            created_at=float(row[11]),
            updated_at=float(row[12]),
            runtime_version=str(row[13]),
        )

    def put_success(self, node_id: str, value: Any, metadata: dict[str, Any] | None = None) -> None:
        encoded = encode_for_storage(value)
        now = time.time()
        payload_file = None
        if encoded.payload_bin is not None:
            payload_file = f"{node_id}.bin"
            (self.payload_dir / payload_file).write_bytes(encoded.payload_bin)
        metadata_json = dumps_json(dict(metadata or {}))
        with self._lock:
            row = self._connection.execute(
                "SELECT created_at, expression_json, dependencies_json FROM results WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            created_at = float(row[0]) if row is not None else now
            expression_json = row[1] if row is not None else "{}"
            dependencies_json = row[2] if row is not None else "{}"
            self._connection.execute(
                """
                INSERT INTO results (
                    node_id, status, format_version, vox_type, descriptor_json,
                    payload_json, payload_file, error, metadata_json, expression_json,
                    dependencies_json, runtime_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    status = excluded.status,
                    format_version = excluded.format_version,
                    vox_type = excluded.vox_type,
                    descriptor_json = excluded.descriptor_json,
                    payload_json = excluded.payload_json,
                    payload_file = excluded.payload_file,
                    error = NULL,
                    metadata_json = excluded.metadata_json,
                    expression_json = excluded.expression_json,
                    dependencies_json = excluded.dependencies_json,
                    runtime_version = excluded.runtime_version,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    MATERIALIZED_STATUS,
                    encoded.format_version,
                    encoded.vox_type,
                    dumps_json(encoded.descriptor),
                    dumps_json(encoded.payload_json),
                    payload_file,
                    None,
                    metadata_json,
                    expression_json,
                    dependencies_json,
                    self.runtime_version,
                    created_at,
                    now,
                ),
            )

    def delete(self, node_id: str) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM results WHERE node_id = ?", (node_id,))
        payload = self.payload_dir / f"{node_id}.bin"
        if payload.exists():
            payload.unlink()

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM results")
        for payload in self.payload_dir.glob("*.bin"):
            payload.unlink()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class NoCacheStorageBackend:
    """Storage backend that records nothing and never returns hits."""

    def has(self, node_id: str) -> bool:
        return False

    def get_record(self, node_id: str) -> ResultRecord | None:
        return None

    def put_definition(self, node_id: str, node: NodeSpec) -> None:
        return None

    def put_plan_definitions(self, plan: SymbolicPlan) -> None:
        return None

    def put_success(self, node_id: str, value: Any, metadata: dict[str, Any] | None = None) -> None:
        return None

    def delete(self, node_id: str) -> None:
        return None

    def clear(self) -> None:
        return None

    def close(self) -> None:
        return None


StorageBackend = SQLiteResultsDatabase
ResultsDatabase = Union[SQLiteResultsDatabase, NoCacheStorageBackend]
_storage_instance: SQLiteResultsDatabase | None = None


def get_storage(db_path: str | Path | None = None) -> SQLiteResultsDatabase:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = SQLiteResultsDatabase(db_path=db_path)
    return _storage_instance


def set_storage(storage: SQLiteResultsDatabase | None) -> None:
    global _storage_instance
    _storage_instance = storage


def close_storage() -> None:
    global _storage_instance
    if _storage_instance is not None:
        _storage_instance.close()
        _storage_instance = None


@dataclass
class MaterializationRecord:
    status: str
    expression: Any = None
    dependencies: list[str] = field(default_factory=list)
    value: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    format_version: str = ""
    vox_type: str = ""

# class DefinitionStore:
#     """Immutable symbolic definition store."""
# 
#     def __init__(self, definitions: dict[str, NodeSpec] | None = None):
#         self._definitions = dict(definitions or {})
# 
#     def get(self, node_id: str) -> NodeSpec:
#         return self._definitions[node_id]
# 
#     def items(self):
#         return self._definitions.items()


class MaterializationStore:
    """Runtime store with optional read/write-through persistence."""

    def __init__(self, backend: ResultsDatabase | None = None, *, read_through: bool = True, write_through: bool = True):
        self._records: dict[str, MaterializationRecord] = {}
        self._backend = backend
        self._read_through = read_through
        self._write_through = write_through
        self._lock = threading.RLock()
        self._persist_queue: queue.Queue[tuple[str, Any, dict[str, Any]]] = queue.Queue()
        self._persist_stop = threading.Event()
        self._persist_thread: threading.Thread | None = None
        if self._backend is not None and self._write_through:
            self._persist_thread = threading.Thread(target=self._persistence_loop, name="voxlogica-persist", daemon=True)
            self._persist_thread.start()

    def _materialize_from_backend(self, node_id: str) -> MaterializationRecord | None:
        if not self._read_through or self._backend is None:
            return None
        record = self._backend.get_record(node_id)
        if record is None or record.status != MATERIALIZED_STATUS:
            return None
        materialized = MaterializationRecord(
            status=MATERIALIZED_STATUS,
            expression=record.expression,
            dependencies=record.dependencies,
            value=record.value,
            metadata={**record.metadata, "source": "results-db", "cache_hit": True},
            format_version=record.format_version,
            vox_type=record.vox_type,
        )
        self._records[node_id] = materialized
        return materialized

    def has(self, node_id: str) -> bool:
        with self._lock:
            record = self._records.get(node_id)
            try:
                if record is not None and record.status == MATERIALIZED_STATUS and record.metadata["persisted"] == True:
                    return True
            except KeyError:
                if record is not None and record.status == MATERIALIZED_STATUS:
                    return True
            loaded = self._materialize_from_backend(node_id)
            return loaded is not None and loaded.status == MATERIALIZED_STATUS

    def get(self, node_id: str) -> Any:
        with self._lock:
            record = self._records.get(node_id)
            if record is not None and record.value == node_id:
                if self._backend is not None:
                    backend_record = self._backend.get_record(node_id)
                    if backend_record is not None and backend_record.status == MATERIALIZED_STATUS:
                        return backend_record.value
                raise KeyError(f"No materialized record for node {node_id}")
            if record is None or record.status != MATERIALIZED_STATUS:
                raise KeyError(f"No materialized record for node {node_id}")
            return record.value

    def put(self, node_id: str, expression: Any, dependencies: list[str], value: Any, metadata: dict[str, Any] | None = None) -> None:
        with self._lock:
            record_metadata = dict(metadata or {})
            val,reason,encoded = can_serialize_value(value)
            format_version = encoded.format_version if encoded is not None else ""
            vox_type = encoded.vox_type if encoded is not None else ""     
            if vox_type == "bytes" or vox_type == "overlay" or vox_type == "ndarray":
                stored_value = node_id
                # self._backend.put_success(node_id, value, metadata=record_metadata) if self._backend is not None else None
            else:
                stored_value = value
            self._records[node_id] = MaterializationRecord(MATERIALIZED_STATUS, expression, dependencies, stored_value, record_metadata, format_version=format_version, vox_type=vox_type)
            #if vox_type == "bytes" or vox_type == "ndarray":
            #    return
            if self._backend is None or not self._write_through:
                return
            if not val:
                self._records[node_id].metadata["persisted"] = False
                self._records[node_id].metadata["persist_error"] = reason
                return
            self._records[node_id].metadata["persisted"] = "pending"
            self._persist_queue.put((node_id, value, record_metadata))

    # def fail(self, node_id: str, message: str) -> None:
    #     with self._lock:
    #         self._records[node_id] = MaterializationRecord("failed", None, {"error": str(message)})

    def metadata(self, node_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(node_id)
            return dict(record.metadata) if record is not None else {}

    @property
    def completed_nodes(self) -> set[str]:
        with self._lock:
            return {node_id for node_id, record in self._records.items() if record.status == MATERIALIZED_STATUS}

    def flush(self, timeout_s: float = 10.0) -> bool:
        deadline = time.time() + max(0.0, timeout_s)
        while time.time() < deadline:
            if self._persist_queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._persist_queue.unfinished_tasks == 0

    def close(self) -> None:
        self.flush(timeout_s=2.0)
        self._persist_stop.set()
        if self._persist_thread is not None:
            self._persist_thread.join(timeout=1.0)

    def _persistence_loop(self) -> None:
        while not self._persist_stop.is_set() or self._persist_queue.unfinished_tasks > 0:
            try:
                node_id, value, metadata = self._persist_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self._backend is not None:
                    self._backend.put_success(node_id, value, metadata=metadata)
                with self._lock:
                    record = self._records.get(node_id)
                    if record is not None:
                        record.metadata["persisted"] = True
                        record.metadata.pop("persist_error", None)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Async persistence failed for node %s", node_id)
                with self._lock:
                    record = self._records.get(node_id)
                    if record is not None:
                        record.metadata["persisted"] = False
                        record.metadata["persist_error"] = str(exc)
            finally:
                self._persist_queue.task_done()
