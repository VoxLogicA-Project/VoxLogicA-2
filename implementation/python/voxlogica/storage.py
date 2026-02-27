"""Storage contracts for symbolic definitions, runtime materialization, and result persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union
import json
import logging
import queue
import sqlite3
import threading
import time

from voxlogica.pod_codec import (
    EncodedPage,
    EncodedRecord,
    can_serialize_value,
    decode_page_payload,
    decode_runtime_value,
    dumps_json,
    encode_for_storage,
    loads_json,
)
from voxlogica.lazy.hash import hash_sequence_item
from voxlogica.value_model import VOX_FORMAT_VERSION, UnsupportedVoxValueError, VoxSequenceValue, adapt_runtime_value


MATERIALIZED_STATUS = "materialized"
FAILED_STATUS = "failed"
STORE_SCHEMA_VERSION = 1
PERSIST_PAGE_SIZE = 128
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
    descriptor: Dict[str, Any] = field(default_factory=dict)
    payload_json: Dict[str, Any] = field(default_factory=dict)
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
            row = self._connection.execute("PRAGMA user_version").fetchone()
            current_version = int(row[0]) if row else 0
            if current_version != STORE_SCHEMA_VERSION:
                logger.warning(
                    "Results DB schema version mismatch (%s != %s): recreating store.",
                    current_version,
                    STORE_SCHEMA_VERSION,
                )
                self._recreate_schema_locked()

    def _recreate_schema_locked(self) -> None:
        self._connection.execute("DROP TABLE IF EXISTS result_pages")
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
                payload_bin BLOB,
                error TEXT,
                metadata_json TEXT NOT NULL,
                runtime_version TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE result_pages (
                node_id TEXT NOT NULL,
                path TEXT NOT NULL,
                offset INTEGER NOT NULL,
                page_limit INTEGER NOT NULL,
                descriptor_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_bin BLOB,
                runtime_version TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (node_id, path, offset, page_limit, runtime_version)
            )
            """
        )
        self._connection.execute("CREATE INDEX idx_results_status ON results(status)")
        self._connection.execute("CREATE INDEX idx_results_runtime ON results(runtime_version)")
        self._connection.execute("CREATE INDEX idx_result_pages_node ON result_pages(node_id, runtime_version)")
        self._connection.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")

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
                SELECT node_id, status, format_version, vox_type, descriptor_json, payload_json,
                       payload_bin, error, metadata_json, created_at, updated_at, runtime_version
                FROM results
                WHERE node_id = ? AND runtime_version = ?
                """,
                (node_id, self.runtime_version),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        format_version = str(row[2] or VOX_FORMAT_VERSION)
        vox_type = str(row[3] or "")
        descriptor = self._decode_json_dict(row[4])
        payload_json = self._decode_json_dict(row[5])
        payload_bin = row[6]
        error = row[7]
        metadata = self._decode_json_dict(row[8])
        value = None
        if row[1] == MATERIALIZED_STATUS and vox_type:
            value = decode_runtime_value(vox_type, payload_json, payload_bin)

        return ResultRecord(
            node_id=row[0],
            status=row[1],
            format_version=format_version,
            vox_type=vox_type or None,
            descriptor=descriptor,
            payload_json=payload_json,
            value=value,
            error=error,
            metadata=metadata,
            created_at=float(row[9]),
            updated_at=float(row[10]),
            runtime_version=row[11],
        )

    def put_success(
        self,
        node_id: str,
        value: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        metadata_dict = dict(metadata or {})
        metadata_json = self._encode_metadata(metadata_dict)
        adapted = adapt_runtime_value(value)

        with self._lock:
            if isinstance(adapted, VoxSequenceValue):
                self._persist_sequence_with_refs_locked(
                    node_id=node_id,
                    sequence=adapted,
                    metadata=metadata_dict,
                    metadata_json=metadata_json,
                    now=now,
                )
                return
            encoded = encode_for_storage(value, page_size=PERSIST_PAGE_SIZE)
            self._persist_encoded_success_locked(
                node_id=node_id,
                encoded=encoded,
                metadata_json=metadata_json,
                now=now,
            )

    def _persist_sequence_with_refs_locked(
        self,
        *,
        node_id: str,
        sequence: VoxSequenceValue,
        metadata: Dict[str, Any],
        metadata_json: str,
        now: float,
    ) -> None:
        page_size = PERSIST_PAGE_SIZE
        offset = 0
        total = 0
        has_more = True
        pages: list[EncodedPage] = []

        while has_more:
            page = sequence.page(offset=offset, limit=page_size)
            page_items: list[dict[str, Any]] = []
            for local_index, item in enumerate(page.items):
                absolute_index = offset + local_index
                item_node_id = hash_sequence_item(node_id, absolute_index)
                if "_raw" in item:
                    item_value = item["_raw"]
                elif "value" in item:
                    item_value = item["value"]
                else:
                    raise UnsupportedVoxValueError(
                        item,
                        f"Sequence item {absolute_index} is missing persistence payload.",
                    )

                try:
                    child_encoded = encode_for_storage(item_value, page_size=page_size)
                except UnsupportedVoxValueError as exc:
                    raise UnsupportedVoxValueError(
                        item_value,
                        f"Sequence item {absolute_index} cannot be persisted: {exc}",
                    ) from exc

                child_metadata = dict(metadata)
                child_metadata.update(
                    {
                        "sequence_parent_node_id": str(node_id),
                        "sequence_index": int(absolute_index),
                    }
                )
                self._persist_encoded_success_locked(
                    node_id=item_node_id,
                    encoded=child_encoded,
                    metadata_json=self._encode_metadata(child_metadata),
                    now=now,
                )

                descriptor = item.get("descriptor")
                ref_payload: dict[str, Any] = {"node_id": item_node_id}
                if isinstance(descriptor, dict):
                    ref_payload["descriptor"] = descriptor
                page_items.append({"__vox_ref__": ref_payload})

            has_more = bool(page.has_more)
            pages.append(
                EncodedPage(
                    path="",
                    offset=offset,
                    limit=page.limit,
                    descriptor={
                        "vox_type": "sequence-page",
                        "format_version": VOX_FORMAT_VERSION,
                        "summary": {"offset": offset, "limit": page.limit, "count": len(page_items)},
                        "navigation": {
                            "path": "",
                            "pageable": False,
                            "can_descend": False,
                            "default_page_size": page_size,
                            "max_page_size": page_size,
                        },
                    },
                    payload_json={"items": page_items, "has_more": has_more},
                )
            )
            offset += len(page_items)
            total += len(page_items)
            if len(page_items) == 0:
                break

        root_descriptor = dict(sequence.describe(path=""))
        root_summary = root_descriptor.get("summary")
        if not isinstance(root_summary, dict):
            root_summary = {}
        root_summary = dict(root_summary)
        root_summary["length"] = total
        root_summary["page_size"] = page_size
        root_descriptor["summary"] = root_summary
        root_encoded = EncodedRecord(
            format_version=VOX_FORMAT_VERSION,
            vox_type="sequence",
            descriptor=root_descriptor,
            payload_json={"encoding": "sequence-node-refs-v1", "length": total, "page_size": page_size},
            payload_bin=None,
            pages=pages,
        )
        self._persist_encoded_success_locked(
            node_id=node_id,
            encoded=root_encoded,
            metadata_json=metadata_json,
            now=now,
        )

    def _persist_encoded_success_locked(
        self,
        *,
        node_id: str,
        encoded: EncodedRecord,
        metadata_json: str,
        now: float,
    ) -> None:
        descriptor_json = dumps_json(encoded.descriptor)
        payload_json = dumps_json(encoded.payload_json)
        self._connection.execute(
            """
            INSERT INTO results (
                node_id, status, format_version, vox_type, descriptor_json,
                payload_json, payload_bin, error, metadata_json, runtime_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                status = excluded.status,
                format_version = excluded.format_version,
                vox_type = excluded.vox_type,
                descriptor_json = excluded.descriptor_json,
                payload_json = excluded.payload_json,
                payload_bin = excluded.payload_bin,
                error = excluded.error,
                metadata_json = excluded.metadata_json,
                runtime_version = excluded.runtime_version,
                updated_at = excluded.updated_at
            """,
            (
                node_id,
                MATERIALIZED_STATUS,
                encoded.format_version,
                encoded.vox_type,
                descriptor_json,
                payload_json,
                encoded.payload_bin,
                None,
                metadata_json,
                self.runtime_version,
                now,
                now,
            ),
        )
        self._connection.execute(
            "DELETE FROM result_pages WHERE node_id = ? AND runtime_version = ?",
            (node_id, self.runtime_version),
        )
        for page in encoded.pages:
            self._connection.execute(
                """
                INSERT INTO result_pages (
                    node_id, path, offset, page_limit, descriptor_json, payload_json, payload_bin,
                    runtime_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    page.path,
                    int(page.offset),
                    int(page.limit),
                    dumps_json(page.descriptor),
                    dumps_json(page.payload_json),
                    page.payload_bin,
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
                    node_id, status, format_version, vox_type, descriptor_json,
                    payload_json, payload_bin, error, metadata_json, runtime_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    status = excluded.status,
                    format_version = excluded.format_version,
                    vox_type = excluded.vox_type,
                    descriptor_json = excluded.descriptor_json,
                    payload_json = excluded.payload_json,
                    payload_bin = excluded.payload_bin,
                    error = excluded.error,
                    metadata_json = excluded.metadata_json,
                    runtime_version = excluded.runtime_version,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    FAILED_STATUS,
                    VOX_FORMAT_VERSION,
                    "error",
                    dumps_json(
                        {
                            "vox_type": "error",
                            "format_version": VOX_FORMAT_VERSION,
                            "summary": {"message": str(error)},
                            "navigation": {
                                "path": "",
                                "pageable": False,
                                "can_descend": False,
                                "default_page_size": 64,
                                "max_page_size": 512,
                            },
                        }
                    ),
                    dumps_json({"encoding": "none"}),
                    None,
                    str(error),
                    metadata_json,
                    self.runtime_version,
                    now,
                    now,
                ),
            )
            self._connection.execute(
                "DELETE FROM result_pages WHERE node_id = ? AND runtime_version = ?",
                (node_id, self.runtime_version),
            )

    def delete(self, node_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM results WHERE node_id = ?", (node_id,)
            )
            self._connection.execute(
                "DELETE FROM result_pages WHERE node_id = ?",
                (node_id,),
            )

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM results")
            self._connection.execute("DELETE FROM result_pages")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _encode_metadata(self, metadata: Optional[Dict[str, Any]]) -> str:
        return self._encode_json(metadata or {})

    def _encode_json(self, value: Dict[str, Any]) -> str:
        try:
            return dumps_json(value)
        except Exception:
            return "{}"

    def _decode_metadata(self, metadata_json: str) -> Dict[str, Any]:
        return self._decode_json_dict(metadata_json)

    def _decode_json_dict(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return dict(payload)
        if not isinstance(payload, str):
            return {}
        try:
            return loads_json(payload)
        except Exception:
            return {}

    def get_page_record(self, node_id: str, path: str, offset: int, limit: int) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT descriptor_json, payload_json, payload_bin
                FROM result_pages
                WHERE node_id = ? AND path = ? AND offset = ? AND page_limit = ? AND runtime_version = ?
                LIMIT 1
                """,
                (node_id, path, int(offset), int(limit), self.runtime_version),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        descriptor = self._decode_json_dict(row[0])
        payload_json = self._decode_json_dict(row[1])
        decoded = decode_page_payload(payload_json)
        return {
            "descriptor": descriptor,
            "items": decoded["items"],
            "has_more": bool(decoded["has_more"]),
            "offset": int(offset),
            "limit": int(limit),
        }

    def get_page_containing_index(self, node_id: str, path: str, index: int) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT offset, page_limit, descriptor_json, payload_json
                FROM result_pages
                WHERE node_id = ? AND path = ? AND runtime_version = ?
                  AND offset <= ?
                ORDER BY offset DESC
                LIMIT 1
                """,
                (node_id, path, self.runtime_version, int(index)),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        offset = int(row[0])
        limit = int(row[1])
        descriptor = self._decode_json_dict(row[2])
        payload_json = self._decode_json_dict(row[3])
        decoded = decode_page_payload(payload_json)
        items = decoded["items"] if isinstance(decoded, dict) else []
        if index >= offset + len(items):
            return None
        return {
            "descriptor": descriptor,
            "items": items,
            "has_more": bool(decoded["has_more"]),
            "offset": offset,
            "limit": limit,
        }


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
            descriptor: dict[str, Any]
            vox_type: str | None
            try:
                descriptor = adapt_runtime_value(value).describe(path="")
                vox_type = str(descriptor.get("vox_type", "")) or None
            except UnsupportedVoxValueError:
                descriptor = {
                    "vox_type": "unavailable",
                    "format_version": VOX_FORMAT_VERSION,
                    "summary": {"reason": "E_UNSPECIFIED_VALUE_TYPE"},
                    "navigation": {
                        "path": "",
                        "pageable": False,
                        "can_descend": False,
                        "default_page_size": 64,
                        "max_page_size": 512,
                    },
                }
                vox_type = None
            self._records[node_id] = ResultRecord(
                node_id=node_id,
                status=MATERIALIZED_STATUS,
                format_version=VOX_FORMAT_VERSION,
                vox_type=vox_type,
                descriptor=descriptor,
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
                format_version=VOX_FORMAT_VERSION,
                vox_type="error",
                descriptor={
                    "vox_type": "error",
                    "format_version": VOX_FORMAT_VERSION,
                    "summary": {"message": str(error)},
                    "navigation": {
                        "path": "",
                        "pageable": False,
                        "can_descend": False,
                        "default_page_size": 64,
                        "max_page_size": 512,
                    },
                },
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
        self._persist_queue: queue.Queue[tuple[str, str, Any, Dict[str, Any]]] = queue.Queue(maxsize=4096)
        self._persist_stop = threading.Event()
        self._persist_thread: threading.Thread | None = None
        if self._write_through and self._backend is not None:
            self._persist_thread = threading.Thread(
                target=self._persistence_loop,
                name="voxlogica-persist",
                daemon=True,
            )
            self._persist_thread.start()

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
                supported, reason = can_serialize_value(value)
                if not supported:
                    self._records[node_id].metadata["persisted"] = False
                    self._records[node_id].metadata["persist_warning"] = {
                        "code": "E_UNSPECIFIED_VALUE_TYPE",
                        "message": reason or "Value is not persistable under voxpod/1.",
                    }
                    return
                self._records[node_id].metadata["persisted"] = "pending"
                self._enqueue_persist("success", node_id, value, record_metadata)

    def fail(self, node_id: str, message: str) -> None:
        with self._lock:
            self._records[node_id] = MaterializationRecord(
                status=FAILED_STATUS,
                value=None,
                metadata={"error": message},
            )

            if self._write_through and self._backend is not None:
                self._enqueue_persist("failure", node_id, message, {"error": message})

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

    def flush(self, timeout_s: float = 10.0) -> bool:
        """Wait for async persistence queue to drain."""
        deadline = time.time() + max(0.0, float(timeout_s))
        while time.time() < deadline:
            if self._persist_queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._persist_queue.unfinished_tasks == 0

    def close(self) -> None:
        self.flush(timeout_s=2.0)
        self._persist_stop.set()
        thread = self._persist_thread
        if thread is not None:
            thread.join(timeout=1.0)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _enqueue_persist(self, kind: str, node_id: str, payload: Any, metadata: Dict[str, Any]) -> None:
        try:
            self._persist_queue.put_nowait((kind, node_id, payload, dict(metadata)))
        except queue.Full:
            with self._lock:
                record = self._records.get(node_id)
                if record is not None:
                    record.metadata["persisted"] = False
                    record.metadata["persist_error"] = "Persistence queue is full."

    def _persistence_loop(self) -> None:
        while not self._persist_stop.is_set() or self._persist_queue.unfinished_tasks > 0:
            try:
                kind, node_id, payload, metadata = self._persist_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self._backend is None:
                    continue
                if kind == "success":
                    self._backend.put_success(node_id, payload, metadata=metadata)
                else:
                    self._backend.put_failure(node_id, str(payload), metadata=metadata)
                with self._lock:
                    record = self._records.get(node_id)
                    if record is not None:
                        record.metadata["persisted"] = True
                        record.metadata.pop("persist_error", None)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    record = self._records.get(node_id)
                    if record is not None:
                        record.metadata["persisted"] = False
                        record.metadata["persist_error"] = str(exc)
            finally:
                self._persist_queue.task_done()
