# main.py - Command Line Interface and Entry Points

## Purpose

The `main.py` module provides the main entry points for VoxLogicA-2, including the command-line interface (CLI), REST API server, and interactive web interface. It orchestrates the integration between different system components and provides user-facing interfaces.

## Architecture

### Core Components

#### 1. Command Line Interface (CLI)
- **Typer Integration**: Modern CLI framework with automatic help generation
- **Feature-Based Commands**: CLI commands automatically generated from registered features
- **Argument Parsing**: Type-safe argument parsing and validation

#### 2. REST API Server
- **FastAPI Framework**: High-performance async API server
- **WebSocket Support**: Real-time communication for monitoring and debugging
- **CORS Enabled**: Cross-origin resource sharing for web clients

#### 3. Web Interface
- **Static File Serving**: Built-in web dashboard for VoxLogicA operations
- **File Watching**: Automatic reload during development
- **Responsive Design**: Modern web interface for program execution and monitoring

#### 4. Global Configuration
- **Logging Setup**: Centralized logging configuration
- **Verbose Levels**: Multiple verbosity levels for debugging
- **Dask Integration**: Global Dask client management

### Key Classes and Functions

#### CLI Application
```python
# Typer CLI application
cli_app = typer.Typer(
    name="voxlogica",
    help="VoxLogicA-2: Spatial Model Checker for Declarative Image Analysis",
    no_args_is_help=True
)

@cli_app.command()
def run(
    program: Optional[str] = typer.Argument(None, help="VoxLogicA program source"),
    filename: Optional[Path] = typer.Option(None, "--file", "-f", help="Program file path"),
    execute: bool = typer.Option(True, "--execute/--no-execute", help="Execute the program"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output")
) -> None:
    """Execute VoxLogicA program."""
```

#### API Server
```python
# FastAPI application
api_app = FastAPI(
    title="VoxLogicA-2 API",
    description="REST API for VoxLogicA-2 Spatial Model Checker",
    version=get_version()
)

@api_app.post("/api/v1/execute")
async def execute_program(request: ExecuteRequest) -> SuccessResponse[Dict[str, Any]]:
    """Execute VoxLogicA program via API."""

@api_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication."""
```

#### Response Models
```python
class ErrorResponse(BaseModel):
    """Standard error response model."""
    detail: str

class SuccessResponse(BaseModel, Generic[T]):
    """Standard success response model."""
    success: bool = True
    data: Optional[T] = None

class ExecuteRequest(BaseModel):
    """Request model for program execution."""
    program: str
    options: Optional[Dict[str, Any]] = None
```

## Implementation Details

### CLI Command Generation

Commands are automatically generated from registered features:

```python
def register_feature_commands():
    """Register CLI commands from feature registry."""
    
    for name, feature in FeatureRegistry.get_all_features().items():
        if feature.cli_options:
            # Create dynamic command function
            def create_command(feature_ref: Feature):
                def command_func(**kwargs):
                    result = feature_ref.handler(**kwargs)
                    
                    if result.success:
                        if result.data:
                            print(json.dumps(result.data, indent=2, cls=WorkPlanJSONEncoder))
                    else:
                        print(f"Error: {result.error}", file=sys.stderr)
                        raise typer.Exit(1)
                
                return command_func
            
            # Register command with Typer
            cli_app.command(name=name, help=feature.description)(create_command(feature))
```

### Logging Configuration

```python
# Global verbose level
VERBOSE_LEVEL = 0

def setup_logging(verbose_level: int = 0):
    """Configure logging based on verbosity level."""
    
    if verbose_level == 0:
        level = logging.WARNING
    elif verbose_level == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Suppress noisy third-party loggers
    logging.getLogger('distributed').setLevel(logging.WARNING)
    logging.getLogger('tornado').setLevel(logging.WARNING)
    logging.getLogger('dask').setLevel(logging.WARNING)
```

### Error Handling

```python
def handle_execution_error(e: Exception, debug: bool = False) -> None:
    """Handle and report execution errors."""
    
    if debug:
        # Full traceback in debug mode
        traceback.print_exc()
    else:
        # Clean error message for users
        print(f"Error: {str(e)}", file=sys.stderr)
    
    # Log full error details
    logger.error(f"Execution error: {e}", exc_info=True)
```

### API Route Registration

```python
def register_api_routes():
    """Register API routes from feature registry."""
    
    router = APIRouter(prefix="/api/v1")
    
    for name, feature in FeatureRegistry.get_all_features().items():
        if feature.api_endpoint:
            endpoint = feature.api_endpoint
            
            async def create_route_handler(feature_ref: Feature):
                async def route_handler(request: Dict[str, Any]):
                    try:
                        result = feature_ref.handler(**request)
                        
                        if result.success:
                            return SuccessResponse(data=result.data)
                        else:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=result.error
                            )
                    
                    except Exception as e:
                        logger.error(f"API error in {feature_ref.name}: {e}")
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=str(e)
                        )
                
                return route_handler
            
            # Register route with FastAPI
            router.add_api_route(
                endpoint["path"],
                create_route_handler(feature),
                methods=[endpoint["method"]],
                response_model=SuccessResponse[Any]
            )
    
    api_app.include_router(router)
```

## Dependencies

### Internal Dependencies
- `voxlogica.features` - Feature registry and execution
- `voxlogica.version` - Version information
- `voxlogica.converters.json_converter` - JSON serialization

### External Dependencies
- `typer` - Modern CLI framework
- `fastapi` - High-performance web framework
- `uvicorn` - ASGI server for FastAPI
- `pydantic` - Data validation and serialization
- `dask` - Distributed computing
- `watchdog` - File system monitoring

## Usage Examples

### CLI Usage
```bash
# Execute VoxLogicA program from command line
voxlogica run "print add(1, 2)" --execute --verbose

# Execute program from file
voxlogica run --file program.vl --debug

# Show version information
voxlogica version

# Get help for any command
voxlogica run --help
```

### API Server Usage
```bash
# Start API server
voxlogica serve --host 0.0.0.0 --port 8000

# Execute program via API
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{"program": "print add(1, 2)", "options": {"execute": true}}'
```

### Programmatic Usage
```python
from voxlogica.main import main_cli, main_api

# CLI entry point
def run_cli():
    main_cli()

# API server entry point
def run_server():
    main_api(host="localhost", port=8000)
```

### WebSocket Communication
```javascript
// Connect to WebSocket for real-time updates
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('Received:', data);
};

// Send execution request
ws.send(JSON.stringify({
    type: 'execute',
    program: 'print add(1, 2)',
    options: {execute: true}
}));
```

## Performance Considerations

### CLI Performance
- **Lazy Loading**: Features are loaded only when needed
- **Minimal Startup**: Fast CLI startup for common operations
- **Efficient Parsing**: Typer provides efficient argument parsing

### API Performance
- **Async Processing**: FastAPI enables high-concurrency request handling
- **Connection Pooling**: Reuse of database and Dask connections
- **Response Caching**: Common responses are cached for performance

### Memory Management
- **Shared Resources**: Dask client and storage are shared across requests
- **Garbage Collection**: Automatic cleanup of completed operations
- **Resource Limits**: Configurable memory and CPU limits

## Configuration Options

### Environment Variables
```bash
# Logging configuration
export VOXLOGICA_LOG_LEVEL="INFO"
export VOXLOGICA_LOG_FILE="/var/log/voxlogica.log"

# API server configuration
export VOXLOGICA_API_HOST="0.0.0.0"
export VOXLOGICA_API_PORT="8000"
export VOXLOGICA_API_WORKERS="4"

# Dask configuration
export VOXLOGICA_DASK_DASHBOARD="true"
export VOXLOGICA_DASK_THREADS="4"
```

### CLI Configuration
```python
# Default CLI options
DEFAULT_CLI_CONFIG = {
    'verbose': False,
    'debug': False,
    'execute': True,
    'cache': True,
    'dask_dashboard': False
}

# Override via command line or config file
cli_app = typer.Typer(
    context_settings={"default_map": DEFAULT_CLI_CONFIG}
)
```

### API Configuration
```python
# API server configuration
API_CONFIG = {
    'title': 'VoxLogicA-2 API',
    'version': get_version(),
    'docs_url': '/docs',
    'redoc_url': '/redoc',
    'openapi_url': '/openapi.json'
}

api_app = FastAPI(**API_CONFIG)
```

## Error Handling and Logging

### CLI Error Handling
```python
def handle_cli_error(e: Exception, debug: bool = False) -> None:
    """Handle CLI errors with appropriate user feedback."""
    
    if isinstance(e, typer.BadParameter):
        print(f"Invalid parameter: {e}", file=sys.stderr)
    elif isinstance(e, FileNotFoundError):
        print(f"File not found: {e.filename}", file=sys.stderr)
    elif debug:
        traceback.print_exc()
    else:
        print(f"Error: {str(e)}", file=sys.stderr)
    
    logger.error(f"CLI error: {e}", exc_info=debug)
    raise typer.Exit(1)
```

### API Error Handling
```python
@api_app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions with structured responses."""
    
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(detail=exc.detail).dict()
    )

@api_app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    
    logger.error(f"Unhandled API error: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(detail="Internal server error").dict()
    )
```

### Logging Integration
```python
def log_request_info(request, response_time: float):
    """Log request information for monitoring."""
    
    logger.info(
        f"API request: {request.method} {request.url.path} "
        f"- {response_time:.3f}s"
    )

def log_execution_metrics(operation: str, duration: float, success: bool):
    """Log execution metrics for performance monitoring."""
    
    status = "SUCCESS" if success else "FAILURE"
    logger.info(f"Execution: {operation} - {duration:.3f}s - {status}")
```

## Integration Points

### With Feature System
The main module integrates closely with the feature system:

```python
# Automatic CLI command generation
for name, feature in FeatureRegistry.get_all_features().items():
    if feature.cli_options:
        register_cli_command(name, feature)

# Automatic API route generation
for name, feature in FeatureRegistry.get_all_features().items():
    if feature.api_endpoint:
        register_api_route(name, feature)
```

### With Execution Engine
Integration with the execution engine for program running:

```python
from voxlogica.execution import ExecutionEngine

def execute_program_with_monitoring(program: str, options: Dict[str, Any]):
    """Execute program with monitoring and logging."""
    
    engine = ExecutionEngine()
    
    start_time = time.time()
    try:
        result = engine.execute_program(program, **options)
        duration = time.time() - start_time
        log_execution_metrics("program_execution", duration, True)
        return result
    
    except Exception as e:
        duration = time.time() - start_time
        log_execution_metrics("program_execution", duration, False)
        raise
```

### With Storage System
Integration with storage for caching and persistence:

```python
from voxlogica.storage import get_storage

def initialize_storage():
    """Initialize storage system with monitoring."""
    
    storage = get_storage()
    
    # Log storage statistics
    stats = storage.get_statistics()
    logger.info(f"Storage initialized: {stats['total_keys']} cached items")
    
    return storage
```
