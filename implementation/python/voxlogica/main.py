"""Command-line entry point for the DAG-only VoxLogicA2 toolchain.

The CLI deliberately stays small: it reads a program, builds the DAG, optionally
exports the syntax or graph, and optionally executes the plan.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import logging
from typing import Any

from voxlogica.converters.dot_converter import to_dot
from voxlogica.converters.json_converter import WorkPlanJSONEncoder, to_json
from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import NoCacheStorageBackend, SQLiteResultsDatabase

logger = logging.getLogger("voxlogica.main")


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def build_workplan(program_text: str):
    """Parse source text and reduce it into a symbolic work plan."""
    syntax = parse_program_content(program_text)
    return syntax, reduce_program(syntax)


def _write_text(path: str | None, content: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _summary_payload(workplan, execution_result: Any | None) -> dict[str, Any]:
    """Create the compact machine-readable payload printed by the CLI."""
    payload: dict[str, Any] = {
        "nodes": len(workplan.nodes),
        "goals": len(workplan.goals),
        "imports": list(workplan.imported_namespaces),
    }
    if execution_result is not None:
        payload["execution"] = {
            "success": execution_result.success,
            "completed_operations": sorted(execution_result.completed_operations),
            "failed_operations": execution_result.failed_operations,
            "execution_time": execution_result.execution_time,
            "total_operations": execution_result.total_operations,
            "cache_summary": execution_result.cache_summary,
        }
    return payload


def run_command(args: argparse.Namespace) -> int:
    """Implement the ``run`` subcommand."""
    _configure_logging(args.debug)
    program_text = Path(args.filename).read_text(encoding="utf-8")
    syntax, workplan = build_workplan(program_text)

    _write_text(args.save_syntax, syntax.to_syntax())
    _write_text(args.save_task_graph, str(workplan))
    if args.save_task_graph_as_dot:
        _write_text(args.save_task_graph_as_dot, to_dot(workplan))
    if args.save_task_graph_as_json:
        _write_text(
            args.save_task_graph_as_json,
            json.dumps(to_json(workplan), indent=2, cls=WorkPlanJSONEncoder),
        )

    execution_result = None
    if args.execute:
        storage = NoCacheStorageBackend() if args.no_cache else SQLiteResultsDatabase(db_path=args.store_db)
        execution_result = ExecutionEngine(storage_backend=storage, no_cache=args.no_cache).execute_workplan(workplan)
        if not execution_result.success:
            logger.error("DAG execution failed")
            return 1

    print(json.dumps(_summary_payload(workplan, execution_result), indent=2))
    return 0


def list_primitives_command(_args: argparse.Namespace) -> int:
    """Implement the ``list-primitives`` subcommand."""
    engine = ExecutionEngine(no_cache=True)
    payload = {
        "namespaces": engine.primitives.list_namespaces(),
        "primitives": engine.primitives.list_primitives(),
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser and register all supported subcommands."""
    parser = argparse.ArgumentParser(prog="voxlogica", description="Build and execute VoxLogicA DAGs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Parse, build, export, and optionally execute a DAG.")
    run_parser.add_argument("filename", help="VoxLogicA program file")
    run_parser.add_argument("--save-task-graph")
    run_parser.add_argument("--save-task-graph-as-dot")
    run_parser.add_argument("--save-task-graph-as-json")
    run_parser.add_argument("--save-syntax")
    run_parser.add_argument("--execute", action=argparse.BooleanOptionalAction, default=True)
    run_parser.add_argument("--no-cache", action="store_true", help="Force recomputation without reading or writing the store")
    run_parser.add_argument("--store-db", help="Path to the persistent results SQLite database")
    run_parser.add_argument("--debug", action="store_true")
    run_parser.set_defaults(handler=run_command)

    list_parser = subparsers.add_parser("list-primitives", help="List primitive kernels.")
    list_parser.set_defaults(handler=list_primitives_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
