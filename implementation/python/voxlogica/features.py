"""Feature registry and handlers for CLI/API orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Generic, Optional, TypeVar
import json
import logging

from voxlogica.converters import to_dot, to_json
from voxlogica.converters.json_converter import WorkPlanJSONEncoder
from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program, parse_program_content
from voxlogica.policy import (
    StaticPolicyError,
    diagnostics_payload,
    enforce_workplan_policy_or_raise,
    runtime_policy_scope,
)
from voxlogica.reducer import reduce_program_with_bindings
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


def _failed_operation_details(workplan: Any, failed_operations: dict[str, str]) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    nodes = getattr(workplan, "nodes", {}) if workplan is not None else {}
    if not isinstance(nodes, dict):
        return details

    for node_id in failed_operations.keys():
        node = nodes.get(node_id)
        if node is None:
            continue
        raw_kwargs = getattr(node, "kwargs", ())
        kwargs_pairs: list[tuple[str, str]] = []
        if isinstance(raw_kwargs, dict):
            kwargs_pairs = [(str(key), str(value)) for key, value in raw_kwargs.items()]
        else:
            try:
                kwargs_pairs = [(str(key), str(value)) for key, value in raw_kwargs]
            except Exception:
                kwargs_pairs = []

        details[str(node_id)] = {
            "operator": str(getattr(node, "operator", "")),
            "kind": str(getattr(node, "kind", "")),
            "output_kind": str(getattr(node, "output_kind", "")),
            "args": [str(arg) for arg in getattr(node, "args", ())],
            "kwargs": dict(kwargs_pairs),
            "attrs": dict(getattr(node, "attrs", {})),
        }
    return details


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
    legacy: bool = False,
    serve_mode: bool = False,
    _include_execution_events: bool = False,
    _include_goal_descriptors: bool = False,
    _goals: list[str] | None = None,
    **kwargs,
) -> OperationResult[Dict[str, Any]]:
    """Run parse/reduce/export/execute pipeline for a VoxLogicA program."""
    try:
        strategy_name = str(execution_strategy or "dask").strip().lower()
        if strategy_name not in {"", "dask"}:
            return OperationResult.fail(
                f"Unsupported execution strategy '{execution_strategy}'. Only 'dask' is available."
            )
        execution_strategy = "dask"
        syntax = _load_syntax(program, filename)
        workplan, declaration_bindings = reduce_program_with_bindings(syntax)
        policy_goal_scope = list(_goals or []) if execute and _goals else None
        enforce_workplan_policy_or_raise(
            workplan,
            legacy=bool(legacy),
            serve_mode=bool(serve_mode),
            goal_scope=policy_goal_scope,
        )

        cli_mode = bool(filename)
        execution_result = None
        prepared_plan = None
        if execute:
            engine: ExecutionEngine
            if no_cache:
                logger.info("No-cache mode enabled - results are not persisted")
                engine = ExecutionEngine(storage_backend=NoCacheStorageBackend())
                with runtime_policy_scope(serve_mode=bool(serve_mode)):
                    execution_result, prepared_plan = engine.execute_with_prepared(
                        workplan,
                        dask_dashboard=dask_dashboard,
                        strategy=execution_strategy,
                        goals=_goals,
                    )
            else:
                engine = ExecutionEngine()
                with runtime_policy_scope(serve_mode=bool(serve_mode)):
                    execution_result, prepared_plan = engine.execute_with_prepared(
                        workplan,
                        dask_dashboard=dask_dashboard,
                        strategy=execution_strategy,
                        goals=_goals,
                    )

            # Playground/value jobs need persisted pages to become immediately inspectable.
            # Keep non-blocking semantics for normal runs, but wait briefly in serve
            # scoped value-resolution paths so the UI does not get stuck in "queued".
            if (
                prepared_plan is not None
                and bool(serve_mode)
                and (_include_goal_descriptors or bool(_goals))
            ):
                try:
                    flushed = prepared_plan.materialization_store.flush(timeout_s=2.5)
                    if not flushed:
                        logger.warning(
                            "Persistence queue did not fully flush before returning serve payload"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Unable to flush persistence queue: %s", exc)

            if not execution_result.success:
                error_msg = (
                    "Execution failed with "
                    f"{len(execution_result.failed_operations)} errors"
                )
                if cli_mode:
                    logger.error(error_msg)
                    for node_id, message in execution_result.failed_operations.items():
                        logger.error("  %s...: %s", node_id[:8], message)
                failure_payload: dict[str, Any] = {
                    "operations": len(workplan.operations),
                    "goals": len(workplan.goals),
                    "task_graph": str(workplan),
                    "syntax": str(syntax),
                    "symbol_table": declaration_bindings,
                    "print_targets": [
                        {
                            "name": goal.name,
                            "node_id": goal.id,
                        }
                        for goal in workplan.goals
                        if goal.operation == "print"
                    ],
                    "execution": {
                        "success": execution_result.success,
                        "completed_operations": len(execution_result.completed_operations),
                        "failed_operations": len(execution_result.failed_operations),
                        "execution_time": execution_result.execution_time,
                        "total_operations": execution_result.total_operations,
                        "cache_summary": execution_result.cache_summary,
                        "errors": execution_result.failed_operations,
                        "error_details": _failed_operation_details(
                            workplan,
                            execution_result.failed_operations,
                        ),
                    },
                }
                if _include_execution_events:
                    failure_payload["execution"]["node_events"] = execution_result.node_events
                else:
                    failure_payload["execution"]["events_available"] = bool(execution_result.node_events)
                    failure_payload["execution"]["events_total"] = int(
                        (execution_result.cache_summary or {}).get(
                            "events_total",
                            len(execution_result.node_events),
                        )
                    )
                return OperationResult(
                    success=False,
                    data=_json_safe(failure_payload),
                    error=error_msg,
                )

        result: dict[str, Any] = {
            "operations": len(workplan.operations),
            "goals": len(workplan.goals),
            "task_graph": str(workplan),
            "syntax": str(syntax),
            "symbol_table": declaration_bindings,
            "print_targets": [
                {
                    "name": goal.name,
                    "node_id": goal.id,
                }
                for goal in workplan.goals
                if goal.operation == "print"
            ],
        }

        if execution_result is not None:
            execution_payload: dict[str, Any] = {
                "success": execution_result.success,
                "completed_operations": len(execution_result.completed_operations),
                "failed_operations": len(execution_result.failed_operations),
                "execution_time": execution_result.execution_time,
                "total_operations": execution_result.total_operations,
                "cache_summary": execution_result.cache_summary,
            }
            if _include_execution_events:
                execution_payload["node_events"] = execution_result.node_events
            else:
                execution_payload["events_available"] = bool(execution_result.node_events)
                execution_payload["events_total"] = int(
                    (execution_result.cache_summary or {}).get(
                        "events_total",
                        len(execution_result.node_events),
                    )
                )
            result["execution"] = execution_payload
            if execution_result.failed_operations:
                result["execution"]["errors"] = execution_result.failed_operations
                result["execution"]["error_details"] = _failed_operation_details(
                    workplan,
                    execution_result.failed_operations,
                )

            goal_results: list[dict[str, Any]] = []
            if prepared_plan is not None:
                declared_names_by_node: dict[str, str] = {}
                for declared_name, declared_node in declaration_bindings.items():
                    key = str(declared_node)
                    if key not in declared_names_by_node:
                        declared_names_by_node[key] = str(declared_name)

                target_nodes: list[dict[str, str]] = [
                    {
                        "operation": str(goal.operation),
                        "name": str(goal.name),
                        "node_id": str(goal.id),
                    }
                    for goal in workplan.goals
                ]
                seen_target_nodes = {entry["node_id"] for entry in target_nodes}
                for requested_node in _goals or []:
                    requested_id = str(requested_node)
                    if requested_id in seen_target_nodes:
                        continue
                    target_nodes.append(
                        {
                            "operation": "inspect",
                            "name": declared_names_by_node.get(requested_id, requested_id),
                            "node_id": requested_id,
                        }
                    )
                    seen_target_nodes.add(requested_id)

                for goal in target_nodes:
                    node_id = goal["node_id"]
                    goal_payload: dict[str, Any] = {
                        "operation": goal["operation"],
                        "name": goal["name"],
                        "node_id": node_id,
                    }
                    if node_id in execution_result.failed_operations:
                        goal_payload["status"] = "failed"
                        goal_payload["error"] = execution_result.failed_operations[node_id]
                    else:
                        try:
                            metadata = prepared_plan.materialization_store.metadata(node_id)
                            goal_payload["status"] = "materialized"
                            goal_payload["metadata"] = metadata
                            if _include_goal_descriptors:
                                try:
                                    from voxlogica.serve_support import describe_runtime_value

                                    runtime_value = prepared_plan.materialization_store.get(node_id)
                                    goal_payload["runtime_descriptor"] = describe_runtime_value(
                                        node_id=node_id,
                                        value=runtime_value,
                                        path="",
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    goal_payload["runtime_descriptor_error"] = str(exc)
                        except Exception as exc:  # noqa: BLE001
                            goal_payload["status"] = "unavailable"
                            goal_payload["error"] = str(exc)
                    goal_results.append(goal_payload)
            result["goal_results"] = goal_results

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

    except StaticPolicyError as exc:
        message = exc.diagnostics[0].message if exc.diagnostics else "Static policy violation"
        return OperationResult(
            success=False,
            data={"diagnostics": diagnostics_payload(exc.diagnostics)},
            error=message,
        )
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
                "help": "Execution strategy to use (dask only)",
            },
            "legacy": {
                "type": bool,
                "required": False,
                "default": False,
                "help": "Enable legacy mode (allow effectful primitives)",
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
                "execution_strategy": (Optional[str], "Execution strategy (dask only)"),
                "legacy": (Optional[bool], "Enable legacy mode (allow effectful primitives)"),
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
