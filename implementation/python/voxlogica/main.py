"""VoxLogicA CLI and API entrypoints."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any, Optional, TypeVar, Generic, MutableMapping, Callable

import dask
import typer
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from voxlogica.features import FeatureRegistry, OperationResult, handle_list_primitives
from voxlogica.lazy.hash import hash_sequence_item
from voxlogica.parser import parse_program_content
from voxlogica.policy import (
    diagnostics_payload,
    diagnostics_from_exception,
    enforce_workplan_policy_or_raise,
    validate_workplan_policy,
)
from voxlogica.priority import compute_priority_context
from voxlogica.reducer import reduce_program_with_bindings
from voxlogica.repl import run_interactive_repl
from voxlogica.sequence_identity import resolve_sequence_container_node, resolve_sequence_reference, stored_sequence_child_node_id
from voxlogica.serve_support import (
    PERF_REPORT_SVG,
    PERF_REPORT_DIR,
    PlaygroundJobManager,
    TestingJobManager,
    build_test_dashboard_snapshot,
    describe_runtime_value,
    get_lightweight_storage_stats_snapshot,
    inspect_store_result,
    inspect_store_result_page,
    list_playground_programs,
    list_store_results_snapshot,
    load_gallery_document,
    load_playground_program,
    render_store_result_nifti,
    render_store_result_nifti_gz,
    render_store_result_png,
)
from voxlogica.storage import get_storage
from voxlogica.value_model import UnsupportedVoxValueError, VoxValueError, adapt_runtime_value, normalize_path
from voxlogica.version import get_version
from voxlogica.converters.json_converter import WorkPlanJSONEncoder


T = TypeVar("T")
logger = logging.getLogger("voxlogica.main")
_MAIN_LOG_ENV = "VOXLOGICA_MAIN_LOG_PATH"
_DEFAULT_MAIN_LOG_RELATIVE = ("tests", "reports", "serve", "voxlogica-main.log")


def _diagnostic_http_detail(exc: Exception, fallback: str) -> dict[str, Any]:
    diagnostics = diagnostics_from_exception(exc)
    message = diagnostics[0]["message"] if diagnostics else str(fallback or exc or "Request failed")
    return {
        "message": str(message),
        "diagnostics": diagnostics,
    }


class SuccessResponse(BaseModel, Generic[T]):
    """Standard success response model."""

    success: bool = True
    data: Optional[T] = None


class ErrorResponse(BaseModel):
    """Standard error response model."""

    detail: str


class RunRequest(BaseModel):
    """API payload for `/api/v1/run`."""

    program: str
    filename: Optional[str] = None
    save_task_graph: Optional[str] = None
    save_task_graph_as_dot: Optional[str] = None
    save_task_graph_as_json: Optional[str] = None
    save_syntax: Optional[str] = None
    compute_memory_assignment: Optional[bool] = False
    execute: Optional[bool] = True
    no_cache: Optional[bool] = False
    debug: Optional[bool] = False
    verbose: Optional[bool] = False
    dask_dashboard: Optional[bool] = False
    execution_strategy: Optional[str] = "dask"
    background_fill: Optional[bool] = False


class TestRunRequest(BaseModel):
    """API payload for interactive test runs."""

    profile: str = "full"
    include_perf: bool = True


class PlaygroundSymbolsRequest(BaseModel):
    """Payload to pre-compute variable hashes for editor interactions."""

    program: str


class PlaygroundValueRequest(BaseModel):
    """Payload to lazily resolve one variable/node value for viewer interactions."""

    program: str
    execution_strategy: str = "dask"
    node_id: str | None = None
    variable: str | None = None
    path: str | None = None
    enqueue: bool = True
    ui_awaited: bool = True
    interaction: dict[str, Any] | None = None


class PlaygroundValuePageRequest(BaseModel):
    """Payload to lazily resolve one pageable value slice for viewer interactions."""

    program: str
    execution_strategy: str = "dask"
    node_id: str | None = None
    variable: str | None = None
    path: str | None = None
    offset: int = 0
    limit: int = 64
    enqueue: bool = True
    ui_awaited: bool = True
    interaction: dict[str, Any] | None = None


class PlaygroundGraphRequest(BaseModel):
    """Payload to fetch a symbolic compute graph for a program."""

    program: str


class ClientLogEvent(BaseModel):
    """One browser-originated log event."""

    level: str = "info"
    message: str = ""
    source: str | None = None
    url: str | None = None
    ts: str | None = None
    payload: dict[str, Any] | None = None
    user_agent: str | None = None


class ClientLogBatchRequest(BaseModel):
    """Batch payload for browser-originated log events."""

    events: list[ClientLogEvent]


def _validate_no_server_save(request: RunRequest) -> None:
    blocked_fields: list[str] = []
    if request.save_task_graph:
        blocked_fields.append("save_task_graph")
    if request.save_task_graph_as_dot:
        blocked_fields.append("save_task_graph_as_dot")
    if request.save_task_graph_as_json:
        blocked_fields.append("save_task_graph_as_json")
    if request.save_syntax:
        blocked_fields.append("save_syntax")
    if blocked_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Server-side save/export options are disabled in serve mode. "
                f"Blocked fields: {', '.join(blocked_fields)}"
            ),
        )


def _prepare_serve_run_payload(request: RunRequest) -> dict[str, Any]:
    _validate_no_server_save(request)
    payload = request.model_dump()
    payload["legacy"] = False
    payload["serve_mode"] = True
    payload["execution_strategy"] = "dask"
    filename = (request.filename or "").strip()
    if filename:
        try:
            loaded = load_playground_program(filename)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        payload["filename"] = loaded["absolute_path"]
    return payload


def _background_fill_goal_ids(program_text: str) -> list[str]:
    """Collect stable declaration goal ids for background materialization."""
    _workplan, symbol_table, _print_targets = _program_introspection(
        program_text,
        legacy=False,
        serve_mode=True,
        enforce_policy=True,
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for node_id in symbol_table.values():
        resolved = str(node_id or "").strip()
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def _program_introspection(
    program_text: str,
    *,
    legacy: bool = False,
    serve_mode: bool = False,
    enforce_policy: bool = True,
) -> tuple[Any, dict[str, str], list[dict[str, str]]]:
    workplan, symbol_table, print_targets = _cached_program_introspection(program_text)
    if enforce_policy:
        enforce_workplan_policy_or_raise(
            workplan,
            legacy=legacy,
            serve_mode=serve_mode,
        )
    # Return shallow copies so callers can mutate payloads without polluting cache.
    return workplan, dict(symbol_table), [dict(item) for item in print_targets]


@lru_cache(maxsize=64)
def _cached_program_introspection(program_text: str) -> tuple[Any, dict[str, str], list[dict[str, str]]]:
    syntax = parse_program_content(program_text)
    workplan, symbol_table = reduce_program_with_bindings(syntax)
    print_targets = [
        {"name": goal.name, "node_id": goal.id}
        for goal in workplan.goals
        if goal.operation == "print"
    ]
    return workplan, symbol_table, print_targets


def _program_introspection_uncached(
    program_text: str,
    *,
    legacy: bool = False,
    serve_mode: bool = False,
    enforce_policy: bool = True,
) -> tuple[Any, dict[str, str], list[dict[str, str]]]:
    """Compatibility helper retained for diagnostics/debugging paths."""
    syntax = parse_program_content(program_text)
    workplan, symbol_table = reduce_program_with_bindings(syntax)
    if enforce_policy:
        enforce_workplan_policy_or_raise(
            workplan,
            legacy=legacy,
            serve_mode=serve_mode,
        )
    print_targets = [
        {"name": goal.name, "node_id": goal.id}
        for goal in workplan.goals
        if goal.operation == "print"
    ]
    return workplan, symbol_table, print_targets


def _program_hash(program_text: str) -> str:
    return hashlib.sha1(program_text.encode("utf-8")).hexdigest()


def _resolve_requested_node(
    *,
    symbol_table: dict[str, str],
    node_id: str | None,
    variable: str | None,
    allowed_nodes: set[str] | None = None,
) -> tuple[str, str]:
    resolved_variable = (variable or "").strip()
    resolved_node = ""
    if resolved_variable:
        mapped = symbol_table.get(resolved_variable)
        if mapped:
            resolved_node = mapped
    if not resolved_node:
        resolved_node = (node_id or "").strip()
    if not resolved_node:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing node selection: provide `node_id` or a bound `variable`.",
        )
    if allowed_nodes is not None and resolved_node not in allowed_nodes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown node selection '{resolved_node[:12]}'. "
                "This hash does not belong to the current program."
            ),
        )
    return resolved_node, resolved_variable


def _path_tokens(path: str | None) -> list[str]:
    return [token for token in str(path or "").strip().split("/") if token]


def _append_sequence_index_path(base_path: str, index: int) -> str:
    base = str(base_path or "").strip()
    if base in {"", "/"}:
        return f"/{index}"
    return f"{base.rstrip('/')}/{index}"


def _pending_descriptor(*, path: str, reason: str) -> dict[str, Any]:
    return {
        "vox_type": "unavailable",
        "format_version": "voxpod/1",
        "summary": {"reason": reason},
        "navigation": {
            "path": path,
            "pageable": False,
            "can_descend": False,
            "default_page_size": 64,
            "max_page_size": 512,
        },
    }


def _in_progress_descriptor(*, output_kind: str, path: str, status: str) -> dict[str, Any]:
    normalized_path = str(path or "").strip()
    if normalized_path not in {"", "/"}:
        return _pending_descriptor(path=normalized_path, reason=f"status={status}")
    if output_kind == "sequence":
        return {
            "vox_type": "sequence",
            "format_version": "voxpod/1",
            "summary": {"length": None},
            "navigation": {
                "path": normalized_path,
                "pageable": True,
                "can_descend": True,
                "default_page_size": 64,
                "max_page_size": 512,
            },
        }
    if output_kind == "mapping":
        return {
            "vox_type": "mapping",
            "format_version": "voxpod/1",
            "summary": {"length": None},
            "navigation": {
                "path": normalized_path,
                "pageable": True,
                "can_descend": True,
                "default_page_size": 64,
                "max_page_size": 512,
            },
        }
    if output_kind == "overlay":
        return {
            "vox_type": "overlay",
            "format_version": "voxpod/1",
            "summary": {"layer_count": None},
            "navigation": {
                "path": normalized_path,
                "pageable": False,
                "can_descend": True,
                "default_page_size": 64,
                "max_page_size": 512,
            },
        }
    return _pending_descriptor(path=normalized_path, reason=f"status={status}")


def _sequence_reference_for_path(root_node_id: str, path: str | None) -> tuple[str, str] | None:
    return resolve_sequence_reference(root_node_id=str(root_node_id), path=path, storage=get_storage())


def _sequence_container_node_for_path(root_node_id: str, path: str | None) -> str | None:
    return resolve_sequence_container_node(root_node_id=str(root_node_id), path=path, storage=get_storage())


def _inspect_store_result_best_effort(
    storage: Any,
    *,
    node_id: str,
    path: str,
    lock_wait_ms: float = 0.0,
) -> tuple[dict[str, Any] | None, str]:
    """Inspect one stored value without blocking behind persistence lock contention."""
    lock = getattr(storage, "_lock", None)
    lock_acquired = False
    lock_is_rlock_like = (
        callable(getattr(lock, "_is_owned", None))
        and callable(getattr(lock, "acquire", None))
        and callable(getattr(lock, "release", None))
    )

    if lock_is_rlock_like:
        wait_ms = max(0.0, float(lock_wait_ms))
        try:
            if wait_ms <= 0.0:
                lock_acquired = bool(lock.acquire(blocking=False))
            else:
                lock_acquired = bool(lock.acquire(timeout=wait_ms / 1000.0))
        except TypeError:
            lock_acquired = bool(lock.acquire(False))
        if not lock_acquired:
            return None, "busy"

    try:
        return inspect_store_result(storage, node_id=node_id, path=path), "ok"
    except KeyError:
        return None, "missing"
    finally:
        if lock_is_rlock_like and lock_acquired:
            try:
                lock.release()
            except Exception:
                pass


def _transient_sequence_page_from_store(
    *,
    storage: Any,
    container_node_id: str,
    descriptor: dict[str, Any] | None,
    base_path: str,
    offset: int,
    limit: int,
) -> dict[str, Any] | None:
    if not isinstance(descriptor, dict):
        return None
    if str(descriptor.get("vox_type", "")) != "sequence":
        return None

    summary = descriptor.get("summary")
    summary_dict = summary if isinstance(summary, dict) else {}
    total: int | None = None
    raw_length = summary_dict.get("length")
    try:
        if raw_length is not None:
            parsed_length = int(raw_length)
            if parsed_length >= 0:
                total = parsed_length
    except Exception:
        total = None

    safe_offset = max(0, int(offset))
    safe_limit = max(1, int(limit))
    if total is not None and safe_offset >= total:
        return {
            "offset": safe_offset,
            "limit": safe_limit,
            "items": [],
            "next_offset": None,
            "has_more": False,
            "total": total,
        }

    upper_bound = safe_offset + safe_limit
    if total is not None:
        upper_bound = min(upper_bound, total)

    items_out: list[dict[str, Any]] = []
    if total is None:
        unknown_upper_bound = safe_offset + safe_limit
        for cursor in range(safe_offset, unknown_upper_bound):
            item_node_id = stored_sequence_child_node_id(storage, parent_node_id=container_node_id, index=cursor)
            if item_node_id is None:
                item_node_id = hash_sequence_item(container_node_id, cursor)
            item_path = _append_sequence_index_path(base_path, cursor)
            try:
                item_payload, _lookup_state = _inspect_store_result_best_effort(
                    storage,
                    node_id=item_node_id,
                    path="",
                    lock_wait_ms=0.0,
                )
            except UnsupportedVoxValueError:
                item_payload = None

            item_status = "pending"
            item_descriptor: dict[str, Any]
            if isinstance(item_payload, dict):
                item_status = str(item_payload.get("status", "unknown"))
                descriptor_candidate = item_payload.get("descriptor")
                if isinstance(descriptor_candidate, dict):
                    item_descriptor = descriptor_candidate
                else:
                    item_descriptor = _pending_descriptor(path=item_path, reason=f"status={item_status}")
            else:
                item_descriptor = _pending_descriptor(path=item_path, reason="status=pending")
            items_out.append(
                {
                    "index": cursor,
                    "label": f"[{cursor}]",
                    "path": item_path,
                    "descriptor": item_descriptor,
                    "node_id": item_node_id,
                    "status": item_status,
                }
            )
        has_more = True
        next_offset = safe_offset + len(items_out)
        return {
            "offset": safe_offset,
            "limit": safe_limit,
            "items": items_out,
            "next_offset": next_offset,
            "has_more": bool(has_more),
            "total": None,
        }

    for cursor in range(safe_offset, upper_bound):
        item_node_id = stored_sequence_child_node_id(storage, parent_node_id=container_node_id, index=cursor)
        if item_node_id is None:
            item_node_id = hash_sequence_item(container_node_id, cursor)
        item_path = _append_sequence_index_path(base_path, cursor)
        item_payload: dict[str, Any] | None
        try:
            item_payload, _lookup_state = _inspect_store_result_best_effort(
                storage,
                node_id=item_node_id,
                path="",
                lock_wait_ms=0.0,
            )
        except UnsupportedVoxValueError:
            item_payload = None

        item_status = "pending"
        item_descriptor: dict[str, Any]
        if isinstance(item_payload, dict):
            payload_status = str(item_payload.get("status", "pending"))
            if payload_status == "materialized":
                item_descriptor_raw = item_payload.get("descriptor")
                if isinstance(item_descriptor_raw, dict):
                    item_descriptor = item_descriptor_raw
                    item_status = "materialized"
                else:
                    item_descriptor = _pending_descriptor(path=item_path, reason="status=materialized")
                    item_status = "materialized"
            elif payload_status == "failed":
                item_descriptor_raw = item_payload.get("descriptor")
                if isinstance(item_descriptor_raw, dict):
                    item_descriptor = item_descriptor_raw
                else:
                    item_descriptor = _pending_descriptor(path=item_path, reason="status=failed")
                item_status = "failed"
            else:
                item_descriptor = _pending_descriptor(path=item_path, reason=f"status={payload_status}")
                item_status = payload_status
        else:
            item_descriptor = _pending_descriptor(path=item_path, reason="status=pending")

        items_out.append(
            {
                "index": cursor,
                "label": f"[{cursor}]",
                "path": item_path,
                "descriptor": item_descriptor,
                "node_id": item_node_id,
                "status": item_status,
            }
        )

    if total is not None:
        has_more = upper_bound < total
    else:
        has_more = len(items_out) >= safe_limit
    next_offset = safe_offset + len(items_out) if has_more else None

    return {
        "offset": safe_offset,
        "limit": safe_limit,
        "items": items_out,
        "next_offset": next_offset,
        "has_more": bool(has_more),
        "total": total,
    }


def _slice_runtime_preview_page(
    preview_page: dict[str, Any] | None,
    *,
    offset: int,
    limit: int,
) -> dict[str, Any] | None:
    """Return one requested page window from a runtime preview page payload."""
    if not isinstance(preview_page, dict):
        return None
    raw_items = preview_page.get("items")
    if not isinstance(raw_items, list):
        return None

    safe_offset = max(0, int(offset))
    safe_limit = max(1, int(limit))
    items = [item for item in raw_items if isinstance(item, dict)]

    indexed_items = [item for item in items if isinstance(item.get("index"), int)]
    if indexed_items:
        selected = [item for item in indexed_items if safe_offset <= int(item.get("index", -1)) < safe_offset + safe_limit]
        next_offset = safe_offset + len(selected) if len(selected) >= safe_limit else None
    else:
        selected = items[safe_offset : safe_offset + safe_limit]
        next_offset = safe_offset + len(selected) if (safe_offset + len(selected)) < len(items) else None

    total = preview_page.get("total")
    has_more = bool(next_offset is not None)
    return {
        "offset": safe_offset,
        "limit": safe_limit,
        "items": selected,
        "next_offset": next_offset,
        "has_more": has_more,
        "total": total if isinstance(total, int) else None,
    }


def _page_richness_tuple(page_payload: dict[str, Any] | None) -> tuple[int, int, int]:
    if not isinstance(page_payload, dict):
        return (-1, -1, -1)
    raw_items = page_payload.get("items")
    if not isinstance(raw_items, list):
        return (-1, -1, -1)

    ready_items = 0
    concrete_items = 0
    total_items = 0
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        total_items += 1
        descriptor = item.get("descriptor") if isinstance(item.get("descriptor"), dict) else {}
        vox_type = str(descriptor.get("vox_type", "")).strip().lower()
        status = str(item.get("state") or item.get("status") or "").strip().lower()
        is_concrete = vox_type not in {"", "unavailable", "error"}
        is_ready = is_concrete or status in {"ready", "materialized", "computed", "cached"}
        if is_concrete:
            concrete_items += 1
        if is_ready:
            ready_items += 1
    return (ready_items, concrete_items, total_items)


def _prefer_richer_page(*pages: dict[str, Any] | None) -> dict[str, Any] | None:
    best_page: dict[str, Any] | None = None
    best_score = (-1, -1, -1)
    for page in pages:
        score = _page_richness_tuple(page)
        if score > best_score:
            best_page = page
            best_score = score
    return best_page


def _is_terminal_value_payload(payload: dict[str, Any]) -> bool:
    materialization = str(payload.get("materialization", "missing")).lower()
    compute_status = str(payload.get("compute_status", "missing")).lower()
    status_name = str(payload.get("status", "")).lower()
    if materialization == "failed" or compute_status in {"failed", "killed"} or status_name == "failed":
        return True
    if materialization in {"cached", "computed"}:
        return True
    return False


def _payload_sequence_version(payload: dict[str, Any]) -> int | None:
    raw_version = payload.get("sequence_version")
    if raw_version is None:
        return None
    try:
        return int(raw_version)
    except Exception:
        return None


class ElapsedMsFormatter(logging.Formatter):
    """Formatter that prints elapsed milliseconds from process start."""

    def __init__(self, fmt: str | None = None, datefmt: str | None = None):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.start_time = time.monotonic()
        self.width = 8

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
        if elapsed_ms < 10**7:
            record.elapsed = f"[{elapsed_ms:>{self.width}}ms]"
        else:
            record.elapsed = f"[{elapsed_ms}ms]"
        return super().format(record)


VERBOSE_LEVEL = 15
logging.addLevelName(VERBOSE_LEVEL, "VERBOSE")


def verbose(self: logging.Logger, message, *args, **kwargs):
    if self.isEnabledFor(VERBOSE_LEVEL):
        self._log(VERBOSE_LEVEL, message, args, **kwargs)


logging.Logger.verbose = verbose  # type: ignore[attr-defined]


def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    """Configure root logging and reduce distributed noise by default."""

    if debug:
        log_level = logging.DEBUG
    elif verbose:
        log_level = VERBOSE_LEVEL
    else:
        log_level = logging.INFO

    def _resolve_main_log_path() -> Path:
        configured = os.environ.get(_MAIN_LOG_ENV, "").strip()
        if configured:
            target = Path(configured).expanduser().resolve()
        else:
            repo_root = Path(__file__).resolve().parents[3]
            target = (repo_root / Path(*_DEFAULT_MAIN_LOG_RELATIVE)).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(ElapsedMsFormatter("%(elapsed)s %(message)s"))
    file_handler = logging.FileHandler(_resolve_main_log_path(), encoding="utf-8")
    file_handler.setFormatter(
        ElapsedMsFormatter("%(asctime)s %(levelname)s %(name)s %(elapsed)s %(message)s")
    )

    root = logging.getLogger()
    root.handlers = []
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(log_level)

    dask.config.set({"distributed.worker.redirect_stdouts": True})

    noisy_loggers = [
        "distributed",
        "distributed.core",
        "distributed.scheduler",
        "distributed.worker",
        "distributed.client",
        "distributed.comm",
        "distributed.protocol",
        "distributed.deploy",
        "distributed.diagnostics",
        "dask",
        "dask.bag",
        "tornado",
        "bokeh",
        "asyncio",
        "fsspec",
    ]
    target_level = logging.DEBUG if debug else logging.WARNING
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(target_level)

    if not debug:
        import warnings

        warnings.filterwarnings("ignore", category=UserWarning, module="distributed")
        warnings.filterwarnings("ignore", category=UserWarning, module="dask")


def _feature_or_exit(name: str):
    feature = FeatureRegistry.get_feature(name)
    if feature is None:
        logger.error("Unknown feature: %s", name)
        raise typer.Exit(code=1)
    return feature


def _handle_cli_result(feature_name: str, result: OperationResult[Any]) -> None:
    if not result.success:
        logger.error("Operation failed: %s", result.error or "Unknown error")
        raise typer.Exit(code=1)

    if not result.data:
        return

    data = result.data
    if feature_name == "version":
        logger.info("VoxLogicA version: %s", data.get("version", "unknown"))
        return

    if feature_name == "run":
        logger.debug("Program completed successfully")
        logger.debug("  Operations: %d", data.get("operations", 0))
        logger.debug("  Goals: %d", data.get("goals", 0))
        for message in data.get("messages", []):
            logger.info("  %s", message)
        logger.debug(json.dumps(data, indent=2, cls=WorkPlanJSONEncoder))


app = typer.Typer(
    name="voxlogica",
    help="VoxLogicA - A tool for analyzing VoxLogicA programs",
    add_completion=False,
)

mcp_app = typer.Typer(
    help="Model Context Protocol servers for VoxLogicA",
    add_completion=False,
)

app.add_typer(mcp_app, name="mcp")


@app.command()
def version() -> None:
    """Show VoxLogicA version."""

    setup_logging(False)
    feature = _feature_or_exit("version")
    _handle_cli_result("version", feature.handler())


@app.command()
def run(
    filename: str = typer.Argument(..., help="VoxLogicA session file"),
    save_task_graph: Optional[str] = typer.Option(None, help="Save the task graph"),
    save_task_graph_as_dot: Optional[str] = typer.Option(
        None,
        help="Save the task graph in .dot format and exit",
    ),
    save_task_graph_as_json: Optional[str] = typer.Option(
        None,
        help="Save the task graph as JSON and exit",
    ),
    save_syntax: Optional[str] = typer.Option(
        None,
        help="Save the AST in text format and exit",
    ),
    compute_memory_assignment: bool = typer.Option(
        False,
        "--compute-memory-assignment",
        help="Compute and display memory buffer assignments",
    ),
    execute: bool = typer.Option(
        True,
        "--execute/--no-execute",
        help="Execute the workplan (default: --execute)",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Force recomputation without reading or writing cache",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help=(
            "Drop cached entries for all hashes reachable from the loaded program "
            "before execution"
        ),
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging"),
    dask_dashboard: bool = typer.Option(
        False,
        "--dask-dashboard",
        help="Enable Dask dashboard for execution debugging",
    ),
    execution_strategy: str = typer.Option(
        "dask",
        "--execution-strategy",
        help="Execution strategy to use (only dask is supported).",
    ),
    legacy: bool = typer.Option(
        False,
        "--legacy",
        help="Enable legacy mode (allows effectful primitives).",
    ),
) -> None:
    """Run a VoxLogicA program."""

    setup_logging(debug, verbose)
    logger.info("VoxLogicA version: %s", get_version())

    try:
        program = Path(filename).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("File not found: %s", filename)
        raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error reading file %s: %s", filename, exc)
        raise typer.Exit(code=1)

    chosen_strategy = "dask"
    if str(execution_strategy).strip().lower() not in {"", "dask"}:
        logger.error("Only dask execution strategy is supported.")
        raise typer.Exit(code=2)
    legacy_enabled = legacy if isinstance(legacy, bool) else False
    fresh_enabled = fresh if isinstance(fresh, bool) else False
    feature = _feature_or_exit("run")
    result = feature.handler(
        program=program,
        filename=filename,
        save_task_graph=save_task_graph,
        save_task_graph_as_dot=save_task_graph_as_dot,
        save_task_graph_as_json=save_task_graph_as_json,
        save_syntax=save_syntax,
        compute_memory_assignment=compute_memory_assignment,
        execute=execute,
        no_cache=no_cache,
        fresh=fresh_enabled,
        debug=debug,
        verbose=verbose,
        dask_dashboard=dask_dashboard,
        execution_strategy=chosen_strategy,
        legacy=legacy_enabled,
        serve_mode=False,
    )

    _handle_cli_result("run", result)


@app.command("list-primitives")
def list_primitives(
    namespace: Optional[str] = typer.Argument(
        None,
        help="Namespace to filter primitives (optional)",
    )
) -> None:
    """List available primitives."""

    setup_logging(False)
    result = handle_list_primitives(namespace=namespace)
    if not result.success:
        logger.error("Error: %s", result.error)
        raise typer.Exit(code=1)

    data = result.data or {}
    if data.get("namespace_filter"):
        print(f"Primitives in namespace '{data['namespace_filter']}':")
    else:
        print("All available primitives:")

    primitives = data.get("primitives", {})
    if not primitives:
        print("  No primitives found.")
    else:
        for name, description in sorted(primitives.items()):
            print(f"  {name:<30} {description}")

    if not data.get("namespace_filter"):
        namespaces = data.get("namespaces", [])
        if namespaces:
            print(f"\nAvailable namespaces: {', '.join(sorted(namespaces))}")
            print("Use 'voxlogica list-primitives <namespace>' to filter by namespace.")


@app.command()
def repl(
    execution_strategy: str = typer.Option(
        "dask",
        "--execution-strategy",
        help="Execution strategy to use (only dask is supported).",
    ),
    legacy: bool = typer.Option(
        False,
        "--legacy",
        help="Enable legacy mode (allows effectful primitives in REPL).",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging"),
) -> None:
    """Start interactive VoxLogicA REPL."""

    setup_logging(debug, verbose)
    chosen_strategy = "dask"
    if str(execution_strategy).strip().lower() not in {"", "dask"}:
        logger.error("Only dask execution strategy is supported in REPL.")
        raise typer.Exit(code=2)
    legacy_enabled = legacy if isinstance(legacy, bool) else False
    exit_code = run_interactive_repl(strategy=chosen_strategy, legacy=legacy_enabled)
    raise typer.Exit(code=exit_code)


live_reload_clients: set[WebSocket] = set()
_file_observer: BaseObserver | None = None
_DEV_BACKEND_WATCHABLE_SUFFIXES = frozenset({".py", ".toml", ".yaml", ".yml", ".json"})
_DEV_BACKEND_WATCHABLE_FILES = frozenset({"requirements.txt", "requirements-test.txt", "setup.py"})
_DEV_BACKEND_IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "node_modules", ".venv"})


class ReloadEventHandler(FileSystemEventHandler):
    """Watch static files and notify websocket clients on change."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.logger = logging.getLogger("voxlogica.filewatcher")

    def on_any_event(self, event) -> None:  # noqa: ANN001
        if event.is_directory:
            return

        self.logger.info("File change detected: %s (%s)", event.src_path, event.event_type)
        asyncio.run_coroutine_threadsafe(self._notify_clients(), self.loop)

    async def _notify_clients(self) -> None:
        self.logger.info("Notifying %d WebSocket clients", len(live_reload_clients))
        failed: set[WebSocket] = set()

        for websocket in list(live_reload_clients):
            try:
                await websocket.send_text("reload")
            except Exception:  # noqa: BLE001
                failed.add(websocket)

        for websocket in failed:
            live_reload_clients.discard(websocket)


def _is_dev_backend_watch_path(repo_root: Path, candidate: str | Path | None) -> bool:
    if candidate is None:
        return False

    candidate_path = Path(candidate)
    if not candidate_path.parts:
        return False
    if any(part in _DEV_BACKEND_IGNORED_PARTS for part in candidate_path.parts):
        return False

    try:
        relative_path = candidate_path.resolve().relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return False

    if len(relative_path.parts) < 2 or relative_path.parts[:2] != ("implementation", "python"):
        return False

    if relative_path.name in _DEV_BACKEND_WATCHABLE_FILES:
        return True

    return relative_path.suffix.lower() in _DEV_BACKEND_WATCHABLE_SUFFIXES


class DevBackendReloadEventHandler(FileSystemEventHandler):
    """Watch Python/backend sources and request supervisor restarts."""

    def __init__(self, repo_root: Path, on_change: Callable[[str], None]):
        self.repo_root = repo_root.resolve()
        self.on_change = on_change
        self.logger = logging.getLogger("voxlogica.devwatch")

    def on_any_event(self, event) -> None:  # noqa: ANN001
        if event.is_directory:
            return

        candidates = [getattr(event, "src_path", None)]
        destination = getattr(event, "dest_path", None)
        if destination:
            candidates.append(destination)

        for candidate in candidates:
            if not _is_dev_backend_watch_path(self.repo_root, candidate):
                continue
            self.logger.info("Backend source change detected: %s (%s)", candidate, event.event_type)
            self.on_change(str(candidate))
            break


def _start_dev_backend_watcher(repo_root: Path, on_change: Callable[[str], None]) -> BaseObserver | None:
    watch_root = (repo_root / "implementation" / "python").resolve()
    if not watch_root.exists():
        return None

    observer = Observer()
    observer.schedule(DevBackendReloadEventHandler(repo_root, on_change), str(watch_root), recursive=True)
    observer.start()
    return observer


def _stop_dev_backend_watcher(observer: BaseObserver | None) -> None:
    if observer is None:
        return
    observer.stop()
    observer.join()


def start_file_watcher() -> None:
    """Start static directory file watcher for web live reload."""
    global _file_observer

    if _file_observer is not None:
        return

    static_dir = Path(__file__).parent / "static"
    if not static_dir.exists():
        return

    logger_fw = logging.getLogger("voxlogica.filewatcher")
    logger_fw.info("Starting file watcher for directory: %s", static_dir)

    loop = asyncio.get_running_loop()
    event_handler = ReloadEventHandler(loop)

    _file_observer = Observer()
    _file_observer.schedule(event_handler, str(static_dir), recursive=True)
    _file_observer.start()


def stop_file_watcher() -> None:
    """Stop static directory file watcher."""
    global _file_observer
    if _file_observer is None:
        return

    _file_observer.stop()
    _file_observer.join()
    _file_observer = None


@contextlib.asynccontextmanager
async def lifespan(_api_app: FastAPI):
    """FastAPI lifespan hook for startup/shutdown operations."""
    start_file_watcher()
    try:
        yield
    finally:
        stop_file_watcher()


api_app = FastAPI(
    title="VoxLogicA API",
    description="API for VoxLogicA program analysis",
    version=get_version(),
    lifespan=lifespan,
)

api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _should_trace_http_request(path: str) -> bool:
    """Return whether request-level tracing should be emitted for this path."""
    normalized = str(path or "")
    if normalized == "/api/v1/log/client":
        return True
    if normalized.startswith("/api/v1/playground"):
        return True
    if normalized.startswith("/api/v1/testing"):
        return True
    if normalized.startswith("/api/v1/version"):
        return True
    if normalized.startswith("/api/v1/capabilities"):
        return True
    if normalized.startswith("/api/v1/results"):
        return True
    if normalized.startswith("/api/v1/docs/gallery"):
        return True
    if normalized.startswith("/api/v1/storage"):
        return True
    return False


@api_app.middleware("http")
async def trace_http_requests(request: Request, call_next):
    """Emit start/end timing for selected API endpoints to isolate transport delays."""
    path = request.url.path
    if not _should_trace_http_request(path):
        return await call_next(request)

    trace_logger = logging.getLogger("voxlogica.http")
    trace_id = hashlib.sha1(f"{time.time_ns()}:{request.method}:{path}".encode("utf-8")).hexdigest()[:10]
    started = time.perf_counter()
    trace_logger.info(
        "[http:%s] start method=%s path=%s query=%s",
        trace_id,
        request.method,
        path,
        request.url.query or "-",
    )
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        trace_logger.error(
            "[http:%s] error method=%s path=%s elapsed=%.1fms error=%s",
            trace_id,
            request.method,
            path,
            elapsed_ms,
            exc,
        )
        raise

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    trace_logger.info(
        "[http:%s] done method=%s path=%s status=%s elapsed=%.1fms",
        trace_id,
        request.method,
        path,
        response.status_code,
        elapsed_ms,
    )
    return response


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles variant that disables browser caching for UI assets."""

    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            response.headers["Cache-Control"] = "no-store"
        return response


_ASSET_REV_SENTINEL = "__ASSET_REV__"


def _compute_static_asset_revision(static_dir: Path) -> str:
    """Build a deterministic revision token from current static asset mtimes/sizes."""
    parts: list[str] = []
    for rel in ("index.html", "app.css", "app.js", "results_viewer.js"):
        path = static_dir / rel
        if not path.exists():
            continue
        stat = path.stat()
        parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


static_path = Path(__file__).parent / "static"
if static_path.exists():
    api_app.mount("/static", NoCacheStaticFiles(directory=str(static_path)), name="static")


def _iter_ui_source_files(ui_dir: Path) -> list[Path]:
    """Return source files that should trigger a UI rebuild when changed."""
    files: list[Path] = []
    for rel in ("package.json", "package-lock.json", "vite.config.js", "index.html"):
        path = ui_dir / rel
        if path.exists():
            files.append(path)
    src_dir = ui_dir / "src"
    if src_dir.exists():
        files.extend(path for path in src_dir.rglob("*") if path.is_file())
    return files


def _ui_assets_up_to_date(ui_dir: Path, static_dir: Path) -> bool:
    """Return True when built UI assets are newer than all relevant UI sources."""
    outputs = [static_dir / "app.js", static_dir / "app.css"]
    if not all(path.exists() for path in outputs):
        return False
    source_files = _iter_ui_source_files(ui_dir)
    if not source_files:
        return True
    latest_source_mtime = max(path.stat().st_mtime_ns for path in source_files)
    earliest_output_mtime = min(path.stat().st_mtime_ns for path in outputs)
    return earliest_output_mtime >= latest_source_mtime


def _build_ui_assets_if_needed(*, build_enabled: bool, force_build: bool = False) -> None:
    """Build Svelte UI assets when serve starts, unless disabled or already fresh."""
    if not build_enabled:
        logger.info("UI auto-build disabled via --no-build-ui.")
        return

    repo_root = Path(__file__).resolve().parents[3]
    ui_dir = repo_root / "implementation" / "ui"
    static_dir = Path(__file__).parent / "static"

    if not ui_dir.exists():
        logger.debug("UI workspace not found at %s; skipping UI build.", ui_dir)
        return
    if not force_build and _ui_assets_up_to_date(ui_dir, static_dir):
        logger.debug("UI assets are up to date; skipping UI build.")
        return

    npm_bin = shutil.which("npm")
    if npm_bin is None:
        outputs = [static_dir / "app.js", static_dir / "app.css"]
        if all(path.exists() for path in outputs):
            logger.warning("npm is not available; serving existing built UI assets.")
            return
        raise RuntimeError("UI assets are missing/stale and npm is not installed. Install Node.js/npm or use --no-build-ui.")

    logger.info("Building UI assets from %s", ui_dir)
    node_modules = ui_dir / "node_modules"
    install_cmd = [npm_bin, "install", "--no-audit", "--no-fund"]
    build_cmd = [npm_bin, "run", "build"]
    env = dict(os.environ)

    try:
        if not node_modules.exists():
            logger.info("Installing UI dependencies...")
            subprocess.run(install_cmd, cwd=ui_dir, check=True, env=env)
        subprocess.run(build_cmd, cwd=ui_dir, check=True, env=env)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - exercised via integration/manual runs
        raise RuntimeError(f"UI build failed (command: {' '.join(exc.cmd)}).") from exc


def _terminate_child_process(process: subprocess.Popen[str] | None, *, name: str, timeout_s: float = 8.0) -> None:
    """Terminate a child process gracefully, then force kill if needed."""
    if process is None:
        return
    if process.poll() is not None:
        return
    logger.info("Stopping %s process (pid=%s)...", name, process.pid)
    process.terminate()
    try:
        process.wait(timeout=timeout_s)
        return
    except subprocess.TimeoutExpired:
        logger.warning("%s process did not exit in %.1fs; killing.", name, timeout_s)
    process.kill()
    process.wait(timeout=3.0)


def _stdin_command_bridge(
    *,
    on_command: Callable[[str], None],
    label: str = "serve",
) -> tuple[threading.Event, threading.Thread] | None:
    """Start a best-effort stdin command reader thread for interactive terminal controls."""
    if not sys.stdin or not sys.stdin.isatty():
        return None

    stop_event = threading.Event()

    def _reader() -> None:
        while not stop_event.is_set():
            try:
                raw = sys.stdin.readline()
            except Exception:
                break
            if raw == "":
                break
            command = str(raw).strip()
            if not command:
                continue
            try:
                on_command(command)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s command failed: %s", label, exc)

    thread = threading.Thread(target=_reader, name=f"voxlogica-{label}-stdin", daemon=True)
    thread.start()
    return stop_event, thread

api_router = APIRouter(prefix="/api/v1")
playground_jobs = PlaygroundJobManager()
testing_jobs = TestingJobManager()


@api_router.get("/version")
async def get_version_endpoint() -> dict[str, Any]:
    """Return VoxLogicA version payload."""
    feature = FeatureRegistry.get_feature("version")
    if feature is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version feature not found")

    result = feature.handler()
    if not result.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error or "An error occurred")

    return result.data or {}


@api_router.get("/capabilities")
async def capabilities_endpoint() -> dict[str, Any]:
    """Return serve/API capabilities exposed by this backend process."""
    return {
        "playground_jobs": True,
        "playground_program_library": True,
        "playground_symbols": True,
        "playground_value_resolver": True,
        "playground_value_paging": True,
        "client_logging": True,
        "testing_jobs": True,
        "testing_report": True,
        "storage_stats": False,
        "storage_stats_lightweight": True,
        "store_results_viewer": True,
        "store_results_paging": True,
        "gallery": True,
    }


@api_router.post("/log/client")
async def client_log_endpoint(request: ClientLogBatchRequest) -> dict[str, Any]:
    """Ingest browser log events and mirror them into the unified server log file."""
    client_logger = logging.getLogger("voxlogica.client")
    accepted = 0
    truncated = False
    max_events = 200
    for event in list(request.events)[:max_events]:
        accepted += 1
        level_name = str(event.level or "info").strip().lower()
        level_value = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warn": logging.WARNING,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(level_name, logging.INFO)
        event_payload = {
            "source": event.source,
            "url": event.url,
            "ts": event.ts,
            "user_agent": event.user_agent,
            "payload": event.payload,
        }
        client_logger.log(
            level_value,
            "[browser] %s | %s",
            str(event.message or ""),
            json.dumps(event_payload, sort_keys=True, default=str),
        )
    if len(request.events) > max_events:
        truncated = True
    return {
        "ok": True,
        "accepted": accepted,
        "truncated": truncated,
    }


@api_router.post("/run")
async def run_program_endpoint(request: RunRequest) -> dict[str, Any]:
    """Execute a VoxLogicA run request."""
    feature = FeatureRegistry.get_feature("run")
    if feature is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run feature not found")

    payload = _prepare_serve_run_payload(request)
    result = feature.handler(**payload)
    if not result.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error or "An error occurred")

    return result.data or {}


@api_router.get("/primitives")
async def list_primitives_endpoint(namespace: Optional[str] = Query(default=None)) -> dict[str, Any]:
    """List registered primitives through API."""
    result = handle_list_primitives(namespace=namespace)
    if not result.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error or "An error occurred")
    return result.data or {}


@api_router.get("/docs/gallery")
async def docs_gallery_endpoint() -> dict[str, Any]:
    """Return markdown gallery and parsed playground directives."""
    return load_gallery_document()


@api_router.get("/playground/files")
async def list_playground_files_endpoint(limit: int = Query(default=400, ge=1, le=1000)) -> dict[str, Any]:
    """List available program files from the fixed playground load directory."""
    return list_playground_programs(limit=limit)


@api_router.get("/playground/files/{relative_path:path}")
async def get_playground_file_endpoint(relative_path: str) -> dict[str, Any]:
    """Load one program file from the fixed playground load directory."""
    try:
        return load_playground_program(relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@api_router.post("/playground/symbols")
async def playground_symbols_endpoint(request: PlaygroundSymbolsRequest) -> dict[str, Any]:
    """Parse-only endpoint used by editor hover/selector to pre-compute node hashes."""
    try:
        workplan, symbol_table, print_targets = _program_introspection(
            request.program,
            legacy=False,
            serve_mode=True,
            enforce_policy=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "program_hash": _program_hash(request.program),
            "operations": 0,
            "goals": 0,
            "symbol_table": {},
            "print_targets": [],
            "diagnostics": diagnostics_from_exception(exc),
        }
    diagnostics = validate_workplan_policy(
        workplan,
        legacy=False,
        serve_mode=True,
    )
    symbol_output_kinds: dict[str, str] = {}
    for name, node_id in symbol_table.items():
        node = workplan.nodes.get(node_id)
        hinted_kind = str(getattr(node, "output_kind", "unknown"))
        if str(getattr(node, "kind", "")) == "constant":
            try:
                hinted_kind = str(adapt_runtime_value(getattr(node, "attrs", {}).get("value")).vox_type)
            except Exception:
                pass
        symbol_output_kinds[str(name)] = hinted_kind
    return {
        "available": True,
        "program_hash": _program_hash(request.program),
        "operations": len(workplan.operations),
        "goals": len(workplan.goals),
        "symbol_table": symbol_table,
        "symbol_output_kinds": symbol_output_kinds,
        "print_targets": print_targets,
        "diagnostics": diagnostics_payload(diagnostics),
    }


@api_router.post("/playground/graph")
async def playground_graph_endpoint(request: PlaygroundGraphRequest) -> dict[str, Any]:
    """Return symbolic compute graph nodes and dependencies for the program."""
    try:
        workplan, symbol_table, print_targets = _program_introspection(
            request.program,
            legacy=False,
            serve_mode=True,
            enforce_policy=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "program_hash": _program_hash(request.program),
            "nodes": [],
            "goals": [],
            "symbol_table": {},
            "print_targets": [],
            "diagnostics": diagnostics_from_exception(exc),
        }

    diagnostics = validate_workplan_policy(
        workplan,
        legacy=False,
        serve_mode=True,
    )
    names_by_node: dict[str, list[str]] = {}
    for name, node_id in symbol_table.items():
        names_by_node.setdefault(str(node_id), []).append(str(name))

    nodes: list[dict[str, Any]] = []
    for node_id, node in workplan.nodes.items():
        deps = [str(dep) for dep in getattr(node, "args", ()) or ()]
        for _key, dep in getattr(node, "kwargs", ()) or ():
            deps.append(str(dep))
        nodes.append(
            {
                "node_id": str(node_id),
                "operator": str(getattr(node, "operator", "")),
                "kind": str(getattr(node, "kind", "")),
                "output_kind": str(getattr(node, "output_kind", "")),
                "variables": sorted(names_by_node.get(str(node_id), [])),
                "dependencies": deps,
            }
        )

    goals = [
        {
            "name": str(goal.name),
            "node_id": str(goal.id),
            "operation": str(goal.operation),
        }
        for goal in getattr(workplan, "goals", [])
    ]

    return {
        "available": True,
        "program_hash": _program_hash(request.program),
        "nodes": nodes,
        "goals": goals,
        "symbol_table": symbol_table,
        "print_targets": print_targets,
        "diagnostics": diagnostics_payload(diagnostics),
    }


@api_router.post("/playground/value")
async def playground_value_endpoint(request: PlaygroundValueRequest) -> dict[str, Any]:
    """Resolve one node lazily with cache-first lookup and prioritized on-demand execution."""
    value_logger = logging.getLogger("voxlogica.playground.value")
    request_started = time.perf_counter()
    requested_variable = str(request.variable or "").strip()
    requested_node = str(request.node_id or "").strip()
    requested_path = str(request.path or "").strip() or "/"
    program_hash = _program_hash(request.program)
    request_id = hashlib.sha1(
        f"{program_hash}:{requested_variable}:{requested_node}:{requested_path}:{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:12]
    phase_timings_ms: dict[str, float] = {}
    introspection_cache_state = "unknown"

    def _request_elapsed_ms() -> float:
        return (time.perf_counter() - request_started) * 1000.0

    value_logger.info(
        "[value:%s] start enqueue=%s variable=%s node=%s path=%s program=%s",
        request_id,
        bool(request.enqueue),
        requested_variable or "-",
        requested_node[:12] if requested_node else "-",
        requested_path,
        program_hash[:12],
    )
    introspection_started = time.perf_counter()
    introspection_cache_before = _cached_program_introspection.cache_info()
    try:
        workplan, symbol_table, _print_targets = _program_introspection(
            request.program,
            legacy=False,
            serve_mode=True,
            enforce_policy=False,
        )
    except Exception as exc:  # noqa: BLE001
        value_logger.error(
            "[value:%s] introspection failed after %.1fms: %s",
            request_id,
            _request_elapsed_ms(),
            exc,
        )
        detail = _diagnostic_http_detail(exc, f"Unable to parse program: {exc}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    introspection_cache_after = _cached_program_introspection.cache_info()
    if introspection_cache_after.hits > introspection_cache_before.hits:
        introspection_cache_state = "hit"
    elif introspection_cache_after.misses > introspection_cache_before.misses:
        introspection_cache_state = "miss"
    introspection_ms = (time.perf_counter() - introspection_started) * 1000.0
    phase_timings_ms["introspection"] = introspection_ms
    if introspection_ms >= 120.0:
        value_logger.info(
            "[value:%s] introspection %.1fms cache=%s symbols=%d nodes=%d",
            request_id,
            introspection_ms,
            introspection_cache_state,
            len(symbol_table),
            len(getattr(workplan, "nodes", {})),
        )

    node_id, variable_name = _resolve_requested_node(
        symbol_table=symbol_table,
        node_id=request.node_id,
        variable=request.variable,
        allowed_nodes=set(workplan.nodes.keys()),
    )
    selected_node = workplan.nodes.get(node_id)
    node_output_kind = str(getattr(selected_node, "output_kind", "unknown")) if selected_node is not None else "unknown"
    strategy = "dask"
    view_path = request.path or ""
    storage = get_storage()
    priority_context = compute_priority_context(
        job_kind="value-resolve",
        enqueue=bool(request.enqueue),
        ui_awaited=bool(request.ui_awaited),
        path=view_path,
        interaction=request.interaction,
    )

    def _attach_common(payload: dict[str, Any]) -> dict[str, Any]:
        payload["node_id"] = node_id
        payload["path"] = view_path
        payload["execution_strategy"] = strategy
        payload["ui_awaited"] = bool(request.ui_awaited)
        payload["priority_bucket"] = priority_context.bucket
        payload["urgency_score"] = priority_context.urgency_score
        if variable_name:
            payload["variable"] = variable_name
        return payload

    def _attach_and_log(payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
        attached = _attach_common(payload)
        timings_summary = ",".join(
            f"{phase}={duration_ms:.1f}ms" for phase, duration_ms in phase_timings_ms.items()
        ) or "-"
        value_logger.info(
            "[value:%s] %s materialization=%s compute_status=%s store_status=%s enqueued=%s job=%s "
            "introspection_cache=%s timings=%s elapsed=%.1fms",
            request_id,
            reason,
            str(attached.get("materialization", "-")),
            str(attached.get("compute_status", attached.get("status", "-"))),
            str(attached.get("store_status", attached.get("status", "-"))),
            bool(attached.get("request_enqueued", False)),
            str(attached.get("job_id", "-"))[:12],
            introspection_cache_state,
            timings_summary,
            _request_elapsed_ms(),
        )
        return attached

    def _execution_errors_from_job(job_payload: dict[str, Any] | None) -> dict[str, str]:
        if not isinstance(job_payload, dict):
            return {}
        result_payload = job_payload.get("result")
        if not isinstance(result_payload, dict):
            return {}
        execution_payload = result_payload.get("execution")
        if not isinstance(execution_payload, dict):
            return {}
        errors_payload = execution_payload.get("errors")
        if not isinstance(errors_payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in errors_payload.items():
            out[str(key)] = str(value)
        return out

    def _execution_error_details_from_job(job_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not isinstance(job_payload, dict):
            return {}
        result_payload = job_payload.get("result")
        if not isinstance(result_payload, dict):
            return {}
        execution_payload = result_payload.get("execution")
        if not isinstance(execution_payload, dict):
            return {}
        details_payload = execution_payload.get("error_details")
        if not isinstance(details_payload, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for key, value in details_payload.items():
            if not isinstance(value, dict):
                continue
            out[str(key)] = dict(value)
        return out

    def _cache_summary_from_job(job_payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(job_payload, dict):
            return {}
        result_payload = job_payload.get("result")
        if not isinstance(result_payload, dict):
            return {}
        execution_payload = result_payload.get("execution")
        if not isinstance(execution_payload, dict):
            return {}
        summary = execution_payload.get("cache_summary")
        if isinstance(summary, dict):
            return dict(summary)
        return {}

    def _parse_iso_utc(value: Any) -> float | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return float(parsed.timestamp())
        except Exception:
            return None

    def _goal_result_from_job(job_payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(job_payload, dict):
            return None
        result_payload = job_payload.get("result")
        if not isinstance(result_payload, dict):
            return None
        goal_results = result_payload.get("goal_results")
        if not isinstance(goal_results, list):
            return None
        for goal_result in goal_results:
            if not isinstance(goal_result, dict):
                continue
            if str(goal_result.get("node_id", "")) != node_id:
                continue
            return goal_result
        return None

    def _runtime_descriptor_from_job(
        job_payload: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        goal_result = _goal_result_from_job(job_payload)
        if not isinstance(goal_result, dict):
            return None, {}
        descriptor = goal_result.get("runtime_descriptor")
        metadata = goal_result.get("metadata")
        if isinstance(descriptor, dict):
            return descriptor, metadata if isinstance(metadata, dict) else {}
        return None, metadata if isinstance(metadata, dict) else {}

    def _runtime_preview_from_job(
        job_payload: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        goal_result = _goal_result_from_job(job_payload)
        if not isinstance(goal_result, dict):
            return None, {}
        metadata = goal_result.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        previews = goal_result.get("runtime_previews")
        if not isinstance(previews, dict):
            return None, metadata_dict
        requested_path = normalize_path(view_path)
        lookup_candidates = [requested_path]
        if requested_path in {"", "/"}:
            lookup_candidates.extend(["", "/"])
        preview: dict[str, Any] | None = None
        for candidate in lookup_candidates:
            candidate_preview = previews.get(candidate)
            if isinstance(candidate_preview, dict):
                preview = candidate_preview
                break
        if not isinstance(preview, dict):
            return None, metadata_dict
        return preview, metadata_dict

    def _payload_from_runtime_preview(
        *,
        preview: dict[str, Any],
        metadata: dict[str, Any],
        compute_status: str,
        source: str,
        job_id: Any,
    ) -> dict[str, Any] | None:
        preview_descriptor = preview.get("descriptor")
        if not isinstance(preview_descriptor, dict):
            return None
        preview_status = str(preview.get("status", "materialized") or "materialized").strip().lower()
        pending_states = {"not_loaded", "queued", "blocked", "running", "persisting", "pending", "missing"}
        failed_states = {"failed", "killed", "error"}
        ready_states = {"materialized", "computed", "completed", "cached", "ready"}
        if preview_status in failed_states:
            materialization = "failed"
            resolved_compute_status = "failed"
            available = False
        elif preview_status in pending_states:
            materialization = "pending"
            resolved_compute_status = "pending" if preview_status == "not_loaded" else preview_status
            available = False
        else:
            materialization = "computed" if preview_status in ready_states else "pending"
            resolved_compute_status = compute_status if materialization == "computed" else preview_status
            available = materialization == "computed"
        preview_payload: dict[str, Any] = {
            "available": available,
            "status": "materialized" if materialization == "computed" else preview_status,
            "materialization": materialization,
            "compute_status": resolved_compute_status,
            "store_status": "missing",
            "job_id": job_id,
            "request_enqueued": False,
            "descriptor": preview_descriptor,
        }
        preview_value = preview.get("value")
        if preview_value is not None:
            preview_payload["value"] = preview_value
        preview_page = preview.get("page")
        if isinstance(preview_page, dict):
            preview_payload["runtime_preview_page"] = preview_page
        if preview.get("error") is not None:
            preview_payload["error"] = str(preview.get("error"))
        if preview.get("state_reason") is not None:
            preview_payload["state_reason"] = str(preview.get("state_reason"))
        if preview.get("blocked_on") is not None:
            preview_payload["blocked_on"] = str(preview.get("blocked_on"))
        preview_payload["metadata"] = {
            **(metadata if isinstance(metadata, dict) else {}),
            "source": source,
        }
        return preview_payload

    def _runtime_inspection_failure_payload(
        *,
        preview: dict[str, Any],
        compute_status: str,
        job_id: Any,
    ) -> dict[str, Any]:
        raw_error = str(preview.get("runtime_error") or "Nested value inspection failed.").strip()
        error_message = raw_error
        if raw_error.startswith("'") and raw_error.endswith("'") and len(raw_error) >= 2:
            error_message = raw_error[1:-1]
        return {
            "available": False,
            "materialization": "failed",
            "compute_status": "failed" if compute_status == "completed" else compute_status,
            "status": "failed",
            "store_status": "missing",
            "job_id": job_id,
            "request_enqueued": False,
            "error": error_message,
            "diagnostics": {
                "code": "E_RUNTIME_INSPECTION",
                "message": error_message,
                "node_id": str(preview.get("node_id") or node_id),
                "path": str(preview.get("path") or view_path or "/"),
                "runtime_error_type": str(preview.get("runtime_error_type") or ""),
            },
        }

    def _inspect_sequence_reference_from_store(
        *,
        root_descriptor: dict[str, Any] | None,
        compute_status: str,
        job_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if view_path in {"", "/"}:
            return None
        if not isinstance(root_descriptor, dict):
            return None
        if str(root_descriptor.get("vox_type", "")) != "sequence":
            return None
        referenced = _sequence_reference_for_path(node_id, view_path)
        if referenced is None:
            return None
        referenced_node_id, referenced_remainder = referenced
        try:
            item_payload, lookup_state = _inspect_store_result_best_effort(
                storage,
                node_id=referenced_node_id,
                path=referenced_remainder,
                lock_wait_ms=0.0,
            )
            if lookup_state == "busy":
                return None
            if item_payload is None:
                return None
        except UnsupportedVoxValueError as exc:
            return _attach_and_log(
                {
                    "available": False,
                    "materialization": "failed",
                    "compute_status": "failed",
                    "status": "failed",
                    "error": str(exc),
                    "job_id": job_payload.get("job_id"),
                    "request_enqueued": False,
                    "diagnostics": {
                        "code": exc.code,
                        "message": str(exc),
                        "node_id": node_id,
                        "path": view_path or "/",
                    },
                },
                reason="sequence-reference-unsupported",
            )

        item_status = str(item_payload.get("status", "unknown"))
        if item_status == "materialized":
            item_payload["materialization"] = "computed"
            item_payload["compute_status"] = compute_status
            item_payload["store_status"] = "materialized"
            item_payload["request_enqueued"] = False
            item_payload["resolved_store_node_id"] = referenced_node_id
            item_payload["resolved_store_path"] = referenced_remainder
            return _attach_and_log(item_payload, reason="sequence-reference-materialized")
        if item_status == "failed":
            return _attach_and_log(
                {
                    "available": False,
                    "materialization": "failed",
                    "compute_status": "failed",
                    "status": "failed",
                    "error": str(item_payload.get("error") or "Referenced sequence item failed."),
                    "job_id": job_payload.get("job_id"),
                    "request_enqueued": False,
                    "diagnostics": {
                        "node_id": node_id,
                        "path": view_path or "/",
                        "store_status": item_status,
                    },
                },
                reason="sequence-reference-failed",
            )
        return None

    def _failure_payload(
        *,
        compute_status: str,
        inspected_payload: dict[str, Any] | None,
        tracked_job_payload: dict[str, Any] | None,
        fallback_error: str,
        request_enqueued: bool,
    ) -> dict[str, Any]:
        store_status = str(inspected_payload.get("status")) if inspected_payload is not None else "missing"
        store_error = ""
        if inspected_payload is not None and inspected_payload.get("error"):
            store_error = str(inspected_payload.get("error"))

        job_error = ""
        log_tail = ""
        job_id = ""
        if isinstance(tracked_job_payload, dict):
            if tracked_job_payload.get("error"):
                job_error = str(tracked_job_payload.get("error"))
            if tracked_job_payload.get("log_tail"):
                log_tail = str(tracked_job_payload.get("log_tail"))
            if tracked_job_payload.get("job_id"):
                job_id = str(tracked_job_payload.get("job_id"))

        execution_errors = _execution_errors_from_job(tracked_job_payload)
        execution_error_details = _execution_error_details_from_job(tracked_job_payload)
        cache_summary = _cache_summary_from_job(tracked_job_payload)

        error_message = job_error or store_error or fallback_error
        payload: dict[str, Any] = {
            "available": False,
            "materialization": "failed",
            "compute_status": compute_status,
            "status": "failed",
            "error": error_message,
            "store_status": store_status,
            "request_enqueued": request_enqueued,
            "diagnostics": {
                "store_status": store_status,
                "store_error": store_error or None,
                "job_error": job_error or None,
                "execution_errors": execution_errors,
                "execution_error_details": execution_error_details,
                "cache_summary": cache_summary,
            },
        }
        if store_error:
            payload["store_error"] = store_error
        if job_error:
            payload["job_error"] = job_error
        if execution_errors:
            payload["execution_errors"] = execution_errors
        if execution_error_details:
            payload["execution_error_details"] = execution_error_details
        if cache_summary:
            payload["cache_summary"] = cache_summary
        if log_tail:
            payload["log_tail"] = log_tail
        if job_id:
            payload["job_id"] = job_id
        return payload

    # Fast-path literal constants: they are immediately knowable from the
    # reduced plan and should never wait for execution queue/persistence.
    if selected_node is not None and str(getattr(selected_node, "kind", "")) == "constant":
        try:
            constant_payload = describe_runtime_value(
                node_id=node_id,
                value=getattr(selected_node, "attrs", {}).get("value"),
                path=view_path,
            )
            constant_payload["materialization"] = "computed"
            constant_payload["compute_status"] = "computed"
            constant_payload["store_status"] = "ephemeral"
            constant_payload["request_enqueued"] = False
            constant_payload["metadata"] = {
                **(constant_payload.get("metadata") or {}),
                "source": "plan-constant",
            }
            return _attach_and_log(constant_payload, reason="constant-fast-path")
        except UnsupportedVoxValueError as exc:
            return _attach_and_log(
                {
                    "available": False,
                    "materialization": "failed",
                    "compute_status": "failed",
                    "status": "failed",
                    "error": str(exc),
                    "request_enqueued": False,
                    "diagnostics": {
                        "code": exc.code,
                        "message": str(exc),
                        "node_id": node_id,
                        "path": view_path or "/",
                    },
                },
                reason="constant-fast-path-unsupported",
            )
        except Exception as exc:  # noqa: BLE001
            return _attach_and_log(
                {
                    "available": False,
                    "materialization": "failed",
                    "compute_status": "failed",
                    "status": "failed",
                    "error": f"Unable to inspect constant value: {exc}",
                    "request_enqueued": False,
                    "diagnostics": {
                        "node_id": node_id,
                        "path": view_path or "/",
                    },
                },
                reason="constant-fast-path-error",
            )

    inspected: dict[str, Any] | None = None
    store_lookup_started = time.perf_counter()
    store_lookup_state = "missing"
    try:
        store_lock_wait_ms = 2.0 if bool(request.enqueue) else 0.0
        inspected, store_lookup_state = _inspect_store_result_best_effort(
            storage,
            node_id=node_id,
            path=view_path,
            lock_wait_ms=store_lock_wait_ms,
        )
    except UnsupportedVoxValueError as exc:
        return _attach_and_log(
            {
                "available": False,
                "materialization": "failed",
                "compute_status": "failed",
                "status": "failed",
                "error": str(exc),
                "request_enqueued": False,
                "diagnostics": {
                    "code": exc.code,
                    "message": str(exc),
                    "node_id": node_id,
                    "path": view_path or "/",
                },
            },
            reason="store-unsupported",
        )
    store_lookup_ms = (time.perf_counter() - store_lookup_started) * 1000.0
    phase_timings_ms["store_lookup"] = store_lookup_ms
    if store_lookup_ms >= 120.0:
        value_logger.info(
            "[value:%s] store-lookup %.1fms node=%s path=%s",
            request_id,
            store_lookup_ms,
            node_id[:12],
            view_path or "/",
        )
    elif store_lookup_state == "busy":
        value_logger.info(
            "[value:%s] store-lookup skipped lock-busy node=%s path=%s",
            request_id,
            node_id[:12],
            view_path or "/",
        )

    if inspected is not None and str(inspected.get("status")) == "materialized":
        inspected["materialization"] = "cached"
        inspected["compute_status"] = "cached"
        return _attach_and_log(inspected, reason="store-hit")

    tracked_lookup_started = time.perf_counter()
    tracked_job = playground_jobs.get_value_job(
        program_hash=program_hash,
        node_id=node_id,
        execution_strategy=strategy,
    )
    tracked_lookup_ms = (time.perf_counter() - tracked_lookup_started) * 1000.0
    phase_timings_ms["job_lookup"] = tracked_lookup_ms
    if tracked_job is not None or tracked_lookup_ms >= 120.0:
        value_logger.info(
            "[value:%s] value-job lookup %.1fms job=%s status=%s",
            request_id,
            tracked_lookup_ms,
            str((tracked_job or {}).get("job_id", "-"))[:12],
            str((tracked_job or {}).get("status", "none")),
        )

    if tracked_job is not None:
        job_status = str(tracked_job.get("status", "unknown"))
        if job_status in {"queued", "running"}:
            inspect_job_runtime = getattr(playground_jobs, "inspect_value_job_runtime", None)
            if callable(inspect_job_runtime):
                runtime_live_preview = inspect_job_runtime(
                    program_hash=program_hash,
                    node_id=node_id,
                    execution_strategy=strategy,
                    path=view_path,
                    page_offset=0,
                    page_limit=64,
                )
                if isinstance(runtime_live_preview, dict):
                    if runtime_live_preview.get("runtime_error"):
                        return _attach_and_log(
                            _runtime_inspection_failure_payload(
                                preview=runtime_live_preview,
                                compute_status=job_status,
                                job_id=tracked_job.get("job_id"),
                            ),
                            reason=f"job-{job_status}-runtime-error",
                        )
                    preview_payload = _payload_from_runtime_preview(
                        preview=runtime_live_preview,
                        metadata={"source": "runtime-live", "persisted": "pending"},
                        compute_status=job_status,
                        source="runtime-live",
                        job_id=tracked_job.get("job_id"),
                    )
                    if preview_payload is not None:
                        return _attach_and_log(preview_payload, reason=f"job-{job_status}-runtime-live")
            progress_descriptor = _in_progress_descriptor(
                output_kind=node_output_kind,
                path=view_path,
                status=job_status,
            )
            reference_payload = _inspect_sequence_reference_from_store(
                root_descriptor=progress_descriptor if str(progress_descriptor.get("vox_type", "")) == "sequence" else None,
                compute_status=job_status,
                job_payload=tracked_job,
            )
            if reference_payload is not None:
                return reference_payload
            return _attach_and_log(
                {
                    "available": False,
                    "materialization": "pending",
                    "compute_status": job_status,
                    "job_id": tracked_job.get("job_id"),
                    "log_tail": tracked_job.get("log_tail", ""),
                    "store_status": inspected.get("status") if inspected is not None else "missing",
                    "request_enqueued": False,
                    "descriptor": progress_descriptor,
                    "resolved_store_node_id": node_id,
                    "resolved_store_path": "",
                },
                reason=f"job-{job_status}",
            )
        if job_status == "completed":
            try:
                inspected, completed_lookup_state = _inspect_store_result_best_effort(
                    storage,
                    node_id=node_id,
                    path=view_path,
                    lock_wait_ms=0.0,
                )
                if inspected is not None and str(inspected.get("status")) == "materialized":
                    inspected["materialization"] = "computed"
                    inspected["compute_status"] = "completed"
                    inspected["job_id"] = tracked_job.get("job_id")
                    return _attach_and_log(inspected, reason="job-completed-store-hit")
                if completed_lookup_state == "busy":
                    inspected = None
            except UnsupportedVoxValueError as exc:
                return _attach_and_log(
                    {
                        "available": False,
                        "materialization": "failed",
                        "compute_status": "failed",
                        "status": "failed",
                        "error": str(exc),
                        "job_id": tracked_job.get("job_id"),
                        "request_enqueued": False,
                        "diagnostics": {
                            "code": exc.code,
                            "message": str(exc),
                            "node_id": node_id,
                            "path": view_path or "/",
                        },
                    },
                    reason="job-completed-store-unsupported",
                )
            transient_descriptor, transient_metadata = _runtime_descriptor_from_job(tracked_job)
            inspect_job_runtime = getattr(playground_jobs, "inspect_value_job_runtime", None)
            if callable(inspect_job_runtime):
                runtime_cached_preview = inspect_job_runtime(
                    program_hash=program_hash,
                    node_id=node_id,
                    execution_strategy=strategy,
                    path=view_path,
                    page_offset=0,
                    page_limit=64,
                )
                if isinstance(runtime_cached_preview, dict):
                    if runtime_cached_preview.get("runtime_error"):
                        return _attach_and_log(
                            _runtime_inspection_failure_payload(
                                preview=runtime_cached_preview,
                                compute_status="completed",
                                job_id=tracked_job.get("job_id"),
                            ),
                            reason="job-completed-runtime-error",
                        )
                    preview_payload = _payload_from_runtime_preview(
                        preview=runtime_cached_preview,
                        metadata=transient_metadata,
                        compute_status="completed",
                        source="runtime-cache",
                        job_id=tracked_job.get("job_id"),
                    )
                    if preview_payload is not None:
                        return _attach_and_log(preview_payload, reason="job-completed-runtime-cache")
            runtime_preview, runtime_preview_metadata = _runtime_preview_from_job(tracked_job)
            if isinstance(runtime_preview, dict):
                preview_payload = _payload_from_runtime_preview(
                    preview=runtime_preview,
                    metadata=runtime_preview_metadata,
                    compute_status="persisting",
                    source="runtime-preview",
                    job_id=tracked_job.get("job_id"),
                )
                if preview_payload is not None:
                    return _attach_and_log(preview_payload, reason="job-completed-runtime-preview")
            if transient_descriptor is not None and view_path not in {"", "/"}:
                root_descriptor = transient_descriptor.get("descriptor") if isinstance(transient_descriptor, dict) else None
                persisted_state = transient_metadata.get("persisted") if isinstance(transient_metadata, dict) else None
                if persisted_state == "pending":
                    reference_payload = _inspect_sequence_reference_from_store(
                        root_descriptor=root_descriptor if isinstance(root_descriptor, dict) else None,
                        compute_status="persisting",
                        job_payload=tracked_job,
                    )
                    if reference_payload is not None:
                        return reference_payload
                    finished_at = _parse_iso_utc(tracked_job.get("finished_at"))
                    elapsed_s = (time.time() - finished_at) if finished_at is not None else None
                    return _attach_and_log(
                        {
                            "available": False,
                            "materialization": "pending",
                            "compute_status": "persisting",
                            "job_id": tracked_job.get("job_id"),
                            "store_status": "missing",
                            "request_enqueued": False,
                            "descriptor": root_descriptor if isinstance(root_descriptor, dict) else None,
                            "resolved_store_node_id": node_id,
                            "resolved_store_path": "",
                            "diagnostics": {
                                "store_status": "missing",
                                "message": "Result computed; waiting for async persistence.",
                                "persistence_elapsed_s": elapsed_s,
                            },
                        },
                        reason="job-completed-persisting-nested",
                    )
            if transient_descriptor is not None and view_path in {"", "/"}:
                persisted_state = transient_metadata.get("persisted") if isinstance(transient_metadata, dict) else None
                persist_warning = transient_metadata.get("persist_warning") if isinstance(transient_metadata, dict) else None
                persist_error = transient_metadata.get("persist_error") if isinstance(transient_metadata, dict) else None
                if persisted_state is False or isinstance(persist_warning, dict):
                    warning_payload = persist_warning if isinstance(persist_warning, dict) else {}
                    default_message = "E_UNSPECIFIED_VALUE_TYPE: value cannot be inspected because it is not representable by voxpod/1."
                    if isinstance(persist_error, str) and persist_error.strip():
                        default_message = f"Persistence failed: {persist_error.strip()}"
                    message = str(warning_payload.get("message", default_message))
                    code = str(warning_payload.get("code", "E_PERSISTENCE_FAILED" if default_message.startswith("Persistence failed:") else "E_UNSPECIFIED_VALUE_TYPE"))
                    return _attach_and_log(
                        {
                            "available": False,
                            "materialization": "failed",
                            "compute_status": "failed",
                            "status": "failed",
                            "error": message,
                            "job_id": tracked_job.get("job_id"),
                            "store_status": "missing",
                            "request_enqueued": False,
                            "diagnostics": {
                                "code": code,
                                "message": message,
                                "node_id": node_id,
                                "path": view_path or "/",
                            },
                        },
                        reason="job-completed-persistence-failed",
                    )
                if persisted_state == "pending":
                    finished_at = _parse_iso_utc(tracked_job.get("finished_at"))
                    elapsed_s = (time.time() - finished_at) if finished_at is not None else None
                    return _attach_and_log(
                        {
                            "available": False,
                            "materialization": "pending",
                            "compute_status": "persisting",
                            "job_id": tracked_job.get("job_id"),
                            "store_status": "missing",
                            "request_enqueued": False,
                            "descriptor": transient_descriptor.get("descriptor")
                            if isinstance(transient_descriptor.get("descriptor"), dict)
                            else None,
                            "resolved_store_node_id": node_id,
                            "resolved_store_path": "",
                            "diagnostics": {
                                "store_status": "missing",
                                "message": "Result computed; waiting for async persistence.",
                                "persistence_elapsed_s": elapsed_s,
                            },
                        },
                        reason="job-completed-persisting-root",
                    )
            if not request.enqueue:
                return _attach_and_log(
                    _failure_payload(
                        compute_status="failed",
                        inspected_payload=inspected,
                        tracked_job_payload=tracked_job,
                        fallback_error=(
                            "Node evaluation finished, but no persisted value exists in the store "
                            "and no runtime preview payload was returned."
                        ),
                        request_enqueued=False,
                    ),
                    reason="job-completed-missing-without-enqueue",
                )
        if job_status in {"failed", "killed"}:
            return _attach_and_log(
                _failure_payload(
                    compute_status=job_status,
                    inspected_payload=inspected,
                    tracked_job_payload=tracked_job,
                    fallback_error="Value not materialized.",
                    request_enqueued=False,
                ),
                reason=f"job-{job_status}",
            )

    if inspected is not None and str(inspected.get("status")) == "failed":
        return _attach_and_log(
            _failure_payload(
                compute_status="failed",
                inspected_payload=inspected,
                tracked_job_payload=tracked_job,
                fallback_error="Store contains a failed value for this node.",
                request_enqueued=False,
            ),
            reason="store-failed",
        )

    if not request.enqueue:
        if inspected is not None:
            inspected["materialization"] = "store-status"
            inspected["compute_status"] = str(inspected.get("status", "unknown"))
            return _attach_and_log(inspected, reason="store-status-no-enqueue")
        descriptor = _in_progress_descriptor(
            output_kind=node_output_kind,
            path=view_path,
            status="missing",
        )
        descriptor_vox_type = str(descriptor.get("vox_type", "unavailable"))
        return _attach_and_log(
            {
                "available": False,
                "materialization": "pending" if descriptor_vox_type in {"sequence", "mapping", "overlay"} else "missing",
                "compute_status": "running" if descriptor_vox_type in {"sequence", "mapping", "overlay"} else "missing",
                "store_status": "missing",
                "request_enqueued": False,
                "descriptor": descriptor,
                "resolved_store_node_id": node_id,
                "resolved_store_path": "",
            },
            reason="missing-no-enqueue",
        )

    enqueue_started = time.perf_counter()
    value_job = playground_jobs.ensure_value_job(
        {
            "program": request.program,
            "execute": True,
            "no_cache": False,
            "execution_strategy": "dask",
            "legacy": False,
            "serve_mode": True,
            "_include_goal_descriptors": True,
            "_goals": [node_id],
            "_job_kind": "value-resolve",
            "_priority_node": node_id,
            "_priority_context": priority_context.as_payload(),
            "_program_hash": program_hash,
            "_goal_path": view_path,
            "_ui_awaited": bool(request.ui_awaited),
        },
        program_hash=program_hash,
        node_id=node_id,
        execution_strategy="dask",
    )
    enqueue_ms = (time.perf_counter() - enqueue_started) * 1000.0
    phase_timings_ms["enqueue"] = enqueue_ms
    value_logger.info(
        "[value:%s] ensure-value-job %.1fms job=%s status=%s",
        request_id,
        enqueue_ms,
        str(value_job.get("job_id", "-"))[:12],
        str(value_job.get("status", "queued")),
    )
    return _attach_and_log(
        {
            "available": False,
            "materialization": "pending",
            "compute_status": value_job.get("status", "queued"),
            "job_id": value_job.get("job_id"),
            "log_tail": value_job.get("log_tail", ""),
            "store_status": inspected.get("status") if inspected is not None else "missing",
            "request_enqueued": True,
        },
        reason="enqueued",
    )


@api_router.post("/playground/value/page")
async def playground_value_page_endpoint(request: PlaygroundValuePageRequest) -> dict[str, Any]:
    """Resolve one pageable value lazily and return one page slice."""
    program_hash = _program_hash(request.program)
    value_payload = await playground_value_endpoint(
        PlaygroundValueRequest(
            program=request.program,
            execution_strategy="dask",
            node_id=request.node_id,
            variable=request.variable,
            path=request.path,
            enqueue=bool(request.enqueue),
            ui_awaited=bool(request.ui_awaited),
            interaction=request.interaction,
        )
    )

    materialization = str(value_payload.get("materialization", "missing"))
    compute_status = str(value_payload.get("compute_status", "missing"))
    node_id = str(value_payload.get("node_id") or request.node_id or "")
    if not node_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to page value: node id could not be resolved.",
        )
    path = str(value_payload.get("path") or request.path or "")
    descriptor_payload = value_payload.get("descriptor") if isinstance(value_payload.get("descriptor"), dict) else None
    metadata_payload = value_payload.get("metadata") if isinstance(value_payload.get("metadata"), dict) else {}
    runtime_preview_page = _slice_runtime_preview_page(
        value_payload.get("runtime_preview_page") if isinstance(value_payload.get("runtime_preview_page"), dict) else None,
        offset=request.offset,
        limit=request.limit,
    )

    def _transient_sequence_page_candidate() -> dict[str, Any] | None:
        if not isinstance(descriptor_payload, dict) or str(descriptor_payload.get("vox_type", "")) != "sequence":
            return None
        container_node_id: str | None = None
        resolved_node_raw = value_payload.get("resolved_store_node_id")
        resolved_path_raw = value_payload.get("resolved_store_path")
        if isinstance(resolved_node_raw, str) and resolved_node_raw.strip():
            normalized_resolved_path = str(resolved_path_raw or "").strip()
            if normalized_resolved_path in {"", "/"}:
                container_node_id = resolved_node_raw.strip()
        if container_node_id is None:
            container_node_id = _sequence_container_node_for_path(node_id, path)
        return _transient_sequence_page_from_store(
            storage=get_storage(),
            container_node_id=container_node_id or node_id,
            descriptor=descriptor_payload,
            base_path=path,
            offset=request.offset,
            limit=request.limit,
        )

    if materialization in {"failed"} or compute_status in {"failed", "killed"}:
        return value_payload

    if runtime_preview_page is not None:
        preferred_page = _prefer_richer_page(runtime_preview_page, _transient_sequence_page_candidate())
        return {
            **value_payload,
            "path": path,
            "page": preferred_page,
            "available": bool(preferred_page and preferred_page.get("items")),
        }

    inspect_job_runtime = getattr(playground_jobs, "inspect_value_job_runtime", None)
    runtime_source = str(metadata_payload.get("source", "")).strip().lower()
    runtime_persisted_state = str(metadata_payload.get("persisted", "")).strip().lower()
    runtime_backed_page_candidate = runtime_source in {
        "runtime",
        "runtime-cache",
        "runtime-live",
        "runtime-preview",
    } or runtime_persisted_state == "pending"
    runtime_page_available = False
    runtime_priority = {
        "bucket": str(value_payload.get("priority_bucket", "visible-page") or "visible-page"),
        "urgency_score": int(value_payload.get("urgency_score", 0) or 0),
    }
    if callable(inspect_job_runtime) and (
        str(value_payload.get("store_status", "")) == "missing" or runtime_backed_page_candidate
    ):
        runtime_cached_preview = inspect_job_runtime(
            program_hash=program_hash,
            node_id=node_id,
            execution_strategy="dask",
            path=path,
            page_offset=request.offset,
            page_limit=request.limit,
            priority=runtime_priority,
        )
        runtime_cached_page = (
            runtime_cached_preview.get("page")
            if isinstance(runtime_cached_preview, dict)
            else None
        )
        if isinstance(runtime_cached_page, dict):
            runtime_page_available = True
            preferred_page = _prefer_richer_page(runtime_cached_page, _transient_sequence_page_candidate())
            updated_payload = {
                **value_payload,
                "path": str(runtime_cached_preview.get("path") or path),
                "page": preferred_page,
                "available": bool(preferred_page and preferred_page.get("items")),
            }
            runtime_cached_descriptor = runtime_cached_preview.get("descriptor")
            if isinstance(runtime_cached_descriptor, dict):
                updated_payload["descriptor"] = runtime_cached_descriptor
            return updated_payload

    if runtime_backed_page_candidate and not runtime_page_available:
        try:
            value_job = playground_jobs.ensure_value_job(
                {
                    "program": request.program,
                    "execute": True,
                    "no_cache": False,
                    "execution_strategy": "dask",
                    "legacy": False,
                    "serve_mode": True,
                    "_include_goal_descriptors": True,
                    "_goals": [node_id],
                    "_job_kind": "value-resolve",
                    "_priority_node": node_id,
                    "_priority_context": compute_priority_context(
                        job_kind="value-resolve",
                        enqueue=True,
                        ui_awaited=bool(request.ui_awaited),
                        path=path,
                        interaction=request.interaction,
                    ).as_payload(),
                    "_program_hash": program_hash,
                    "_goal_path": path,
                    "_ui_awaited": bool(request.ui_awaited),
                },
                program_hash=program_hash,
                node_id=node_id,
                execution_strategy="dask",
            )
            value_payload = {
                **value_payload,
                "job_id": value_job.get("job_id"),
                "request_enqueued": True,
                "compute_status": str(value_job.get("status", "queued") or "queued"),
                "materialization": "pending",
                "store_status": str(value_payload.get("store_status", "materialized") or "materialized"),
            }
            materialization = str(value_payload.get("materialization", "pending"))
            compute_status = str(value_payload.get("compute_status", "queued"))
        except Exception:
            pass

    if (
        materialization in {"pending", "missing"}
        or compute_status in {"queued", "running", "persisting", "missing"}
        or runtime_backed_page_candidate
    ):
        transient_page = _transient_sequence_page_candidate()
        if transient_page is not None:
            return {
                **value_payload,
                "path": path,
                "page": transient_page,
                "available": bool(transient_page.get("items")),
            }
        return {
            **value_payload,
            "page": {
                "offset": int(max(0, request.offset)),
                "limit": int(max(1, request.limit)),
                "items": [],
                "next_offset": None,
                "has_more": False,
            },
        }

    inspect_node_id = node_id
    inspect_path = path
    rebase_sequence_path: str | None = None
    if isinstance(descriptor_payload, dict) and str(descriptor_payload.get("vox_type", "")) == "sequence" and path not in {"", "/"}:
        resolved_node_raw = value_payload.get("resolved_store_node_id")
        resolved_path_raw = value_payload.get("resolved_store_path")
        if isinstance(resolved_node_raw, str) and resolved_node_raw.strip():
            normalized_resolved_path = str(resolved_path_raw or "").strip()
            if normalized_resolved_path in {"", "/"}:
                inspect_node_id = resolved_node_raw.strip()
                inspect_path = ""
        if inspect_node_id == node_id:
            container_node_id = _sequence_container_node_for_path(node_id, path)
            if container_node_id is not None:
                inspect_node_id = container_node_id
                inspect_path = ""
        if inspect_path in {"", "/"}:
            rebase_sequence_path = path

    try:
        paged = inspect_store_result_page(
            get_storage(),
            node_id=inspect_node_id,
            path=inspect_path,
            offset=request.offset,
            limit=request.limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedVoxValueError as exc:
        return {
            **value_payload,
            "available": False,
            "materialization": "failed",
            "compute_status": "failed",
            "error": str(exc),
            "diagnostics": {
                "code": exc.code,
                "message": str(exc),
                "node_id": node_id,
                "path": path or "/",
            },
        }
    except (ValueError, VoxValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if rebase_sequence_path is not None:
        paged["path"] = rebase_sequence_path
        descriptor = paged.get("descriptor")
        if isinstance(descriptor, dict):
            navigation = descriptor.get("navigation")
            if isinstance(navigation, dict):
                navigation["path"] = rebase_sequence_path
        page_payload = paged.get("page")
        if isinstance(page_payload, dict):
            items = page_payload.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        item_index = int(item.get("index"))
                    except Exception:
                        continue
                    rebased_item_path = _append_sequence_index_path(rebase_sequence_path, item_index)
                    item["path"] = rebased_item_path
                    item_descriptor = item.get("descriptor")
                    if isinstance(item_descriptor, dict):
                        item_navigation = item_descriptor.get("navigation")
                        if isinstance(item_navigation, dict):
                            item_navigation["path"] = rebased_item_path

    return {
        **value_payload,
        "descriptor": paged.get("descriptor"),
        "page": paged.get("page"),
        "path": paged.get("path", value_payload.get("path", path)),
        "available": True,
    }


@api_router.post("/playground/jobs")
async def create_playground_job_endpoint(request: RunRequest) -> dict[str, Any]:
    """Start an asynchronous playground execution job."""
    payload = _prepare_serve_run_payload(request)
    payload["execution_strategy"] = "dask"
    if bool(request.background_fill):
        payload["_background_fill"] = True
        payload["_job_kind"] = "background-fill"
        payload["_include_goal_descriptors"] = True
        payload["_goals"] = _background_fill_goal_ids(str(request.program or ""))
    return playground_jobs.create_job(payload)


@api_router.get("/playground/jobs")
async def list_playground_jobs_endpoint() -> dict[str, Any]:
    """List recent playground jobs and statuses."""
    return playground_jobs.list_jobs()


@api_router.get("/playground/jobs/{job_id}")
async def get_playground_job_endpoint(job_id: str) -> dict[str, Any]:
    """Get current status and result for a playground job."""
    payload = playground_jobs.get_job(job_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown playground job: {job_id}",
        )
    return payload


@api_router.delete("/playground/jobs/{job_id}")
async def kill_playground_job_endpoint(job_id: str) -> dict[str, Any]:
    """Kill a running playground job and return terminal state."""
    payload = playground_jobs.kill_job(job_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown playground job: {job_id}",
        )
    return payload


@api_router.get("/testing/report")
async def testing_report_endpoint() -> dict[str, Any]:
    """Return aggregated test, coverage, and performance report snapshot."""
    return build_test_dashboard_snapshot()


@api_router.post("/testing/jobs")
async def create_testing_job_endpoint(request: TestRunRequest) -> dict[str, Any]:
    """Start an asynchronous test run job from the web UI."""
    try:
        return testing_jobs.create_job(
            profile=request.profile,
            include_perf=bool(request.include_perf),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@api_router.get("/testing/jobs")
async def list_testing_jobs_endpoint() -> dict[str, Any]:
    """List recent test jobs."""
    return testing_jobs.list_jobs()


@api_router.get("/testing/jobs/{job_id}")
async def get_testing_job_endpoint(job_id: str) -> dict[str, Any]:
    """Get a test job status with recent log tail."""
    payload = testing_jobs.get_job(job_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown testing job: {job_id}",
        )
    return payload


@api_router.delete("/testing/jobs/{job_id}")
async def kill_testing_job_endpoint(job_id: str) -> dict[str, Any]:
    """Kill a running test job."""
    payload = testing_jobs.kill_job(job_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown testing job: {job_id}",
        )
    return payload


@api_router.get("/testing/performance/chart")
async def testing_performance_chart_endpoint() -> FileResponse:
    """Serve the latest VoxLogicA-1 vs VoxLogicA-2 performance chart."""
    if not PERF_REPORT_SVG.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Performance chart not found: {PERF_REPORT_SVG}",
        )
    return FileResponse(str(PERF_REPORT_SVG), media_type="image/svg+xml")


@api_router.get("/testing/performance/primitive-chart")
async def testing_performance_primitive_chart_endpoint() -> FileResponse:
    """Serve per-primitive benchmark histogram chart."""
    primitive_chart = PERF_REPORT_DIR / "primitive_benchmarks.svg"
    if not primitive_chart.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Primitive benchmark chart not found: {primitive_chart}",
        )
    return FileResponse(str(primitive_chart), media_type="image/svg+xml")


@api_router.get("/storage/stats")
async def storage_stats_endpoint(refresh: bool = Query(default=False)) -> dict[str, Any]:
    """Return lightweight non-blocking cache/storage statistics."""
    return get_lightweight_storage_stats_snapshot(get_storage(), force_refresh=bool(refresh))


@api_router.get("/results/store")
async def list_store_results_endpoint(
    limit: int = Query(default=120, ge=1, le=300),
    status_filter: str | None = Query(default=None),
    node_filter: str | None = Query(default=None),
) -> dict[str, Any]:
    """List cached result records available for UI inspection."""
    return list_store_results_snapshot(
        get_storage(),
        limit=limit,
        status_filter=status_filter,
        node_filter=node_filter,
    )


@api_router.get("/results/store/{node_id}")
async def inspect_store_result_endpoint(
    node_id: str,
    path: str | None = Query(default=None),
) -> dict[str, Any]:
    """Inspect one stored result record (optionally selecting a sub-path)."""
    try:
        return inspect_store_result(get_storage(), node_id=node_id, path=path or "")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedVoxValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": exc.code,
                "message": str(exc),
                "node_id": node_id,
                "path": path or "/",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@api_router.get("/results/store/{node_id}/page")
async def inspect_store_result_page_endpoint(
    node_id: str,
    path: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=64, ge=1, le=512),
) -> dict[str, Any]:
    """Inspect a pageable stored result record using lazy page access."""
    try:
        return inspect_store_result_page(
            get_storage(),
            node_id=node_id,
            path=path or "",
            offset=offset,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedVoxValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": exc.code,
                "message": str(exc),
                "node_id": node_id,
                "path": path or "/",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@api_router.get("/results/store/{node_id}/render/png")
async def render_store_png_endpoint(
    node_id: str,
    path: str | None = Query(default=None),
) -> Response:
    """Render one store record (or nested value) as a PNG image."""
    try:
        payload = render_store_result_png(get_storage(), node_id=node_id, path=path or "")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedVoxValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc), "node_id": node_id, "path": path or "/"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return Response(content=payload, media_type="image/png")


@api_router.get("/results/store/{node_id}/render/nii.gz")
async def render_store_nifti_endpoint(
    node_id: str,
    path: str | None = Query(default=None),
) -> Response:
    """Render one store record (or nested value) as gzipped NIfTI."""
    try:
        payload = render_store_result_nifti_gz(get_storage(), node_id=node_id, path=path or "")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedVoxValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc), "node_id": node_id, "path": path or "/"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    headers = {
        "Content-Disposition": f'inline; filename="{node_id}.nii.gz"',
        "Cache-Control": "no-store",
    }
    return Response(content=payload, media_type="application/gzip", headers=headers)


@api_router.get("/results/store/{node_id}/render/nii")
async def render_store_nifti_uncompressed_endpoint(
    node_id: str,
    path: str | None = Query(default=None),
) -> Response:
    """Render one store record (or nested value) as uncompressed NIfTI."""
    try:
        payload = render_store_result_nifti(get_storage(), node_id=node_id, path=path or "")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedVoxValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc), "node_id": node_id, "path": path or "/"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    headers = {
        "Content-Disposition": f'inline; filename="{node_id}.nii"',
        "Cache-Control": "no-store",
    }
    return Response(content=payload, media_type="application/octet-stream", headers=headers)


@api_app.get("/")
async def root() -> HTMLResponse:
    """Serve interactive visualization page with cache-busted asset references."""
    index_path = Path(__file__).parent / "static" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visualization page not found")
    try:
        html = index_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unable to read UI page: {exc}") from exc
    html = html.replace(_ASSET_REV_SENTINEL, _compute_static_asset_revision(index_path.parent))
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@api_app.websocket("/ws/playground/value")
async def playground_value_stream_endpoint(websocket: WebSocket) -> None:
    """Push focused value snapshots over WebSocket with subscription updates."""
    await websocket.accept()
    stream_logger = logging.getLogger("voxlogica.ws.value")

    active_request: PlaygroundValueRequest | PlaygroundValuePageRequest | None = None
    active_mode = "value"
    first_tick = True
    last_signature = ""

    def _parse_subscribe_payload(raw_message: str) -> tuple[str, PlaygroundValueRequest | PlaygroundValuePageRequest] | None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        message_type = str(payload.get("type", "subscribe")).strip().lower()
        if message_type not in {"subscribe", "sub"}:
            return None
        request_payload = payload.get("request", payload)
        if not isinstance(request_payload, dict):
            return None
        requested_mode = str(payload.get("mode", "value")).strip().lower()
        try:
            if requested_mode == "page":
                return ("page", PlaygroundValuePageRequest(**request_payload))
            return ("value", PlaygroundValueRequest(**request_payload))
        except Exception:
            return None

    async def _await_subscription_or_change(payload: dict[str, Any]) -> tuple[str, str | None]:
        if active_request is None:
            return ("idle", None)
        if active_mode != "page":
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.8)
                return ("message", message)
            except TimeoutError:
                return ("tick", None)
            except asyncio.TimeoutError:
                return ("tick", None)
        sequence_version = _payload_sequence_version(payload)
        if sequence_version is None:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.8)
                return ("message", message)
            except TimeoutError:
                return ("tick", None)
            except asyncio.TimeoutError:
                return ("tick", None)
        await asyncio.to_thread(
            playground_jobs.wait_for_value_job_runtime_change,
            program_hash=_program_hash(active_request.program),
            node_id=str(payload.get("node_id", active_request.node_id or "")),
            execution_strategy="dask",
            path=active_request.path or "",
            since_version=sequence_version,
            timeout=15.0,
        )
        return ("tick", None)

    try:
        while True:
            if active_request is None:
                subscribe_message = await websocket.receive_text()
                parsed = _parse_subscribe_payload(subscribe_message)
                if parsed is None:
                    await websocket.send_json({"type": "error", "message": "Invalid subscribe payload."})
                    continue
                active_mode, active_request = parsed
                first_tick = True
                last_signature = ""
                await websocket.send_json({"type": "subscribed", "mode": active_mode})
                continue

            enqueue_now = bool(active_request.enqueue) if first_tick else False
            first_tick = False
            if active_mode == "page":
                payload = await playground_value_page_endpoint(
                    PlaygroundValuePageRequest(
                        program=active_request.program,
                        execution_strategy="dask",
                        node_id=active_request.node_id,
                        variable=active_request.variable,
                        path=active_request.path,
                        offset=active_request.offset,
                        limit=active_request.limit,
                        enqueue=enqueue_now,
                        ui_awaited=bool(active_request.ui_awaited),
                        interaction=active_request.interaction,
                    )
                )
            else:
                payload = await playground_value_endpoint(
                    PlaygroundValueRequest(
                        program=active_request.program,
                        execution_strategy="dask",
                        node_id=active_request.node_id,
                        variable=active_request.variable,
                        path=active_request.path,
                        enqueue=enqueue_now,
                        ui_awaited=bool(active_request.ui_awaited),
                        interaction=active_request.interaction,
                    )
                )
            signature = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            if signature != last_signature:
                await websocket.send_json({"type": active_mode, "payload": payload})
                last_signature = signature
            if _is_terminal_value_payload(payload):
                await websocket.send_json({"type": "terminal", "mode": active_mode, "payload": payload})
                active_request = None
                active_mode = "value"
                first_tick = True
                last_signature = ""

            try:
                wait_kind, message = await _await_subscription_or_change(payload)
                if wait_kind != "message":
                    continue
                parsed = _parse_subscribe_payload(message)
                if parsed is not None:
                    active_mode, active_request = parsed
                    first_tick = True
                    last_signature = ""
                    await websocket.send_json({"type": "subscribed", "mode": active_mode})
            except TimeoutError:
                continue
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        stream_logger.error("playground value ws failed: %s", exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="playground value ws failed")


@api_app.websocket("/ws/playground/jobs")
async def playground_job_stream_endpoint(websocket: WebSocket) -> None:
    """Push playground job status snapshots over WebSocket with auto-resubscribe support."""
    await websocket.accept()
    stream_logger = logging.getLogger("voxlogica.ws.jobs")

    job_id = ""
    last_signature = ""

    def _parse_job_subscribe(raw_message: str) -> str:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return ""
        if not isinstance(payload, dict):
            return ""
        message_type = str(payload.get("type", "subscribe")).strip().lower()
        if message_type not in {"subscribe", "sub"}:
            return ""
        raw_job_id = payload.get("job_id")
        return str(raw_job_id or "").strip()

    try:
        while True:
            if not job_id:
                subscribe_message = await websocket.receive_text()
                selected_job = _parse_job_subscribe(subscribe_message)
                if not selected_job:
                    await websocket.send_json({"type": "error", "message": "Invalid job subscribe payload."})
                    continue
                job_id = selected_job
                last_signature = ""
                await websocket.send_json({"type": "subscribed", "job_id": job_id})
                continue

            payload = playground_jobs.get_job(job_id)
            if payload is None:
                await websocket.send_json({"type": "error", "job_id": job_id, "message": "Unknown playground job."})
                job_id = ""
                last_signature = ""
                continue

            signature = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            if signature != last_signature:
                await websocket.send_json({"type": "job", "job_id": job_id, "payload": payload})
                last_signature = signature

            status_name = str(payload.get("status", "")).lower()
            if status_name in {"completed", "failed", "killed"}:
                await websocket.send_json({"type": "terminal", "job_id": job_id, "payload": payload})
                job_id = ""
                last_signature = ""

            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.8)
                selected_job = _parse_job_subscribe(message)
                if selected_job:
                    job_id = selected_job
                    last_signature = ""
                    await websocket.send_json({"type": "subscribed", "job_id": job_id})
            except TimeoutError:
                continue
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        stream_logger.error("playground job ws failed: %s", exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="playground job ws failed")


@api_app.websocket("/livereload")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for live-reload and browser console forwarding."""
    await websocket.accept()
    live_reload_clients.add(websocket)
    browser_logger = logging.getLogger("voxlogica.browser")

    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
                if isinstance(payload, dict) and "type" in payload and "message" in payload:
                    msg_type = payload["type"]
                    msg_content = payload["message"]
                    if msg_type == "error":
                        browser_logger.error("[BROWSER ERROR] %s", msg_content)
                    elif msg_type == "warn":
                        browser_logger.warning("[BROWSER WARN] %s", msg_content)
                    else:
                        browser_logger.info("[BROWSER] %s", msg_content)
            except json.JSONDecodeError:
                continue
    except WebSocketDisconnect:
        live_reload_clients.discard(websocket)


api_app.include_router(api_router)


@app.command()
def dev(
    backend_host: str = typer.Option("127.0.0.1", help="Backend host"),
    backend_port: int = typer.Option(8000, help="Backend port"),
    frontend_host: str = typer.Option("127.0.0.1", help="Vite frontend host"),
    frontend_port: int = typer.Option(5173, help="Vite frontend port"),
    debug: bool = typer.Option(False, "--debug", help="Enable verbose backend logging"),
    backend_watch: bool = typer.Option(True, "--backend-watch/--no-backend-watch", help="Auto-restart backend on Python-side file changes"),
) -> None:
    """Run backend API and Vite frontend together with one supervisor process."""

    setup_logging(debug=debug, verbose=False)
    repo_root = Path(__file__).resolve().parents[3]
    ui_dir = repo_root / "implementation" / "ui"
    if not ui_dir.exists():
        logger.error("UI workspace not found at %s", ui_dir)
        raise typer.Exit(code=1)

    npm_bin = shutil.which("npm")
    if npm_bin is None:
        logger.error("npm is required for dev mode but was not found in PATH.")
        raise typer.Exit(code=1)

    env = dict(os.environ)
    backend_origin = f"http://{backend_host}:{backend_port}"
    frontend_origin = f"http://{frontend_host}:{frontend_port}"
    env["VOXLOGICA_DEV_BACKEND_URL"] = backend_origin
    local_python_root = str((repo_root / "implementation" / "python").resolve())
    existing_pythonpath = str(env.get("PYTHONPATH", "")).strip()
    if existing_pythonpath:
        python_entries = existing_pythonpath.split(os.pathsep)
        if local_python_root not in python_entries:
            env["PYTHONPATH"] = os.pathsep.join([local_python_root, existing_pythonpath])
    else:
        env["PYTHONPATH"] = local_python_root

    node_modules = ui_dir / "node_modules"
    if not node_modules.exists():
        logger.info("Installing UI dependencies in %s", ui_dir)
        try:
            subprocess.run([npm_bin, "install", "--no-audit", "--no-fund"], cwd=ui_dir, check=True, env=env)
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to install UI dependencies (exit=%s).", exc.returncode)
            raise typer.Exit(code=1) from exc

    backend_cmd = [
        sys.executable,
        "-m",
        "voxlogica.main",
        "serve",
        "--host",
        backend_host,
        "--port",
        str(backend_port),
        "--no-build-ui",
    ]
    if debug:
        backend_cmd.append("--debug")

    frontend_cmd = [
        npm_bin,
        "run",
        "dev",
        "--",
        "--host",
        frontend_host,
        "--port",
        str(frontend_port),
        "--strictPort",
    ]

    logger.info("Starting backend at %s", backend_origin)
    logger.info("Starting Vite frontend at %s", frontend_origin)
    backend_proc: subprocess.Popen[str] | None = None
    frontend_proc: subprocess.Popen[str] | None = None
    backend_watch_observer: BaseObserver | None = None
    exit_code = 0
    command_queue: queue.SimpleQueue[str] = queue.SimpleQueue()

    def _print_dev_help() -> None:
        logger.info("[dev stdin] commands: h/help/? | o/open | rb (restart backend) | rf (restart frontend) | r (restart both) | q/quit")

    def _on_dev_command(command: str) -> None:
        command_queue.put(str(command or "").strip().lower())

    bridge = _stdin_command_bridge(on_command=_on_dev_command, label="dev")
    try:
        backend_proc = subprocess.Popen(backend_cmd, cwd=repo_root, env=env)
        frontend_proc = subprocess.Popen(frontend_cmd, cwd=ui_dir, env=env)
        if backend_watch:
            backend_watch_observer = _start_dev_backend_watcher(
                repo_root,
                lambda changed_path: _on_dev_command(f"backend-dirty:{changed_path}"),
            )
            if backend_watch_observer is not None:
                logger.info("Watching backend sources for restart: %s", repo_root / "implementation" / "python")
        logger.info("Dev mode ready: open %s", frontend_origin)
        logger.info("Press Ctrl+C to stop both backend and frontend.")
        _print_dev_help()
        while True:
            restart_backend = False
            restart_frontend = False
            auto_backend_changes: set[str] = set()
            while True:
                try:
                    command = command_queue.get_nowait()
                except queue.Empty:
                    break
                if command in {"h", "help", "?"}:
                    _print_dev_help()
                elif command in {"o", "open"}:
                    webbrowser.open(frontend_origin)
                    logger.info("[dev stdin] opened %s", frontend_origin)
                elif command in {"rb", "restart-backend"}:
                    restart_backend = True
                elif command in {"rf", "restart-frontend"}:
                    restart_frontend = True
                elif command in {"r", "restart"}:
                    restart_backend = True
                    restart_frontend = True
                elif command.startswith("backend-dirty:"):
                    changed_path = command.split(":", 1)[1].strip()
                    if changed_path:
                        auto_backend_changes.add(changed_path)
                elif command in {"q", "quit", "exit"}:
                    logger.info("[dev stdin] quit requested")
                    exit_code = 0
                    raise KeyboardInterrupt
                else:
                    logger.info("[dev stdin] unknown command '%s' (type 'h' for help)", command)

            if restart_frontend:
                _terminate_child_process(frontend_proc, name="frontend")
                frontend_proc = subprocess.Popen(frontend_cmd, cwd=ui_dir, env=env)
                if restart_backend:
                    logger.info("[dev stdin] frontend restarted")
                else:
                    logger.info("[dev stdin] frontend restarted")

            if restart_backend or auto_backend_changes:
                _terminate_child_process(backend_proc, name="backend")
                backend_proc = subprocess.Popen(backend_cmd, cwd=repo_root, env=env)
                if auto_backend_changes and not restart_backend:
                    changed = sorted(auto_backend_changes)
                    lead = changed[0]
                    if len(changed) == 1:
                        logger.info("[dev watch] backend restarted after change in %s", lead)
                    else:
                        logger.info("[dev watch] backend restarted after %s changes; latest %s", len(changed), lead)
                elif restart_frontend:
                    logger.info("[dev stdin] backend+frontend restarted")
                else:
                    logger.info("[dev stdin] backend restarted")

            backend_status = backend_proc.poll()
            frontend_status = frontend_proc.poll()
            if backend_status is not None:
                logger.error("Backend process exited unexpectedly with code %s.", backend_status)
                exit_code = backend_status if backend_status != 0 else 1
                break
            if frontend_status is not None:
                logger.error("Frontend process exited unexpectedly with code %s.", frontend_status)
                exit_code = frontend_status if frontend_status != 0 else 1
                break
            time.sleep(0.4)
    except KeyboardInterrupt:
        logger.info("Stopping dev mode...")
        exit_code = 0
    finally:
        if bridge is not None:
            bridge[0].set()
        _stop_dev_backend_watcher(backend_watch_observer)
        _terminate_child_process(frontend_proc, name="frontend")
        _terminate_child_process(backend_proc, name="backend")

    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@mcp_app.command("ui-inspector")
def mcp_ui_inspector(
    url: Optional[str] = typer.Option(None, "--url", help="Initial page URL for the inspector browser."),
    headed: bool = typer.Option(False, "--headed", help="Run the browser with a visible window."),
    browser_channel: Optional[str] = typer.Option(
        None,
        "--browser-channel",
        help="Optional Playwright browser channel, for example 'chrome'.",
    ),
    viewport_width: int = typer.Option(1440, help="Browser viewport width"),
    viewport_height: int = typer.Option(900, help="Browser viewport height"),
) -> None:
    """Start the Playwright-backed UI inspector MCP server over stdio."""

    setup_logging(False, False)
    from voxlogica.mcp_server import run_ui_inspector_mcp_server

    run_ui_inspector_mcp_server(
        start_url=url,
        headless=not headed,
        browser_channel=browser_channel,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind the API server"),
    port: int = typer.Option(8000, help="Port to bind the API server"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
    build_ui: bool = typer.Option(True, "--build-ui/--no-build-ui", help="Build Svelte UI assets before starting server"),
    force_ui_build: bool = typer.Option(False, "--force-ui-build", help="Force a UI rebuild even when assets appear fresh"),
) -> None:
    """Start VoxLogicA API server."""

    setup_logging(debug=debug, verbose=False)
    try:
        _build_ui_assets_if_needed(build_enabled=build_ui, force_build=force_ui_build)
    except RuntimeError as exc:
        logger.error("Unable to prepare UI assets: %s", exc)
        raise typer.Exit(code=1) from exc
    app_url = f"http://{host}:{port}/"
    docs_url = f"http://{host}:{port}/docs"
    logger.info("Starting VoxLogicA API server version %s on %s:%s", get_version(), host, port)
    logger.info("Interactive graph visualizer at %s", app_url)
    logger.info("API docs available at %s", docs_url)

    current_server: dict[str, uvicorn.Server | None] = {"server": None}
    restart_requested = False

    def _print_serve_help() -> None:
        logger.info("[serve stdin] commands: h/help/? | o/open | d/docs | r/reload (restart backend) | q/quit")

    def _on_serve_command(command: str) -> None:
        nonlocal restart_requested
        normalized = str(command or "").strip().lower()
        if normalized in {"h", "help", "?"}:
            _print_serve_help()
            return
        if normalized in {"o", "open"}:
            webbrowser.open(app_url)
            logger.info("[serve stdin] opened %s", app_url)
            return
        if normalized in {"d", "docs"}:
            webbrowser.open(docs_url)
            logger.info("[serve stdin] opened %s", docs_url)
            return
        if normalized in {"r", "reload", "restart"}:
            restart_requested = True
            server = current_server.get("server")
            if server is not None:
                server.should_exit = True
                logger.info("[serve stdin] restart requested")
            return
        if normalized in {"q", "quit", "exit"}:
            restart_requested = False
            server = current_server.get("server")
            if server is not None:
                server.should_exit = True
                logger.info("[serve stdin] shutdown requested")
            return
        logger.info("[serve stdin] unknown command '%s' (type 'h' for help)", normalized)

    bridge = _stdin_command_bridge(on_command=_on_serve_command, label="serve")
    _print_serve_help()
    try:
        while True:
            config = uvicorn.Config(api_app, host=host, port=port)
            server = uvicorn.Server(config)
            current_server["server"] = server
            server.run()
            current_server["server"] = None
            if not restart_requested:
                break
            logger.info("[serve stdin] restarting API server...")
            restart_requested = False
    finally:
        if bridge is not None:
            bridge[0].set()


if __name__ == "__main__":
    app()
