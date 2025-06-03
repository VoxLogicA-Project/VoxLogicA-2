"""
Feature definitions for VoxLogicA CLI and API.
This module serves as the single source of truth for all features.
"""
from typing import Dict, Any, Callable, Optional, List, Union, Type, TypeVar, Generic, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import tempfile
import os
import json

from .parser import parse_program, Program as ParserProgram
from .reducer import reduce_program, WorkPlan
from .error_msg import Logger, VLException

T = TypeVar('T')

class OperationResult(Generic[T]):
    """Wrapper for operation results with success/error handling"""
    def __init__(self, success: bool, data: Optional[T] = None, error: Optional[str] = None):
        self.success = success
        self.data = data
        self.error = error


@dataclass
class Feature:
    """Base class for all VoxLogicA features"""
    name: str
    description: str
    handler: Callable
    cli_options: Optional[Dict[str, Any]] = None
    api_endpoint: Optional[Dict[str, Any]] = None


class FeatureRegistry:
    """Registry for all VoxLogicA features"""
    _features: Dict[str, Feature] = {}

    @classmethod
    def register(cls, feature: Feature) -> Feature:
        """Register a new feature"""
        cls._features[feature.name] = feature
        return feature

    @classmethod
    def get_feature(cls, name: str) -> Feature:
        """Get a feature by name"""
        return cls._features[name]

    @classmethod
    def get_all_features(cls) -> Dict[str, Feature]:
        """Get all registered features"""
        return cls._features.copy()


# Feature implementations
def handle_version() -> OperationResult[Dict[str, str]]:
    """Handle version request"""
    from .main import get_version
    return OperationResult[Dict[str, str]](
        success=True,
        data={"version": get_version()}
    )


def handle_program(
    program: str,
    filename: Optional[str] = None,
    **kwargs
) -> OperationResult[Dict[str, Any]]:
    """Handle program parsing and reduction"""
    temp_filename = None
    try:
        # Write the program to a temporary file if no filename is provided
        if filename and os.path.exists(filename):
            temp_filename = filename
            is_temp = False
        else:
            with tempfile.NamedTemporaryFile(suffix=".imgql", delete=False) as temp:
                temp.write(program.encode())
                temp_filename = temp.name
            is_temp = True

        try:
            # Parse and reduce the program
            syntax = parse_program(temp_filename)
            program_obj = reduce_program(syntax)
            
            result = {
                "operations": len(program_obj.operations),
                "goals": len(program_obj.goals),
                "task_graph": str(program_obj),
                "dot_graph": program_obj.to_dot(),
                "syntax": str(syntax),
            }
            return OperationResult[Dict[str, Any]](
                success=True,
                data=result
            )
            
        finally:
            # Clean up temporary file if we created one
            if is_temp and os.path.exists(temp_filename):
                try:
                    os.unlink(temp_filename)
                except Exception as e:
                    Logger.warning(f"Failed to clean up temporary file {temp_filename}: {str(e)}")
                    
    except VLException as e:
        return OperationResult[Dict[str, Any]](
            success=False,
            error=str(e)
        )
    except Exception as e:
        return OperationResult[Dict[str, Any]](
            success=False,
            error=f"Unexpected error: {str(e)}"
        )
    
    return OperationResult[Dict[str, Any]](
        success=False, 
        error="An unknown error occurred processing the program"
    )


def handle_save_task_graph(
    program: str,
    filename: Optional[str] = None,
    output_filename: Optional[str] = None,
    **kwargs
) -> OperationResult[Dict[str, str]]:
    """Handle saving task graph"""
    temp_filename = None
    try:
        # Write the program to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".imgql", delete=False) as temp:
            temp.write(program.encode())
            temp_filename = temp.name

        # Parse and reduce the program
        syntax = parse_program(temp_filename)
        program_obj = reduce_program(syntax)
        
        # Generate output filename
        output = output_filename or "task_graph.dot"

        # Save the task graph
        with open(output, "w") as f:
            f.write(program_obj.to_dot())

        return OperationResult[Dict[str, str]](
            success=True,
            data={"message": f"Task graph saved to {output}"}
        )
        
    except VLException as e:
        return OperationResult[Dict[str, str]](
            success=False,
            error=str(e)
        )
    except Exception as e:
        return OperationResult[Dict[str, str]](
            success=False,
            error=f"Unexpected error: {str(e)}"
        )
    finally:
        # Clean up temporary file
        if temp_filename and os.path.exists(temp_filename):
            try:
                os.unlink(temp_filename)
            except Exception as e:
                Logger.warning(f"Failed to clean up temporary file {temp_filename}: {str(e)}")


# Register all features
version_feature = FeatureRegistry.register(Feature(
    name="version",
    description="Get the VoxLogicA version",
    handler=handle_version,
    api_endpoint={
        "path": "/version",
        "methods": ["GET"],
        "response_model": Dict[str, str]
    }
))

program_feature = FeatureRegistry.register(Feature(
    name="program",
    description="Parse and reduce a VoxLogicA program",
    handler=handle_program,
    cli_options={
        "filename": {
            "type": str,
            "required": True,
            "help": "VoxLogicA session file"
        },
        "save_task_graph": {
            "type": str,
            "required": False,
            "help": "Save the task graph to a file"
        },
        "save_task_graph_as_dot": {
            "type": str,
            "required": False,
            "help": "Save the task graph in .dot format"
        },
        "debug": {
            "type": bool,
            "required": False,
            "default": False,
            "help": "Enable debug mode"
        }
    },
    api_endpoint={
        "path": "/program",
        "methods": ["POST"],
        "request_model": {
            "program": (str, "The VoxLogicA program to parse and reduce"),
            "filename": (Optional[str], "Optional filename for error reporting")
        },
        "response_model": Dict[str, Any]
    }
))

save_task_graph_feature = FeatureRegistry.register(Feature(
    name="save_task_graph",
    description="Parse, reduce, and save the task graph of a VoxLogicA program",
    handler=handle_save_task_graph,
    api_endpoint={
        "path": "/save-task-graph",
        "methods": ["POST"],
        "request_model": {
            "program": (str, "The VoxLogicA program to parse and reduce"),
            "filename": (Optional[str], "Optional output filename")
        },
        "response_model": Dict[str, str]
    }
))
