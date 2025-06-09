"""
VoxLogicA Main module - Python implementation
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Optional, TypeVar, Generic

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

from .features import FeatureRegistry
from .error_msg import Logger, VLException
from .version import get_version

# Type variables for generic response handling
T = TypeVar("T")


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

# Create FastAPI app for API server
api_app = FastAPI(
    title="VoxLogicA API",
    description="API for VoxLogicA program analysis",
    version=get_version(),
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


def setup_logging(debug: bool = False) -> None:
    """Set up logging configuration"""
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def handle_cli_feature(feature_name: str, **kwargs: Any) -> None:
    """Handle a CLI feature execution"""
    logger = logging.getLogger("voxlogica.cli")
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
                        logger.info("Successfully processed program:")
                        logger.info("  Operations: %d", data.get("operations", 0))
                        logger.info("  Goals: %d", data.get("goals", 0))
                        if "task_graph" in data:
                            logger.info("  Task graph:\n%s", data["task_graph"])
                        if "messages" in data:
                            for message in data["messages"]:
                                logger.info("  %s", message)
                    elif feature_name == "version":
                        logger.info(
                            "VoxLogicA version: %s", data.get("version", "unknown")
                        )
        else:
            logger.info("Feature executed successfully")

    except VLException as e:
        logger.error("Error: %s", str(e))
        sys.exit(1)
    except Exception as e:
        logger = logging.getLogger("voxlogica.cli")
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
        False,
        "--execute",
        help="Actually execute the workplan (not just analyze)",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
) -> None:
    """Run a VoxLogicA program"""
    setup_logging(debug)

    # Log version
    Logger.info(f"VoxLogicA version: {get_version()}")

    # Read the program from file
    try:
        with open(filename, "r") as f:
            program = f.read()
    except FileNotFoundError:
        logger = logging.getLogger("voxlogica.cli")
        logger.error("File not found: %s", filename)
        raise typer.Exit(code=1)
    except Exception as e:
        logger = logging.getLogger("voxlogica.cli")
        logger.error("Error reading file %s: %s", filename, str(e))
        raise typer.Exit(code=1)

    # Prepare kwargs
    kwargs = {
        "program": program,
        "filename": filename,
        "debug": debug,
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
        logger = logging.getLogger("voxlogica.cli")
        logger.exception("An unexpected error occurred")
        raise typer.Exit(code=1) from e


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
        logger = logging.getLogger("voxlogica.main")
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
    except VLException as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger = logging.getLogger("voxlogica.main")
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


# FastAPI lifecycle events
@api_app.on_event("startup")
async def startup_event():
    """Initialize file watcher when the server starts"""
    start_file_watcher()


@api_app.on_event("shutdown")
async def shutdown_event():
    """Clean up file watcher when the server shuts down"""
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
                import json

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

    Logger.info(
        f"Starting VoxLogicA API server version {get_version()} on {host}:{port}"
    )
    Logger.info(f"Interactive graph visualizer at http://{host}:{port}/")
    Logger.info(f"API documentation available at http://{host}:{port}/docs")

    # File watcher will be started automatically via FastAPI startup event
    uvicorn.run(api_app, host=host, port=port)


# ----------------- API Models -----------------

# These models are now dynamically generated from feature definitions
# in the register_api_endpoints() function


if __name__ == "__main__":
    app()
