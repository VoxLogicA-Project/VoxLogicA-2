"""
VoxLogicA Main module - Python implementation
"""

import os
import sys
import importlib.metadata
import uvicorn
from typing import Optional, Dict, Any, Union, Type, get_type_hints, List, Callable, TypeVar, Generic, Tuple
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from pydantic import BaseModel, create_model, Field
import uvicorn
import typer
import logging
import sys
import os
from pathlib import Path

from .features import FeatureRegistry, Feature, OperationResult
from .error_msg import Logger, VLException

# Type variables for generic response handling
T = TypeVar('T')

class ErrorResponse(BaseModel):
    """Standard error response model"""
    detail: str

class SuccessResponse(BaseModel, Generic[T]):
    """Standard success response model"""
    success: bool = True
    data: Optional[T] = None

# Create CLI app with Typer
app = typer.Typer(help="VoxLogicA - Voxel Logic Analyzer")

# Create FastAPI app for API server
api_app = FastAPI(
    title="VoxLogicA API",
    description="API for VoxLogicA, a Voxel Logic Analyzer",
    version="0.1.0",
)

# API router for versioned endpoints
api_router = APIRouter(prefix="/api/v1")


def get_version() -> str:
    """Get the version of the VoxLogicA package"""
    try:
        return importlib.metadata.version("voxlogica")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0-dev"


# ----------------- Helper Functions -----------------

def setup_logging(debug: bool = False) -> None:
    """Set up logging configuration"""
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    logger = logging.getLogger('voxlogica')
    logger.info("VoxLogicA version: %s", get_version())


def handle_cli_feature(feature_name: str, **kwargs: Any) -> None:
    """Handle a CLI feature execution"""
    logger = logging.getLogger('voxlogica.cli')
    try:
        # Get the feature handler
        feature = FeatureRegistry.get_feature(feature_name)
        if not feature:
            logger.error("Unknown feature: %s", feature_name)
            sys.exit(1)
            
        # Call the feature handler
        result = feature.handler(**kwargs)
        
        # Handle the result
        if hasattr(result, 'success') and not result.success:
            error_message = getattr(result, 'error', 'Unknown error')
            logger.error("Operation failed: %s", error_message)
            sys.exit(1)
            
    except VLException as e:
        logger.error("Error: %s", str(e))
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error")
        sys.exit(1)


# ----------------- CLI Commands -----------------

@app.command()
def version() -> None:
    """Print the VoxLogicA version and exit"""
    handle_cli_feature("version")
    raise typer.Exit(code=0)


@app.command()
def run(
    filename: str = typer.Argument(..., help="VoxLogicA session file"),
    save_task_graph: Optional[str] = typer.Option(None, help="Save the task graph"),
    save_task_graph_as_dot: Optional[str] = typer.Option(
        None, help="Save the task graph in .dot format and exit"
    ),
    save_syntax: Optional[str] = typer.Option(
        None, help="Save the AST in text format and exit"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
) -> None:
    """Run a VoxLogicA session"""
    setup_logging(debug=debug)
    logger = logging.getLogger('voxlogica.cli')
    
    # Build kwargs for the feature handler
    kwargs: Dict[str, Any] = {
        "filename": filename,
        "debug": debug
    }
    
    try:
        # Read the program file
        with open(filename, "r") as f:
            program = f.read()
        kwargs["program"] = program
    except Exception as e:
        logger.error("Failed to read file %s: %s", filename, str(e))
        raise typer.Exit(code=1) from e
    
    try:
        # Determine which feature to run based on options
        if save_task_graph is not None:
            handle_cli_feature("save_task_graph", output_filename=save_task_graph, **kwargs)
        elif save_task_graph_as_dot is not None:
            handle_cli_feature("save_task_graph", output_filename=save_task_graph_as_dot, **kwargs)
        elif save_syntax is not None:
            logger.info("Saving syntax is not yet implemented in the new feature system")
            raise typer.Exit(code=1)
        else:
            # Default to running the program
            handle_cli_feature("program", **kwargs)
        
        raise typer.Exit(code=0)
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("An unexpected error occurred")
        raise typer.Exit(code=1) from e


# ----------------- API Endpoints -----------------

def create_request_model(feature: Feature) -> Type[BaseModel]:
    """Dynamically create a Pydantic model for the request"""
    if not feature.api_endpoint or "request_model" not in feature.api_endpoint:
        return type("EmptyRequest", (BaseModel,), {"__annotations__": {}})
    
    # Create field definitions with proper typing
    field_definitions: Dict[str, Any] = {}
    for field_name, (field_type, description) in feature.api_endpoint["request_model"].items():
        field_definitions[field_name] = (field_type, Field(..., description=description))
    
    # Create the model with proper type hints
    model_name = f"{feature.name.capitalize()}Request"
    return create_model(model_name, **field_definitions, __base__=BaseModel)


def register_api_endpoints():
    """Register all API endpoints from features"""
    for feature_name, feature in FeatureRegistry.get_all_features().items():
        if not feature.api_endpoint:
            continue
            
        endpoint_config = feature.api_endpoint
        path = endpoint_config["path"]
        methods = endpoint_config.get("methods", ["GET"])
        response_model = endpoint_config.get("response_model")
        
        # Create request model if needed
        request_model = None
        if "request_model" in endpoint_config:
            request_model = create_request_model(feature)
        
        # Create a closure to capture the current feature_name
        def create_endpoint_handler(current_feature_name: str):
            async def endpoint_handler(*, request: Any = None):
                try:
                    # Get the feature handler
                    handler = FeatureRegistry.get_feature(current_feature_name).handler
                    
                    # Prepare kwargs based on request
                    kwargs = {}
                    if request is not None and hasattr(request, "dict"):
                        kwargs.update(request.dict())
                    
                    # Call the handler
                    result = handler(**kwargs)
                    
                    # Handle the operation result
                    if hasattr(result, 'success'):
                        if not result.success:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=result.error or "An error occurred"
                            )
                        return result.data
                    return result
                    
                except HTTPException:
                    raise
                except VLException as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=str(e)
                    )
                except Exception as e:
                    # Log the error using the module-level logger
                    logger = logging.getLogger('voxlogica.main')
                    logger.error(
                        "Error in feature '%s': %s",
                        current_feature_name,
                        str(e),
                        exc_info=True
                    )
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Internal server error"
                    ) from e
            return endpoint_handler
        
        # Create the endpoint handler with the current feature_name
        endpoint_handler = create_endpoint_handler(feature_name)
        
        # Register the endpoint for each HTTP method
        for method in methods:
            method = method.lower()
            
            # Skip unsupported methods
            if method not in {"get", "post", "put", "delete"}:
                logging.getLogger('voxlogica.main').warning(
                    "Unsupported HTTP method: %s for %s",
                    method,
                    path
                )
                continue
            
            # Create route configuration
            route_kwargs = {
                "path": path,
                "response_model": response_model,
                "responses": {
                    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
                    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse}
                }
            }
            
            # Add request model for methods that expect a body
            if method in {"post", "put"} and request_model is not None:
                route_kwargs["response_model"] = response_model
                
                # Create a properly typed handler function
                async def typed_handler(request: Any) -> Any:
                    # Validate the request against the model at runtime
                    try:
                        # Convert request to dict if it's a Pydantic model
                        request_data = request.dict() if hasattr(request, 'dict') else request
                        # Create a new instance of the request model
                        # We know request_model is not None here because of the outer if condition
                        assert request_model is not None  # For type checking
                        validated_request = request_model(**request_data)
                        return await endpoint_handler(request=validated_request)
                    except Exception as e:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"error": f"Invalid request data: {str(e)}"}
                        )
                
                # Register the route with the typed handler
                getattr(api_router, method)(**route_kwargs)(typed_handler)
            else:
                # For methods without request body
                getattr(api_router, method)(**route_kwargs)(endpoint_handler)

# Register all API endpoints
register_api_endpoints()

# Include the router in the FastAPI app
api_app.include_router(api_router)


# ----------------- CLI Commands -----------------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind the API server"),
    port: int = typer.Option(8000, help="Port to bind the API server"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
):
    """Start the VoxLogicA API server"""
    setup_logging(debug)
    
    Logger.info(f"Starting VoxLogicA API server version {get_version()} on {host}:{port}")
    Logger.info(f"API documentation available at http://{host}:{port}/docs")
    
    uvicorn.run(api_app, host=host, port=port)


# ----------------- API Models -----------------

# These models are now dynamically generated from feature definitions
# in the register_api_endpoints() function


if __name__ == "__main__":
    app()
