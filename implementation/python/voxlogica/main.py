"""VoxLogicA CLI and API entrypoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
import time
from typing import Any, Optional, TypeVar, Generic

import dask
import typer
import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from voxlogica.features import FeatureRegistry, OperationResult, handle_list_primitives
from voxlogica.repl import run_interactive_repl
from voxlogica.serve_support import (
    PERF_REPORT_SVG,
    PERF_REPORT_DIR,
    PlaygroundJobManager,
    TestingJobManager,
    build_storage_stats_snapshot,
    build_test_dashboard_snapshot,
    inspect_store_result,
    list_playground_programs,
    list_store_results_snapshot,
    load_gallery_document,
    load_playground_program,
    render_store_result_nifti_gz,
    render_store_result_png,
)
from voxlogica.storage import get_storage
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
        help="Execution strategy to use (dask|strict)",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Shortcut for --execution-strategy strict (useful for parity/debug tests)",
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

    chosen_strategy = "strict" if strict else execution_strategy
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
        help="Execution strategy to use (dask|strict)",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Shortcut for --execution-strategy strict (useful for parity/debug tests)",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging"),
) -> None:
    """Start interactive VoxLogicA REPL."""

    setup_logging(debug, verbose)
    chosen_strategy = "strict" if strict else execution_strategy
    exit_code = run_interactive_repl(strategy=chosen_strategy)
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

static_path = Path(__file__).parent / "static"
if static_path.exists():
    api_app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

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
        "testing_jobs": True,
        "testing_report": True,
        "storage_stats": True,
        "store_results_viewer": True,
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


@api_router.post("/playground/jobs")
async def create_playground_job_endpoint(request: RunRequest) -> dict[str, Any]:
    """Start an asynchronous playground execution job."""
    payload = _prepare_serve_run_payload(request)
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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    headers = {
        "Content-Disposition": f'inline; filename="{node_id}.nii.gz"',
        "Cache-Control": "no-store",
    }
    return Response(content=payload, media_type="application/gzip", headers=headers)


@api_app.get("/")
async def root() -> FileResponse:
    """Serve interactive visualization static page."""
    index_path = Path(__file__).parent / "static" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visualization page not found")
    return FileResponse(str(index_path))


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
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind the API server"),
    port: int = typer.Option(8000, help="Port to bind the API server"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
) -> None:
    """Start VoxLogicA API server."""

    setup_logging(debug=debug, verbose=False)
    logger.info("Starting VoxLogicA API server version %s on %s:%s", get_version(), host, port)
    logger.info("Interactive graph visualizer at http://%s:%s/", host, port)
    logger.info("API docs available at http://%s:%s/docs", host, port)
    uvicorn.run(api_app, host=host, port=port)


if __name__ == "__main__":
    app()
