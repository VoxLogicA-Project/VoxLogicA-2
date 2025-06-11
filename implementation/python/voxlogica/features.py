"""
This module defines all VoxLogicA features using a unified registry system.
This module serves as the single source of truth for all features.
"""

from typing import (
    Dict,
    Any,
    Callable,
    Optional,
    TypeVar,
    Generic,
)
from dataclasses import dataclass
import tempfile
import os
import json

from voxlogica.parser import parse_program
from voxlogica.reducer import reduce_program
from voxlogica.error_msg import Logger, VLException
from voxlogica.converters import to_json, to_dot
from voxlogica.error_msg import Logger

T = TypeVar("T")


class OperationResult(Generic[T]):
    """Wrapper for operation results with success/error handling"""

    def __init__(
        self, success: bool, data: Optional[T] = None, error: Optional[str] = None
    ):
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
    def get_feature(cls, name: str) -> Optional[Feature]:
        """Get a feature by name"""
        return cls._features.get(name)

    @classmethod
    def get_all_features(cls) -> Dict[str, Feature]:
        """Get all registered features"""
        return cls._features.copy()


# ----------------- Feature Handlers -----------------


def handle_version(**kwargs) -> OperationResult[Dict[str, str]]:
    """Handle version request"""
    from voxlogica.version import get_version

    return OperationResult[Dict[str, str]](
        success=True, data={"version": get_version()}
    )


def handle_run(
    program: str,
    filename: Optional[str] = None,
    save_task_graph: Optional[str] = None,
    save_task_graph_as_dot: Optional[str] = None,
    save_task_graph_as_json: Optional[str] = None,
    save_syntax: Optional[str] = None,
    compute_memory_assignment: bool = False,
    execute: bool = False,
    debug: bool = False,
    **kwargs,
) -> OperationResult[Dict[str, Any]]:
    """Handle the unified run command with all options"""
    temp_filename = None
    try:
        # Write the program to a temporary file if needed
        if program:
            with tempfile.NamedTemporaryFile(suffix=".imgql", delete=False) as temp:
                temp.write(program.encode())
                temp_filename = temp.name
            parse_filename = temp_filename
        else:
            parse_filename = filename

        if not parse_filename:
            return OperationResult[Dict[str, Any]](
                success=False,
                error="Either program content or filename must be provided",
            )

        # Parse and reduce the program
        syntax = parse_program(parse_filename)
        Logger.info(f"Program parsed")
        program_obj = reduce_program(syntax)
        Logger.info(f"Program reduced")

        # Execute the workplan if requested
        execution_result = None
        if execute:
            from voxlogica.execution import execute_workplan
            
            try:
                execution_result = execute_workplan(program_obj)
                if execution_result.success:
                    if filename:  # CLI mode
                        Logger.info(f"Execution completed successfully!")
                        Logger.info(f"  Operations completed: {len(execution_result.completed_operations)}")
                        Logger.info(f"  Execution time: {execution_result.execution_time:.2f}s")
                else:
                    error_msg = f"Execution failed with {len(execution_result.failed_operations)} errors"
                    if filename:  # CLI mode
                        Logger.error(error_msg)
                        for op_id, error in execution_result.failed_operations.items():
                            Logger.error(f"  {op_id[:8]}...: {error}")
                    else:
                        return OperationResult[Dict[str, Any]](
                            success=False,
                            error=error_msg
                        )
            except Exception as e:
                error_msg = f"Execution failed: {str(e)}"
                if filename:  # CLI mode
                    print(error_msg)
                else:
                    return OperationResult[Dict[str, Any]](
                        success=False,
                        error=error_msg
                    )

        # Compute buffer allocation if requested
        buffer_assignment = None
        if compute_memory_assignment:
            from voxlogica.buffer_allocation import (
                compute_buffer_allocation,
                print_buffer_assignment,
            )

            # For testing, assume all nodes have "basic_type" and use equality compatibility
            def type_assignment(op_id):
                return "basic_type"

            def type_compatibility(type1, type2):
                return type1 == type2

            buffer_assignment = compute_buffer_allocation(
                program_obj, type_assignment, type_compatibility
            )

            # Print the assignment to console if running from CLI
            if filename:  # CLI mode
                print_buffer_assignment(program_obj, buffer_assignment, type_assignment)

        # Build the result
        result = {
            "operations": len(program_obj.operations),
            "goals": len(program_obj.goals),
            "task_graph": str(program_obj),
            "syntax": str(syntax),
        }

        # Add execution results if execution was performed
        if execution_result:
            result["execution"] = {
                "success": execution_result.success,
                "completed_operations": len(execution_result.completed_operations),
                "failed_operations": len(execution_result.failed_operations),
                "execution_time": execution_result.execution_time,
                "total_operations": execution_result.total_operations
            }
            if execution_result.failed_operations:
                result["execution"]["errors"] = execution_result.failed_operations

        # Handle save options - CLI saves to files, API returns content with same keys
        saved_files = {}
        messages = []

        if save_task_graph or save_task_graph_as_dot:
            dot_content = to_dot(program_obj, buffer_assignment)
            output_file = save_task_graph or save_task_graph_as_dot

            if filename:  # CLI mode - save to file
                if output_file:
                    with open(output_file, "w") as f:
                        f.write(dot_content)
                    messages.append(f"Task graph saved as DOT to {output_file}")
            else:  # API mode - include in response with specified key
                if output_file:
                    saved_files[output_file] = dot_content

        if save_task_graph_as_json:
            json_content = to_json(program_obj, buffer_assignment)

            if filename:  # CLI mode - save to file
                with open(save_task_graph_as_json, "w") as f:
                    json.dump(json_content, f, indent=2)
                messages.append(
                    f"Task graph saved as JSON to {save_task_graph_as_json}"
                )
            else:  # API mode - include in response with specified key
                saved_files[save_task_graph_as_json] = json_content

        if save_syntax:
            syntax_content = str(syntax)

            if filename:  # CLI mode - save to file
                with open(save_syntax, "w") as f:
                    f.write(syntax_content)
                messages.append(f"Syntax saved to {save_syntax}")
            else:  # API mode - include in response with specified key
                saved_files[save_syntax] = syntax_content

        if messages:
            result["messages"] = messages
        if saved_files:
            result["saved_files"] = saved_files

        return OperationResult[Dict[str, Any]](success=True, data=result)

    except VLException as e:
        return OperationResult[Dict[str, Any]](success=False, error=str(e))
    except Exception as e:
        return OperationResult[Dict[str, Any]](
            success=False, error=f"Unexpected error: {str(e)}"
        )
    finally:
        # Clean up temporary file
        if temp_filename and os.path.exists(temp_filename):
            try:
                os.unlink(temp_filename)
            except Exception as e:
                Logger.warning(
                    f"Failed to clean up temporary file {temp_filename}: {str(e)}"
                )


# Register all features
version_feature = FeatureRegistry.register(
    Feature(
        name="version",
        description="Get the VoxLogicA version",
        handler=handle_version,
        api_endpoint={
            "path": "/version",
            "methods": ["GET"],
            "response_model": Dict[str, str],
        },
    )
)

run_feature = FeatureRegistry.register(
    Feature(
        name="run",
        description="Run a VoxLogicA program with various output options",
        handler=handle_run,
        cli_options={
            "filename": {
                "type": str,
                "required": True,
                "help": "VoxLogicA session file",
            },
            "save_task_graph": {
                "type": str,
                "required": False,
                "help": "Save the task graph in DOT format",
            },
            "save_task_graph_as_dot": {
                "type": str,
                "required": False,
                "help": "Save the task graph in DOT format",
            },
            "save_task_graph_as_json": {
                "type": str,
                "required": False,
                "help": "Save the task graph as JSON",
            },
            "save_syntax": {
                "type": str,
                "required": False,
                "help": "Save the AST in text format",
            },
            "compute_memory_assignment": {
                "type": bool,
                "required": False,
                "default": False,
                "help": "Compute and display memory buffer assignments",
            },
            "execute": {
                "type": bool,
                "required": False,
                "default": False,
                "help": "Actually execute the workplan (not just analyze)",
            },
            "debug": {
                "type": bool,
                "required": False,
                "default": False,
                "help": "Enable debug mode",
            },
        },
        api_endpoint={
            "path": "/run",
            "methods": ["POST"],
            "request_model": {
                "program": (str, "The VoxLogicA program content"),
                "filename": (Optional[str], "Optional filename for error reporting"),
                "save_task_graph": (
                    Optional[str],
                    "Save task graph as DOT to this file",
                ),
                "save_task_graph_as_dot": (
                    Optional[str],
                    "Save task graph as DOT to this file",
                ),
                "save_task_graph_as_json": (
                    Optional[str],
                    "Save task graph as JSON to this file",
                ),
                "save_syntax": (Optional[str], "Save syntax tree to this file"),
                "compute_memory_assignment": (
                    Optional[bool],
                    "Compute and display memory buffer assignments",
                ),
                "execute": (
                    Optional[bool],
                    "Actually execute the workplan (not just analyze)",
                ),
                "debug": (Optional[bool], "Enable debug mode"),
            },
            "response_model": Dict[str, Any],
        },
    )
)
