"""VoxLogicA CLI and API entrypoints."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Optional, TypeVar, Generic, MutableMapping

import dask
import typer
import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from voxlogica.features import FeatureRegistry, OperationResult, handle_list_primitives
from voxlogica.parser import parse_program_content
from voxlogica.policy import (
    diagnostics_payload,
    diagnostics_from_exception,
    enforce_workplan_policy_or_raise,
    validate_workplan_policy,
)
from voxlogica.reducer import reduce_program_with_bindings
from voxlogica.repl import run_interactive_repl
from voxlogica.serve_support import (
    PERF_REPORT_SVG,
    PERF_REPORT_DIR,
    PlaygroundJobManager,
    TestingJobManager,
    build_storage_stats_snapshot,
    build_test_dashboard_snapshot,
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
from voxlogica.value_model import UnsupportedVoxValueError
from voxlogica.version import get_version
from voxlogica.converters.json_converter import WorkPlanJSONEncoder


T = TypeVar("T")
logger = logging.getLogger("voxlogica.main")


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


def _program_introspection(
    program_text: str,
    *,
    legacy: bool = False,
    serve_mode: bool = False,
    enforce_policy: bool = True,
) -> tuple[Any, dict[str, str], list[dict[str, str]]]:
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

    handler = logging.StreamHandler()
    handler.setFormatter(ElapsedMsFormatter("%(elapsed)s %(message)s"))

    root = logging.getLogger()
    root.handlers = []
    root.addHandler(handler)
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
        "testing_jobs": True,
        "testing_report": True,
        "storage_stats": True,
        "store_results_viewer": True,
        "store_results_paging": True,
        "gallery": True,
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
    return {
        "available": True,
        "program_hash": _program_hash(request.program),
        "operations": len(workplan.operations),
        "goals": len(workplan.goals),
        "symbol_table": symbol_table,
        "print_targets": print_targets,
        "diagnostics": diagnostics_payload(diagnostics),
    }


@api_router.post("/playground/value")
async def playground_value_endpoint(request: PlaygroundValueRequest) -> dict[str, Any]:
    """Resolve one node lazily with cache-first lookup and prioritized on-demand execution."""
    try:
        workplan, symbol_table, _print_targets = _program_introspection(
            request.program,
            legacy=False,
            serve_mode=True,
            enforce_policy=False,
        )
    except Exception as exc:  # noqa: BLE001
        diagnostics = diagnostics_from_exception(exc)
        detail = diagnostics[0]["message"] if diagnostics else f"Unable to parse program: {exc}"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    node_id, variable_name = _resolve_requested_node(
        symbol_table=symbol_table,
        node_id=request.node_id,
        variable=request.variable,
        allowed_nodes=set(workplan.nodes.keys()),
    )
    strategy = "dask"
    view_path = request.path or ""
    storage = get_storage()

    def _attach_common(payload: dict[str, Any]) -> dict[str, Any]:
        payload["node_id"] = node_id
        payload["path"] = view_path
        payload["execution_strategy"] = strategy
        if variable_name:
            payload["variable"] = variable_name
        return payload

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

    def _runtime_descriptor_from_job(
        job_payload: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not isinstance(job_payload, dict):
            return None, {}
        result_payload = job_payload.get("result")
        if not isinstance(result_payload, dict):
            return None, {}
        goal_results = result_payload.get("goal_results")
        if not isinstance(goal_results, list):
            return None, {}
        for goal_result in goal_results:
            if not isinstance(goal_result, dict):
                continue
            if str(goal_result.get("node_id", "")) != node_id:
                continue
            descriptor = goal_result.get("runtime_descriptor")
            metadata = goal_result.get("metadata")
            if isinstance(descriptor, dict):
                return descriptor, metadata if isinstance(metadata, dict) else {}
            return None, metadata if isinstance(metadata, dict) else {}
        return None, {}

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

    inspected: dict[str, Any] | None = None
    try:
        inspected = inspect_store_result(storage, node_id=node_id, path=view_path)
    except UnsupportedVoxValueError as exc:
        return _attach_common(
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
            }
        )
    except KeyError:
        inspected = None

    if inspected is not None and str(inspected.get("status")) == "materialized":
        inspected["materialization"] = "cached"
        inspected["compute_status"] = "cached"
        return _attach_common(inspected)

    program_hash = _program_hash(request.program)
    tracked_job = playground_jobs.get_value_job(
        program_hash=program_hash,
        node_id=node_id,
        execution_strategy=strategy,
    )

    if tracked_job is not None:
        job_status = str(tracked_job.get("status", "unknown"))
        if job_status in {"queued", "running"}:
            return _attach_common(
                {
                    "available": False,
                    "materialization": "pending",
                    "compute_status": job_status,
                    "job_id": tracked_job.get("job_id"),
                    "log_tail": tracked_job.get("log_tail", ""),
                    "store_status": inspected.get("status") if inspected is not None else "missing",
                    "request_enqueued": False,
                }
            )
        if job_status == "completed":
            try:
                inspected = inspect_store_result(storage, node_id=node_id, path=view_path)
                if str(inspected.get("status")) == "materialized":
                    inspected["materialization"] = "computed"
                    inspected["compute_status"] = "completed"
                    inspected["job_id"] = tracked_job.get("job_id")
                    return _attach_common(inspected)
            except UnsupportedVoxValueError as exc:
                return _attach_common(
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
                    }
                )
            except KeyError:
                inspected = None
            transient_descriptor, transient_metadata = _runtime_descriptor_from_job(tracked_job)
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
                    return _attach_common(
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
                        }
                    )
                if persisted_state == "pending":
                    finished_at = _parse_iso_utc(tracked_job.get("finished_at"))
                    if finished_at is not None and (time.time() - finished_at) > 4.0:
                        return _attach_common(
                            _failure_payload(
                                compute_status="failed",
                                inspected_payload=inspected,
                                tracked_job_payload=tracked_job,
                                fallback_error=(
                                    "Value computation completed, but persistence did not finish "
                                    "within the expected window."
                                ),
                                request_enqueued=False,
                            )
                        )
                    return _attach_common(
                        {
                            "available": False,
                            "materialization": "pending",
                            "compute_status": "persisting",
                            "job_id": tracked_job.get("job_id"),
                            "store_status": "missing",
                            "request_enqueued": False,
                            "diagnostics": {
                                "store_status": "missing",
                                "message": "Result computed; waiting for async persistence.",
                            },
                        }
                    )
            if not request.enqueue:
                return _attach_common(
                    _failure_payload(
                        compute_status="failed",
                        inspected_payload=inspected,
                        tracked_job_payload=tracked_job,
                        fallback_error=(
                            "Node evaluation finished, but no persisted value exists in the store "
                            "and no runtime preview payload was returned."
                        ),
                        request_enqueued=False,
                    )
                )
        if job_status in {"failed", "killed"}:
            return _attach_common(
                _failure_payload(
                    compute_status=job_status,
                    inspected_payload=inspected,
                    tracked_job_payload=tracked_job,
                    fallback_error="Value not materialized.",
                    request_enqueued=False,
                )
            )

    if inspected is not None and str(inspected.get("status")) == "failed":
        return _attach_common(
            _failure_payload(
                compute_status="failed",
                inspected_payload=inspected,
                tracked_job_payload=tracked_job,
                fallback_error="Store contains a failed value for this node.",
                request_enqueued=False,
            )
        )

    if not request.enqueue:
        if inspected is not None:
            inspected["materialization"] = "store-status"
            inspected["compute_status"] = str(inspected.get("status", "unknown"))
            return _attach_common(inspected)
        return _attach_common(
            {
                "available": False,
                "materialization": "missing",
                "compute_status": "missing",
                "store_status": "missing",
                "request_enqueued": False,
            }
        )

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
            "_program_hash": program_hash,
        },
        program_hash=program_hash,
        node_id=node_id,
        execution_strategy="dask",
    )
    return _attach_common(
        {
            "available": False,
            "materialization": "pending",
            "compute_status": value_job.get("status", "queued"),
            "job_id": value_job.get("job_id"),
            "log_tail": value_job.get("log_tail", ""),
            "store_status": inspected.get("status") if inspected is not None else "missing",
            "request_enqueued": True,
        }
    )


@api_router.post("/playground/value/page")
async def playground_value_page_endpoint(request: PlaygroundValuePageRequest) -> dict[str, Any]:
    """Resolve one pageable value lazily and return one page slice."""
    value_payload = await playground_value_endpoint(
        PlaygroundValueRequest(
            program=request.program,
            execution_strategy="dask",
            node_id=request.node_id,
            variable=request.variable,
            path=request.path,
            enqueue=bool(request.enqueue),
        )
    )

    materialization = str(value_payload.get("materialization", "missing"))
    compute_status = str(value_payload.get("compute_status", "missing"))
    if materialization in {"pending", "missing"} or compute_status in {"queued", "running", "persisting", "missing"}:
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
    if materialization in {"failed"} or compute_status in {"failed", "killed"}:
        return value_payload

    node_id = str(value_payload.get("node_id") or request.node_id or "")
    if not node_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to page value: node id could not be resolved.",
        )
    path = request.path or ""
    try:
        paged = inspect_store_result_page(
            get_storage(),
            node_id=node_id,
            path=path,
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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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
async def storage_stats_endpoint() -> dict[str, Any]:
    """Return persistent cache/storage statistics."""
    return build_storage_stats_snapshot(get_storage())


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
    exit_code = 0
    try:
        backend_proc = subprocess.Popen(backend_cmd, cwd=repo_root, env=env)
        frontend_proc = subprocess.Popen(frontend_cmd, cwd=ui_dir, env=env)
        logger.info("Dev mode ready: open %s", frontend_origin)
        logger.info("Press Ctrl+C to stop both backend and frontend.")
        while True:
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
        _terminate_child_process(frontend_proc, name="frontend")
        _terminate_child_process(backend_proc, name="backend")

    if exit_code != 0:
        raise typer.Exit(code=exit_code)


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
    logger.info("Starting VoxLogicA API server version %s on %s:%s", get_version(), host, port)
    logger.info("Interactive graph visualizer at http://%s:%s/", host, port)
    logger.info("API docs available at http://%s:%s/docs", host, port)
    uvicorn.run(api_app, host=host, port=port)


if __name__ == "__main__":
    app()
