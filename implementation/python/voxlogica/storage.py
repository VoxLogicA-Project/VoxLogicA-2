"""Persistent definition and materialization stores for DAG execution."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union
from abc import ABC, abstractmethod
import logging
import os
import queue
import shutil
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
STORE_SCHEMA_VERSION = 4
# The persistent result store is a bounded cache: once its payloads exceed this
# many bytes, entries are evicted (rows + payload files deleted) until back under
# budget. Eviction follows GreedyDual-Size: the eviction key is
# ``clock + compute_ms / bytes``, so a small value that was expensive to compute
# (a precious, hard-won result) outranks a large cheap one and is kept — size is
# the denominator, compute effort the numerator, recency the clock. Values are
# regenerable from lineage, so an evicted entry only ever costs a recompute.
# 0 disables the bound (unbounded).
DEFAULT_CACHE_MAX_BYTES = 100 * 1024 ** 3
# Maximum number of value-bearing entries kept in the in-memory cache tier.
# Overridable via VOXLOGICA_MEMORY_CACHE_CAPACITY for memory-heavy runs.
DEFAULT_MEMORY_CACHE_CAPACITY = 1024
# Maximum number of results queued for async persistence before producers block.
# Bounds peak memory: each queued result pins its (possibly large) value until
# the persistence thread writes it. Overridable via VOXLOGICA_PERSIST_QUEUE_MAX.
DEFAULT_PERSIST_QUEUE_MAX = 64
_RESULTS_TABLE_COLUMNS = frozenset(
    {
        "node_id",
        "status",
        "format_version",
        "vox_type",
        "descriptor_json",
        "payload_json",
        "payload_file",
        "error",
        "metadata_json",
        "expression_json",
        "dependencies_json",
        "runtime_version",
        "created_at",
        "updated_at",
        "accessed_at",
        "payload_bytes",
        "compute_ms",
        "gd_key",
    }
)
logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    base = Path.home() / ".voxlogica"
    base.mkdir(parents=True, exist_ok=True)
    return base / "results.db"


def _default_memory_capacity() -> int:
    raw = os.environ.get("VOXLOGICA_MEMORY_CACHE_CAPACITY")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return DEFAULT_MEMORY_CACHE_CAPACITY


def _default_persist_queue_max() -> int:
    raw = os.environ.get("VOXLOGICA_PERSIST_QUEUE_MAX")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return DEFAULT_PERSIST_QUEUE_MAX


def results_store_paths(db_path: str | Path | None = None) -> tuple[Path, Path]:
    """Return the SQLite database path and sibling payload directory."""
    resolved = Path(db_path) if db_path is not None else _default_db_path()
    payload_dir = resolved.with_suffix(resolved.suffix + ".files")
    return resolved, payload_dir


def delete_results_store(db_path: str | Path | None = None) -> tuple[Path, Path]:
    """Delete the results database, WAL sidecars, and payload directory."""
    db_file, payload_dir = results_store_paths(db_path)
    for candidate in (db_file, Path(f"{db_file}-wal"), Path(f"{db_file}-shm")):
        if candidate.exists():
            candidate.unlink()
    if payload_dir.exists():
        shutil.rmtree(payload_dir)
    return db_file, payload_dir


@dataclass(frozen=True)
class ResultRecord:
    """Persistent result database record."""

    node_id: str
    status: str
    payload_bin: bytes
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


class StorageBackend(ABC):
    """Storage backend that records nothing and never returns hits."""

    @abstractmethod
    def has(self, node_id: str) -> bool:
        pass

    @abstractmethod
    def get_record(self, node_id: str) -> ResultRecord | None:
        pass

    @abstractmethod
    def put_definition(self, node_id: str, node: NodeSpec) -> None:
        pass

    @abstractmethod
    def put_plan_definitions(self, plan: SymbolicPlan) -> None:
        pass

    @abstractmethod
    def put_success(self, node_id: str, value: Any, metadata: dict[str, Any] | None = None,
                    compute_ms: float = 0.0) -> None:
        pass

    @abstractmethod
    def delete(self, node_id: str) -> None:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

class SQLiteResultsDatabase:
    """SQLite plus payload-file storage keyed by stable node hashes."""

    def __init__(self, db_path: str | Path | None = None, runtime_version: str | None = None,
                 max_bytes: int | None = None):
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.payload_dir = self.db_path.with_suffix(self.db_path.suffix + ".files")
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_version = runtime_version or "unknown"
        self._max_bytes = DEFAULT_CACHE_MAX_BYTES if max_bytes is None else max_bytes
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None, timeout=5.0)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._initialize_schema()
        # Running total of stored payload bytes, so the byte budget can be
        # enforced without restatting the payload directory on every write.
        self._payload_bytes = int(
            (self._connection.execute("SELECT COALESCE(SUM(payload_bytes), 0) FROM results").fetchone() or [0])[0]
        )
        # GreedyDual clock: rises to the key of the last evicted entry, folding
        # recency into the cost/size eviction key. Seed at the lowest live key so
        # entries written before this process are comparable.
        self._gd_clock = float(
            (self._connection.execute("SELECT COALESCE(MIN(gd_key), 0.0) FROM results WHERE payload_bytes > 0").fetchone() or [0.0])[0]
        )
        self._stats = {"writes": 0, "evictions": 0, "evicted_bytes": 0, "hits": 0}

    def _results_table_matches_schema(self) -> bool:
        rows = self._connection.execute("PRAGMA table_info(results)").fetchall()
        if not rows:
            return False
        return {str(row[1]) for row in rows} == _RESULTS_TABLE_COLUMNS

    def _initialize_schema(self) -> None:
        with self._lock:
            version = int((self._connection.execute("PRAGMA user_version").fetchone() or [0])[0])
            if version != STORE_SCHEMA_VERSION or not self._results_table_matches_schema():
                self._connection.execute("DROP TABLE IF EXISTS results")
                # A schema reset abandons every old payload file; clear them so
                # they don't linger uncounted against the byte budget.
                if self.payload_dir.exists():
                    shutil.rmtree(self.payload_dir)
                self.payload_dir.mkdir(parents=True, exist_ok=True)
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
                        updated_at REAL NOT NULL,
                        accessed_at REAL NOT NULL,
                        payload_bytes INTEGER NOT NULL DEFAULT 0,
                        compute_ms REAL NOT NULL DEFAULT 0,
                        gd_key REAL NOT NULL DEFAULT 0
                    )
                    """
                )
                self._connection.execute("CREATE INDEX idx_results_status ON results(status)")
                # Eviction scans by GreedyDual key among rows that hold payload bytes.
                self._connection.execute("CREATE INDEX idx_results_evict ON results(gd_key) WHERE payload_bytes > 0")
                self._connection.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")

    def has(self, node_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM results WHERE node_id = ? AND status = ? LIMIT 1",
                (node_id, MATERIALIZED_STATUS),
            ).fetchone()
            return row is not None

    #def put_definition(self, node_id: str, node: NodeSpec) -> None:
    #    expression = node_payload(node)
    #    dependencies = list(node.args) + [value for _key, value in node.kwargs]
    #    now = time.time()
    #    with self._lock:
    #        row = self._connection.execute("SELECT status, created_at FROM results WHERE node_id = ?", (node_id,)).fetchone()
    #        if row is not None and str(row[0]) == MATERIALIZED_STATUS:
    #            return
    #        created_at = float(row[1]) if row is not None else now
    #        self._connection.execute(
    #            """
    #            INSERT INTO results (
    #                node_id, status, format_version, vox_type, descriptor_json,
    #                payload_json, payload_file, error, metadata_json, expression_json,
    #                dependencies_json, runtime_version, created_at, updated_at
    #            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    #            ON CONFLICT(node_id) DO UPDATE SET
    #                status = CASE WHEN results.status = ? THEN results.status ELSE excluded.status END,
    #                expression_json = excluded.expression_json,
    #                dependencies_json = excluded.dependencies_json,
    #                updated_at = excluded.updated_at
    #            """,
    #            (
    #                node_id,
    #                PLANNED_STATUS,
    #                VOX_FORMAT_VERSION,
    #                None,
    #                "{}",
    #                "{}",
    #                None,
    #                None,
    #                "{}",
    #                dumps_json(expression),
    #                dumps_json({"dependencies": dependencies}),
    #                self.runtime_version,
    #                created_at,
    #                now,
    #                MATERIALIZED_STATUS,
    #            ),
    #        )

    #def put_plan_definitions(self, plan: SymbolicPlan) -> None:
    #    for node_id, node in plan.nodes.items():
    #        self.put_definition(node_id, node)

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
        if str(row[1]) == MATERIALIZED_STATUS:
            # A load is a genuine reuse — refresh recency and re-seat the
            # GreedyDual key at the current clock so reused values are kept.
            with self._lock:
                self._stats["hits"] += 1
                self._connection.execute(
                    "UPDATE results SET accessed_at = ?, "
                    "gd_key = ? + CASE WHEN payload_bytes > 0 THEN compute_ms / payload_bytes ELSE 0 END "
                    "WHERE node_id = ?",
                    (time.time(), self._gd_clock, node_id),
                )
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
            payload_bin=payload_bin,
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

    def put_success(self, node_id: str, value: Any, metadata: dict[str, Any] | None = None,
                    compute_ms: float = 0.0) -> None:
        encoded = encode_for_storage(value)
        now = time.time()
        payload_file = None
        payload_bytes = 0
        if encoded.payload_bin is not None:
            payload_file = f"{node_id}.bin"
            (self.payload_dir / payload_file).write_bytes(encoded.payload_bin)
            payload_bytes = len(encoded.payload_bin)
        metadata_json = dumps_json(dict(metadata or {}))
        # GreedyDual-Size key: recency clock + recompute cost per byte. Small +
        # expensive ranks highest (kept longest); large + cheap ranks lowest.
        gd_key = self._gd_clock + (compute_ms / payload_bytes if payload_bytes else 0.0)
        with self._lock:
            row = self._connection.execute(
                "SELECT created_at, expression_json, dependencies_json, payload_bytes FROM results WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            created_at = float(row[0]) if row is not None else now
            expression_json = row[1] if row is not None else "{}"
            dependencies_json = row[2] if row is not None else "{}"
            previous_bytes = int(row[3]) if row is not None else 0
            self._connection.execute(
                """
                INSERT INTO results (
                    node_id, status, format_version, vox_type, descriptor_json,
                    payload_json, payload_file, error, metadata_json, expression_json,
                    dependencies_json, runtime_version, created_at, updated_at,
                    accessed_at, payload_bytes, compute_ms, gd_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at = excluded.updated_at,
                    accessed_at = excluded.accessed_at,
                    payload_bytes = excluded.payload_bytes,
                    compute_ms = excluded.compute_ms,
                    gd_key = excluded.gd_key
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
                    now,
                    payload_bytes,
                    float(compute_ms),
                    gd_key,
                ),
            )
            self._payload_bytes += payload_bytes - previous_bytes
            self._stats["writes"] += 1
        self._enforce_budget()

    def _enforce_budget(self) -> None:
        """Evict payloads by GreedyDual-Size key until back under the byte budget.

        Runs on the async persister thread (off the event loop). Only rows that
        hold payload bytes are candidates, evicted in ascending ``gd_key`` order
        (cheapest-to-recompute-per-byte first) — so a small, expensively-computed
        value is kept while a large, cheap intermediate goes first. The clock
        rises to each evicted key, folding recency into future keys. Evicted
        values remain regenerable from lineage.
        """
        if self._max_bytes <= 0 or self._payload_bytes <= self._max_bytes:
            return
        low_water = int(self._max_bytes * 0.9)
        with self._lock:
            while self._payload_bytes > low_water:
                rows = self._connection.execute(
                    "SELECT node_id, payload_file, payload_bytes, gd_key FROM results "
                    "WHERE payload_bytes > 0 ORDER BY gd_key ASC LIMIT 128"
                ).fetchall()
                if not rows:
                    break
                for node_id, payload_file, nbytes, gd_key in rows:
                    if self._payload_bytes <= low_water:
                        break
                    if payload_file:
                        (self.payload_dir / str(payload_file)).unlink(missing_ok=True)
                    self._connection.execute("DELETE FROM results WHERE node_id = ?", (node_id,))
                    self._payload_bytes -= int(nbytes or 0)
                    self._gd_clock = max(self._gd_clock, float(gd_key))
                    self._stats["evictions"] += 1
                    self._stats["evicted_bytes"] += int(nbytes or 0)

    def stats(self) -> dict[str, Any]:
        """Cache statistics: live entries/bytes, cumulative work banked, activity."""
        with self._lock:
            entries, payload_rows, total_ms = self._connection.execute(
                "SELECT COUNT(*), COUNT(payload_file), COALESCE(SUM(compute_ms), 0) FROM results"
            ).fetchone()
            top = self._connection.execute(
                "SELECT node_id, compute_ms, payload_bytes FROM results "
                "WHERE payload_bytes > 0 ORDER BY compute_ms DESC LIMIT 5"
            ).fetchall()
        return {
            "entries": int(entries),
            "payload_entries": int(payload_rows),
            "payload_bytes": self._payload_bytes,
            "max_bytes": self._max_bytes,
            "compute_ms_banked": float(total_ms),
            **dict(self._stats),
            "most_expensive": [{"node": n[:12], "compute_ms": round(c, 1), "bytes": b} for n, c, b in top],
        }

    def delete(self, node_id: str) -> None:
        with self._lock:
            row = self._connection.execute("SELECT payload_bytes FROM results WHERE node_id = ?", (node_id,)).fetchone()
            self._connection.execute("DELETE FROM results WHERE node_id = ?", (node_id,))
            if row is not None:
                self._payload_bytes -= int(row[0] or 0)
        payload = self.payload_dir / f"{node_id}.bin"
        if payload.exists():
            payload.unlink()

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM results")
            self._payload_bytes = 0
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

    def put_success(self, node_id: str, value: Any, metadata: dict[str, Any] | None = None,
                    compute_ms: float = 0.0) -> None:
        return None

    def delete(self, node_id: str) -> None:
        return None

    def clear(self) -> None:
        return None

    def close(self) -> None:
        return None


_storage_instance: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage_instance
    return _storage_instance


def set_storage(storage: StorageBackend | None) -> None:
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
    """Two-level (memory + disk) result store with optional persistence.

    Tier 1 is a bounded, LRU in-memory cache (``_memory``) holding the live
    Python values. Tier 2 is the optional ``backend`` (disk). Bookkeeping
    records in ``_records`` are kept for every materialized node so ``has`` and
    ``completed_nodes`` stay correct even after a value is evicted from RAM.

    A value is evicted from the memory tier only once it is durably persisted to
    disk, so a node is never recomputed: an evicted value is reloaded from
    tier 2 on demand. With no backend (e.g. ``--no-cache``) nothing is
    persisted, hence nothing is evicted and the memory tier acts as an
    unbounded per-run memo.
    """

    def __init__(
        self,
        backend: StorageBackend | None = None,
        *,
        read_through: bool = True,
        write_through: bool = True,
        memory_capacity: int | None = None,
    ):
        self._records: "OrderedDict[str, MaterializationRecord]" = OrderedDict()
        self._memory: "OrderedDict[str, Any]" = OrderedDict()
        self._memory_capacity = memory_capacity if memory_capacity is not None else _default_memory_capacity()
        self._backend = backend
        self._read_through = read_through
        self._write_through = write_through
        self._lock = threading.RLock()
        self._persist_queue: queue.Queue[tuple[str, Any, dict[str, Any]]] = queue.Queue(
            maxsize=_default_persist_queue_max()
        )
        self._persist_stop = threading.Event()
        self._persist_thread: threading.Thread | None = None
        if self._backend is not None and self._write_through:
            self._persist_thread = threading.Thread(target=self._persistence_loop, name="voxlogica-persist", daemon=True)
            self._persist_thread.start()

    def _remember(self, node_id: str, value: Any) -> None:
        """Insert a value into the in-memory tier and trim to capacity."""
        self._memory[node_id] = value
        self._memory.move_to_end(node_id)
        self._trim()

    def _trim(self) -> None:
        """Evict least-recently-used values that are safely reloadable from disk."""
        while len(self._memory) > self._memory_capacity:
            oldest_id = next(iter(self._memory))
            record = self._records.get(oldest_id)
            # Only drop a value we can reload from tier 2; otherwise the node
            # would have to be recomputed. If the LRU victim is not yet durable,
            # stop trimming and let the memory tier exceed capacity for now.
            if not (self._read_through and record is not None and record.metadata.get("persisted") is True):
                break
            self._memory.pop(oldest_id, None)

    def _load_from_backend(self, node_id: str) -> Any:
        """Return a persisted value from tier 2 and refresh bookkeeping; None on miss."""
        if not self._read_through or self._backend is None:
            return None
        record = self._backend.get_record(node_id)
        if record is None or record.status != MATERIALIZED_STATUS:
            return None
        self._records[node_id] = MaterializationRecord(
            status=MATERIALIZED_STATUS,
            expression=record.expression,
            dependencies=record.dependencies,
            value=None,
            metadata={**record.metadata, "source": "results-db", "cache_hit": True, "persisted": True},
            format_version=record.format_version,
            vox_type=record.vox_type,
        )
        self._records.move_to_end(node_id)
        return record.value

    def has(self, node_id: str) -> bool:
        with self._lock:
            if node_id in self._memory:
                return True
            record = self._records.get(node_id)
            if record is not None and record.status == MATERIALIZED_STATUS and record.metadata.get("persisted") is True:
                return True
            return self._load_from_backend(node_id) is not None

    def get(self, node_id: str) -> Any:
        with self._lock:
            if node_id in self._memory:
                self._memory.move_to_end(node_id)  # tier-1 hit
                return self._memory[node_id]
            value = self._load_from_backend(node_id)  # tier-2 fallback
            if value is not None:
                self._remember(node_id, value)
            return value

    def put(self, node_id: str, expression: Any, dependencies: list[str], value: Any, metadata: dict[str, Any] | None = None) -> None:
        enqueue: tuple[str, Any, dict[str, Any]] | None = None
        with self._lock:
            record_metadata = dict(metadata or {})
            val,reason,encoded = can_serialize_value(value)
            format_version = encoded.format_version if encoded is not None else ""
            vox_type = encoded.vox_type if encoded is not None else ""
            # The bookkeeping record holds no value; the live value lives in the
            # bounded in-memory tier (and, once persisted, on disk).
            self._records[node_id] = MaterializationRecord(MATERIALIZED_STATUS, expression, dependencies, None, record_metadata, format_version=format_version, vox_type=vox_type)
            self._records.move_to_end(node_id)
            if self._backend is None or not self._write_through:
                self._remember(node_id, value)
                return
            if not val:
                record_metadata["persisted"] = False
                record_metadata["persist_error"] = reason
                self._remember(node_id, value)
                return
            record_metadata["persisted"] = "pending"
            self._remember(node_id, value)
            enqueue = (node_id, value, record_metadata)
        # Enqueue outside the lock: the bounded queue blocks the producer when
        # full (backpressure that bounds memory), and the persistence thread
        # needs the lock to mark items done — holding it here would deadlock.
        if enqueue is not None:
            self._persist_queue.put(enqueue)

    def forget(self, node_id: str) -> None:
        """Drop a value from the in-memory tier once the caller no longer needs it.

        Used by the executor to release intermediates whose every consumer has
        run. The bookkeeping record is retained (so completed_nodes stays
        correct); a persisted value remains reloadable from disk, and an
        un-persisted one is simply recomputed if a later run needs it.
        """
        with self._lock:
            self._memory.pop(node_id, None)

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
