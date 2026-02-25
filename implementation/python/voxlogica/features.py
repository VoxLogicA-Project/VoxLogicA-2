"""Feature registry and handlers for CLI/API orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Generic, Optional, TypeVar
import json
import logging

from voxlogica.converters import to_dot, to_json
from voxlogica.converters.json_converter import WorkPlanJSONEncoder
from voxlogica.execution import ExecutionEngine, execute_workplan
from voxlogica.parser import parse_program, parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import NoCacheStorageBackend


logger = logging.getLogger("voxlogica.features")
T = TypeVar("T")


@dataclass
class OperationResult(Generic[T]):
    """Wrapper for feature outcomes with explicit success/error payloads."""

    success: bool
    data: Optional[T] = None
    error: Optional[str] = None

    @classmethod
    def ok(cls, data: Optional[T] = None) -> "OperationResult[T]":
        return cls(success=True, data=data, error=None)

    @classmethod
    def fail(cls, error: str) -> "OperationResult[T]":
        return cls(success=False, data=None, error=error)


@dataclass(frozen=True)
class Feature:
    """Feature definition for CLI/API discovery and execution."""

    name: str
    description: str
    handler: Callable[..., OperationResult[Any]]
    cli_options: Optional[Dict[str, Any]] = None
    api_endpoint: Optional[Dict[str, Any]] = None


class FeatureRegistry:
    """In-memory feature registry."""

    _features: Dict[str, Feature] = {}

    @classmethod
    def register(cls, feature: Feature) -> Feature:
        cls._features[feature.name] = feature
        return feature

    @classmethod
    def get_feature(cls, name: str) -> Optional[Feature]:
        return cls._features.get(name)

    @classmethod
    def get_all_features(cls) -> Dict[str, Feature]:
        return cls._features.copy()


def _json_safe(value: Any) -> Any:
    """Convert payload to JSON-safe structure using project encoder."""
    return json.loads(json.dumps(value, cls=WorkPlanJSONEncoder))


def _load_syntax(program: Optional[str], filename: Optional[str]):
    if program and program.strip():
        return parse_program_content(program)

    if filename:
        return parse_program(filename)

    raise ValueError("Either program content or filename must be provided")


def _collect_exports(
    *,
    cli_mode: bool,
    workplan: Any,
    syntax: Any,
    save_task_graph: Optional[str],
    save_task_graph_as_dot: Optional[str],
    save_task_graph_as_json: Optional[str],
    save_syntax: Optional[str],
) -> tuple[list[str], dict[str, Any]]:
    messages: list[str] = []
    saved_files: dict[str, Any] = {}

    dot_target = save_task_graph or save_task_graph_as_dot
    if dot_target:
        dot_content = to_dot(workplan)
        if cli_mode:
            Path(dot_target).write_text(dot_content, encoding="utf-8")
            messages.append(f"Task graph saved as DOT to {dot_target}")
        else:
            saved_files[dot_target] = dot_content

    if save_task_graph_as_json:
        json_payload = to_json(workplan)
        if cli_mode:
            Path(save_task_graph_as_json).write_text(
                json.dumps(json_payload, indent=2, cls=WorkPlanJSONEncoder),
                encoding="utf-8",
            )
            messages.append(f"Task graph saved as JSON to {save_task_graph_as_json}")
        else:
            saved_files[save_task_graph_as_json] = _json_safe(json_payload)

    if save_syntax:
        syntax_text = str(syntax)
        if cli_mode:
            Path(save_syntax).write_text(syntax_text, encoding="utf-8")
            messages.append(f"Syntax saved to {save_syntax}")
        else:
            saved_files[save_syntax] = syntax_text

    return messages, saved_files


def handle_version(**kwargs) -> OperationResult[Dict[str, str]]:
    """Return current VoxLogicA version."""
    from voxlogica.version import get_version

    return OperationResult.ok({"version": get_version()})


def handle_run(
    program: Optional[str] = None,
    filename: Optional[str] = None,
    save_task_graph: Optional[str] = None,
    save_task_graph_as_dot: Optional[str] = None,
    save_task_graph_as_json: Optional[str] = None,
    save_syntax: Optional[str] = None,
    compute_memory_assignment: bool = False,
    execute: bool = True,
    debug: bool = False,
    verbose: bool = False,
    no_cache: bool = False,
    dask_dashboard: bool = False,
    execution_strategy: str = "dask",
    **kwargs,
) -> OperationResult[Dict[str, Any]]:
    """Run parse/reduce/export/execute pipeline for a VoxLogicA program."""
    try:
        syntax = _load_syntax(program, filename)
        workplan = reduce_program(syntax)

        cli_mode = bool(filename)
        execution_result = None
        if execute:
            if no_cache:
                logger.info("No-cache mode enabled - results are not persisted")
                engine = ExecutionEngine(storage_backend=NoCacheStorageBackend())
                execution_result = engine.execute_workplan(
                    workplan,
                    dask_dashboard=dask_dashboard,
                    strategy=execution_strategy,
                )
            else:
                execution_result = execute_workplan(
                    workplan,
                    dask_dashboard=dask_dashboard,
                    strategy=execution_strategy,
                )

            if not execution_result.success:
                error_msg = (
                    "Execution failed with "
                    f"{len(execution_result.failed_operations)} errors"
                )
                if cli_mode:
                    logger.error(error_msg)
                    for node_id, message in execution_result.failed_operations.items():
                        logger.error("  %s...: %s", node_id[:8], message)
                else:
                    return OperationResult.fail(error_msg)

        result: dict[str, Any] = {
            "operations": len(workplan.operations),
            "goals": len(workplan.goals),
            "task_graph": str(workplan),
            "syntax": str(syntax),
        }

        if execution_result is not None:
            result["execution"] = {
                "success": execution_result.success,
                "completed_operations": len(execution_result.completed_operations),
                "failed_operations": len(execution_result.failed_operations),
                "execution_time": execution_result.execution_time,
                "total_operations": execution_result.total_operations,
            }
            if execution_result.failed_operations:
                result["execution"]["errors"] = execution_result.failed_operations

        messages, saved_files = _collect_exports(
            cli_mode=cli_mode,
            workplan=workplan,
            syntax=syntax,
            save_task_graph=save_task_graph,
            save_task_graph_as_dot=save_task_graph_as_dot,
            save_task_graph_as_json=save_task_graph_as_json,
            save_syntax=save_syntax,
        )

        if messages:
            result["messages"] = messages
        if saved_files:
            result["saved_files"] = saved_files

        return OperationResult.ok(_json_safe(result))

    except Exception as exc:  # noqa: BLE001
        return OperationResult.fail(f"Unexpected error: {exc}")


def handle_list_primitives(namespace: Optional[str] = None, **kwargs) -> OperationResult[Dict[str, Any]]:
    """List available primitives, optionally filtered by namespace."""
    try:
        from voxlogica.execution import PrimitivesLoader

        loader = PrimitivesLoader()
        return OperationResult.ok(
            {
                "primitives": loader.list_primitives(namespace),
                "namespaces": loader.list_namespaces(),
                "namespace_filter": namespace,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return OperationResult.fail(f"Failed to list primitives: {exc}")


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
            "filename": {"type": str, "required": True, "help": "VoxLogicA session file"},
            "save_task_graph": {"type": str, "required": False, "help": "Save task graph in DOT format"},
            "save_task_graph_as_dot": {"type": str, "required": False, "help": "Save task graph in DOT format"},
            "save_task_graph_as_json": {"type": str, "required": False, "help": "Save task graph as JSON"},
            "save_syntax": {"type": str, "required": False, "help": "Save AST in text format"},
            "compute_memory_assignment": {
                "type": bool,
                "required": False,
                "default": False,
                "help": "Compute and display memory buffer assignments",
            },
            "execute": {
                "type": bool,
                "required": False,
                "default": True,
                "help": "Execute the workplan (default: true)",
            },
            "debug": {"type": bool, "required": False, "default": False, "help": "Enable debug mode"},
            "verbose": {
                "type": bool,
                "required": False,
                "default": False,
                "help": "Enable verbose logging",
            },
            "execution_strategy": {
                "type": str,
                "required": False,
                "default": "dask",
                "help": "Execution strategy to use (dask|strict)",
            },
        },
        api_endpoint={
            "path": "/run",
            "methods": ["POST"],
            "request_model": {
                "program": (str, "The VoxLogicA program content"),
                "filename": (Optional[str], "Optional filename for context"),
                "save_task_graph": (Optional[str], "Save task graph as DOT to this file"),
                "save_task_graph_as_dot": (Optional[str], "Save task graph as DOT to this file"),
                "save_task_graph_as_json": (Optional[str], "Save task graph as JSON to this file"),
                "save_syntax": (Optional[str], "Save syntax tree to this file"),
                "compute_memory_assignment": (Optional[bool], "Compute memory buffer assignments"),
                "execute": (Optional[bool], "Execute workplan"),
                "debug": (Optional[bool], "Enable debug mode"),
                "verbose": (Optional[bool], "Enable verbose logging"),
                "execution_strategy": (Optional[str], "Execution strategy (dask|strict)"),
            },
            "response_model": Dict[str, Any],
        },
    )
)

list_primitives_feature = FeatureRegistry.register(
    Feature(
        name="list_primitives",
        description="List available primitives",
        handler=handle_list_primitives,
        cli_options={
            "namespace": {
                "type": Optional[str],
                "required": False,
                "help": "Namespace to filter primitives",
            }
        },
        api_endpoint={
            "path": "/primitives",
            "methods": ["GET"],
            "response_model": Dict[str, Any],
        },
    )
)
