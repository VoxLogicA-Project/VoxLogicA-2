"""
VoxLogicA Main module - Python implementation
"""

import asyncio
import contextlib
import logging
import dask
from distributed import Client
import sys
from pathlib import Path
from typing import Any, Optional, TypeVar, Generic
import traceback
import time

import typer
import uvicorn
from fastapi import FastAPI, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from voxlogica.features import FeatureRegistry
from voxlogica.version import get_version
from voxlogica.converters.json_converter import WorkPlanJSONEncoder

# Type variables for generic response handling
T = TypeVar("T")

# Module-level logger
logger = logging.getLogger("voxlogica.main")


class ErrorResponse(BaseModel):
    """Standard error response model"""

    detail: str


class SuccessResponse(BaseModel, Generic[T]):
    """Standard success response model"""

    success: bool = True
    data: Optional[T] = None


# Create CLI app with Typer
app = typer.Typer(
    name="voxlogica",
    help="VoxLogicA - A tool for analyzing VoxLogicA programs",
    add_completion=False,
)

# FastAPI lifecycle events using lifespan
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the application lifespan"""
    # Startup: Initialize file watcher when the server starts
    start_file_watcher()
    yield
    # Shutdown: Clean up file watcher when the server shuts down  
    stop_file_watcher()


# Create FastAPI app for API server
api_app = FastAPI(
    title="VoxLogicA API",
    description="API for VoxLogicA program analysis",
    version=get_version(),
    lifespan=lifespan,
)

# Add CORS middleware
api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    api_app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# API router for versioned endpoints
api_router = APIRouter(prefix="/api/v1")


# Request models
class RunRequest(BaseModel):
    program: str
    filename: Optional[str] = None
    save_task_graph: Optional[str] = None
    save_task_graph_as_dot: Optional[str] = None
    save_task_graph_as_json: Optional[str] = None
    save_syntax: Optional[str] = None
    compute_memory_assignment: Optional[bool] = False
    debug: Optional[bool] = False


# ----------------- Helper Functions -----------------


class ElapsedMsFormatter(logging.Formatter):
    """Formatter that shows milliseconds since program start, right-aligned for up to 9999 seconds."""
    def __init__(self, fmt=None, datefmt=None, *args, **kwargs):
        super().__init__(fmt, datefmt, *args, **kwargs)
        self.start_time = time.monotonic()
        self.width = 8  # Enough for '9999000ms'

    def format(self, record):
        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
        # Right-align, pad with spaces, always show 'ms' suffix
        if elapsed_ms < 10**7:  # up to 9999.999s
            elapsed = f"[{elapsed_ms:>{self.width}}ms]"
        else:
            # If more than 9999.999s, don't pad
            elapsed = f"[{elapsed_ms}ms]"
        record.elapsed = elapsed
        # Use %(elapsed)s in format string
        return super().format(record)


VERBOSE_LEVEL = 15  # Between INFO (20) and DEBUG (10)
logging.addLevelName(VERBOSE_LEVEL, "VERBOSE")

def verbose(self, message, *args, **kwargs):
    if self.isEnabledFor(VERBOSE_LEVEL):
        self._log(VERBOSE_LEVEL, message, args, **kwargs)
logging.Logger.verbose = verbose  # type: ignore[attr-defined]


def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    """Set up logging configuration"""
    if debug:
        log_level = logging.DEBUG
    elif verbose:
        log_level = VERBOSE_LEVEL
    else:
        log_level = logging.INFO
    formatter = ElapsedMsFormatter('%(elapsed)s %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = []  # Remove any existing handlers
    root.addHandler(handler)
    root.setLevel(log_level)
    
    # Ensure Dask forwards worker stdout/stderr to the main process
    dask.config.set({'distributed.worker.redirect_stdouts': True})
    
    # Set Dask-related loggers to WARNING level to reduce noise
    # This prevents messages like "lost all workers", "connection to inproc://", "closing scheduler" etc.
    dask_loggers = [
        'distributed',
        'distributed.core',
        'distributed.scheduler',
        'distributed.worker',
        'distributed.client', 
        'distributed.comm',
        'distributed.protocol',
        'distributed.utils',
        'distributed.nanny',
        'distributed.process',
        'distributed.deploy',
        'distributed.diagnostics',
        'distributed.dashboard',
        'distributed.dashboard.core',
        'distributed.preloading',
        'distributed.batched',
        'distributed.comm.core',
        'distributed.comm.inproc',
        'distributed.deploy.local',
        'distributed.deploy.spec',
        'distributed.worker_memory',
        'distributed.stealing',
        'distributed.shuffle',
        'dask',
        'dask.bag',
        'dask.core',
        'dask.delayed',
        'dask.distributed',
        'bokeh',
        'bokeh.server',
        'bokeh.server.server',
        'tornado',
        'tornado.access',
        'tornado.application',
        'tornado.general',
        'asyncio',
        'fsspec',
        'fsspec.asyn'
    ]
    
    for logger_name in dask_loggers:
        dask_logger = logging.getLogger(logger_name)
        dask_logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    
    # Specifically suppress scheduler messages about worker removal  
    if not debug:
        scheduler_logger = logging.getLogger('distributed.scheduler')
        scheduler_logger.setLevel(logging.CRITICAL)  # Only show critical errors
    
    # Also suppress warnings module for Dask-related warnings
    import warnings
    if not debug:
        warnings.filterwarnings("ignore", category=UserWarning, module="distributed")
        warnings.filterwarnings("ignore", category=UserWarning, module="dask") 
        warnings.filterwarnings("ignore", category=UserWarning, module="bokeh")
        warnings.filterwarnings("ignore", message=".*jupyter-server-proxy.*")
        warnings.filterwarnings("ignore", message=".*diagnostics web server.*")
        warnings.filterwarnings("ignore", message=".*To route to workers.*")
        
        # Try to silence specific Dask diagnostic prints
        try:
            import distributed.diagnostics
            # Monkey patch to disable the message if possible
            if hasattr(distributed.diagnostics, 'install_jupyter_server_proxy_warning'):
                distributed.diagnostics.install_jupyter_server_proxy_warning = lambda: None
        except (ImportError, AttributeError):
            pass


def handle_cli_feature(feature_name: str, **kwargs: Any) -> None:
    """Handle a CLI feature execution"""
    try:
        # Get the feature handler
        feature = FeatureRegistry.get_feature(feature_name)
        if not feature:
            logger.error("Unknown feature: %s", feature_name)
            sys.exit(1)

        # Call the feature handler
        result = feature.handler(**kwargs)

        # Handle the result
        if hasattr(result, "success"):
            if not result.success:
                error_message = getattr(result, "error", "Unknown error")
                logger.error("Operation failed: %s", error_message)
                sys.exit(1)
            else:
                # Handle successful result
                if hasattr(result, "data") and result.data:
                    data = result.data
                    if feature_name == "run":
                        logger.debug("Program completed successfully")
                        logger.debug("  Operations: %d", data.get("operations", 0))
                        logger.debug("  Goals: %d", data.get("goals", 0))
                        if "task_graph" in data:
                            logger.debug("  Task graph:\n%s", data["task_graph"])
                        if "messages" in data:
                            for message in data["messages"]:
                                logger.info("  %s", message)
                        # Print the result as JSON for CLI output if not None
                        import json as _json
                        logger.debug(_json.dumps(data, indent=2, cls=WorkPlanJSONEncoder))
                    elif feature_name == "version":
                        logger.info(
                            "VoxLogicA version: %s", data.get("version", "unknown")
                        )
        else:
            logger.info("Feature executed successfully")

    except Exception as e:
        logger.exception("Unexpected error")
        sys.exit(1)


# ----------------- CLI Commands -----------------


@app.command()
def version() -> None:
    """Show the VoxLogicA version"""
    setup_logging(False)
    handle_cli_feature("version")


@app.command()
def run(
    filename: str = typer.Argument(..., help="VoxLogicA session file"),
    save_task_graph: Optional[str] = typer.Option(None, help="Save the task graph"),
    save_task_graph_as_dot: Optional[str] = typer.Option(
        None, help="Save the task graph in .dot format and exit"
    ),
    save_task_graph_as_json: Optional[str] = typer.Option(
        None, help="Save the task graph as JSON and exit"
    ),
    save_syntax: Optional[str] = typer.Option(
        None, help="Save the AST in text format and exit"
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
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging (between info and debug)"),
    dask_dashboard: bool = typer.Option(False, "--dask-dashboard", help="Enable Dask web dashboard for real-time task execution debugging"),
) -> None:
    """Run a VoxLogicA program"""
    setup_logging(debug, verbose)

    # Log version
    logger.info(f"VoxLogicA version: {get_version()}")

    # Read the program from file
    try:
        with open(filename, "r") as f:
            program = f.read()
    except FileNotFoundError:
        logger.error("File not found: %s", filename)
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error("Error reading file %s: %s", filename, str(e))
        raise typer.Exit(code=1)

    # Prepare kwargs
    kwargs = {
        "program": program,
        "filename": filename,
        "debug": debug,
        "verbose": verbose,
        "no_cache": no_cache,
        "dask_dashboard": dask_dashboard,
    }

    try:
        # Use the unified run feature with all options
        handle_cli_feature(
            "run",
            save_task_graph=save_task_graph,
            save_task_graph_as_dot=save_task_graph_as_dot,
            save_task_graph_as_json=save_task_graph_as_json,
            save_syntax=save_syntax,
            compute_memory_assignment=compute_memory_assignment,
            execute=execute,
            **kwargs,
        )

        raise typer.Exit(code=0)
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("An unexpected error occurred")
        raise typer.Exit(code=1) from e


@app.command("list-primitives")
def list_primitives(
    namespace: Optional[str] = typer.Argument(None, help="Namespace to filter primitives (optional)")
) -> None:
    """List available primitives"""
    setup_logging(False)
    
    try:
        from voxlogica.features import handle_list_primitives
        
        result = handle_list_primitives(namespace=namespace)
        
        if not result.success:
            logger.error(f"Error: {result.error}")
            raise typer.Exit(code=1)
            
        # Display results in a user-friendly format
        data = result.data
        
        if data.get('namespace_filter'):
            print(f"Primitives in namespace '{data['namespace_filter']}':")
        else:
            print("All available primitives:")
            
        primitives = data.get('primitives', {})
        if not primitives:
            print("  No primitives found.")
        else:
            for name, description in sorted(primitives.items()):
                print(f"  {name:<30} {description}")
                
        # Show available namespaces if listing all
        if not data.get('namespace_filter'):
            namespaces = data.get('namespaces', [])
            if namespaces:
                print(f"\nAvailable namespaces: {', '.join(sorted(namespaces))}")
                print("Use 'voxlogica list-primitives <namespace>' to filter by namespace.")
                
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise typer.Exit(code=1)


# ----------------- API Endpoints -----------------


@api_router.get("/version")
async def get_version_endpoint():
    """Get VoxLogicA version"""
    try:
        feature = FeatureRegistry.get_feature("version")
        if not feature:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Version feature not found",
            )
        result = feature.handler()
        if hasattr(result, "success"):
            if not result.success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result.error or "An error occurred",
                )
            return result.data
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in version endpoint: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@api_router.post("/run")
async def run_program_endpoint(request: RunRequest):
    """Run a VoxLogicA program with various output options"""
    try:
        feature = FeatureRegistry.get_feature("run")
        if not feature:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Run feature not found",
            )

        # Convert request to kwargs
        kwargs = request.dict()
        result = feature.handler(**kwargs)

        if hasattr(result, "success"):
            if not result.success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result.error or "An error occurred",
                )
            return result.data
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in run endpoint: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


# Root endpoint - serve the interactive visualization page
@api_app.get("/")
async def root():
    """Serve the interactive task graph visualizer"""
    static_path = Path(__file__).parent / "static" / "index.html"
    if static_path.exists():
        return FileResponse(str(static_path))
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visualization page not found",
        )


# Include the router in the FastAPI app
api_app.include_router(api_router)


# FastAPI lifecycle events using lifespan
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the application lifespan"""
    # Startup: Initialize file watcher when the server starts
    start_file_watcher()
    yield
    # Shutdown: Clean up file watcher when the server shuts down  
    stop_file_watcher()


# ----------------- Live Reload WebSocket and File Watcher -----------------

live_reload_clients = set()


class ReloadEventHandler(FileSystemEventHandler):
    def __init__(self, loop):
        self.loop = loop
        self.logger = logging.getLogger("voxlogica.filewatcher")

    def on_any_event(self, event):
        if event.is_directory:
            return

        self.logger.info(f"File change detected: {event.src_path} ({event.event_type})")

        # Schedule the notification in the asyncio event loop
        asyncio.run_coroutine_threadsafe(self._notify_clients(), self.loop)

    async def _notify_clients(self):
        """Notify all connected WebSocket clients about file changes"""
        self.logger.info(f"Notifying {len(live_reload_clients)} WebSocket clients")

        # Create a copy of the set to avoid modification during iteration
        clients_to_remove = set()

        for ws in list(live_reload_clients):
            try:
                await ws.send_text("reload")
                self.logger.debug(f"Sent reload signal to WebSocket client")
            except Exception as e:
                self.logger.warning(f"Failed to send reload signal to WebSocket: {e}")
                clients_to_remove.add(ws)

        # Remove failed clients
        for ws in clients_to_remove:
            live_reload_clients.discard(ws)

import json

@api_app.websocket("/livereload")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    live_reload_clients.add(websocket)
    logger = logging.getLogger("voxlogica.browser")
    try:
        while True:
            message = await websocket.receive_text()
            # Handle console messages from browser
            try:
                data = json.loads(message)
                if isinstance(data, dict) and "type" in data and "message" in data:
                    msg_type = data["type"]
                    msg_content = data["message"]
                    if msg_type == "log":
                        logger.info(f"[BROWSER] {msg_content}")
                    elif msg_type == "error":
                        logger.error(f"[BROWSER ERROR] {msg_content}")
                    elif msg_type == "warn":
                        logger.warning(f"[BROWSER WARN] {msg_content}")
                # else: just keep connection alive for other messages
            except (json.JSONDecodeError, KeyError):
                # Not a console message, just keep connection alive
                pass
    except WebSocketDisconnect:
        live_reload_clients.remove(websocket)


# Global observer reference
_file_observer = None


def start_file_watcher():
    global _file_observer
    static_dir = Path(__file__).parent / "static"
    logger = logging.getLogger("voxlogica.filewatcher")
    logger.info(f"Starting file watcher for directory: {static_dir}")

    # Get the current event loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    event_handler = ReloadEventHandler(loop)
    _file_observer = Observer()
    _file_observer.schedule(event_handler, str(static_dir), recursive=True)
    _file_observer.start()

    logger.info("File watcher started successfully")
    return _file_observer


def stop_file_watcher():
    global _file_observer
    if _file_observer:
        _file_observer.stop()
        _file_observer.join()
        _file_observer = None


# ----------------- CLI Commands -----------------


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind the API server"),
    port: int = typer.Option(8000, help="Port to bind the API server"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
):
    """Start the VoxLogicA API server"""
    setup_logging(debug)

    logger.info(
        f"Starting VoxLogicA API server version {get_version()} on {host}:{port}"
    )
    logger.info(f"Interactive graph visualizer at http://{host}:{port}/")
    logger.info(f"API documentation available at http://{host}:{port}/docs")

    # File watcher will be started automatically via FastAPI startup event
    uvicorn.run(api_app, host=host, port=port)


# ----------------- API Models -----------------

# These models are now dynamically generated from feature definitions
# in the register_api_endpoints() function


if __name__ == "__main__":
    app()
