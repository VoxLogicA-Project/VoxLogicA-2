"""Command-line entry point for the DAG-only VoxLogicA2 toolchain.

The CLI deliberately stays small: it reads a program, builds the DAG, optionally
exports the syntax or graph, and optionally executes the plan.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import logging
import subprocess
import sys
import time
from typing import Any

from voxlogica.converters.dot_converter import to_dot
from voxlogica.converters.json_converter import WorkPlanJSONEncoder, to_json
from voxlogica.execution import ExecutionEngine
from voxlogica.parser import ProgramParseError, parse_program_content
from voxlogica.reducer import StaticAnalysisError, reduce_program
from voxlogica.storage import NoCacheStorageBackend, SQLiteResultsDatabase, delete_results_store, results_store_paths
from voxlogica.repl import start_repl
from voxlogica import sharding

logger = logging.getLogger("voxlogica.main")


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def build_workplan(program_text: str, source_name: str = "<input>", for_expansion_cap: int = 4096):
    """Parse source text and reduce it into a symbolic work plan."""
    syntax = parse_program_content(program_text, source_name=source_name)
    return syntax, reduce_program(syntax, source_name=source_name, for_expansion_cap=for_expansion_cap)


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
            # Count only: the full node-id list is one entry per DAG node (tens of
            # thousands after loop expansion) and floods the terminal. The list
            # remains on execution_result.completed_operations for programmatic use.
            "completed_operations": len(execution_result.completed_operations),
            "failed_operations": execution_result.failed_operations,
            "execution_time": execution_result.execution_time,
            "total_operations": execution_result.total_operations,
            "cache_summary": execution_result.cache_summary,
        }
    return payload


def _confirm_yes(prompt: str) -> bool:
    """Return True when the user explicitly confirms with y/yes."""
    try:
        response = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return response in {"y", "yes"}


def _delete_cache_if_requested(args: argparse.Namespace) -> int | None:
    """Prompt to delete the persistent store; return an exit code when cancelled."""
    if not args.delete_cache:
        return None

    db_path, payload_dir = results_store_paths(args.store_db)
    if not _confirm_yes(f"Delete persistent cache at {db_path} and {payload_dir}?"):
        print("Cache deletion cancelled.")
        return 0

    delete_results_store(args.store_db)
    print(f"Deleted cache at {db_path} and {payload_dir}")
    return None


def _as_symbolic_plan(workplan):
    """Normalize reducer output to the symbolic IR sharding.classify_goals reads.

    Mirrors ExecutionEngine._to_symbolic_plan (which is a pure function of its
    argument, not really an instance method) without paying for a full engine
    construction just to reach it.
    """
    from voxlogica.lazy.ir import SymbolicPlan
    if isinstance(workplan, SymbolicPlan):
        return workplan
    if hasattr(workplan, "to_symbolic_plan"):
        return workplan.to_symbolic_plan()
    raise TypeError("expected SymbolicPlan or WorkPlan with to_symbolic_plan()")


def run_command(args: argparse.Namespace) -> int:
    """Implement the ``run`` subcommand."""
    _configure_logging(args.debug)
    cancelled = _delete_cache_if_requested(args)
    if cancelled is not None:
        return cancelled

    program_text = Path(args.filename).read_text(encoding="utf-8")
    try:
        syntax, workplan = build_workplan(program_text, source_name=args.filename,
                                          for_expansion_cap=args.for_expansion_cap)
    except ProgramParseError as exc:
        print(exc.format_block())
        return 2
    except StaticAnalysisError as exc:
        print(exc.format_block())
        return 2

    is_child = args._shard_index is not None or args._finalize_only

    _write_text(args.save_syntax, syntax.to_syntax())
    _write_text(args.save_task_graph, str(workplan))
    if args.save_task_graph_as_dot:
        _write_text(args.save_task_graph_as_dot, to_dot(workplan))
    if args.save_task_graph_as_json:
        _write_text(
            args.save_task_graph_as_json,
            json.dumps(to_json(workplan), indent=2, cls=WorkPlanJSONEncoder),
        )

    if args.execute and not is_child:
        # The one decision point: should this top-level invocation fork into
        # per-core shards instead of running single-process? See
        # sharding.decide's docstring for exactly when this fails safe to "no".
        shard_plan = sharding.decide(
            _as_symbolic_plan(workplan), no_cache=args.no_cache, explicit_shards=args.shards,
            profile_requested=args.profile is not None, is_child_invocation=False)
        if shard_plan.enabled:
            return _run_sharded(args, workplan, shard_plan)
        logger.debug("auto-shard: single-process (%s)", shard_plan.reason)

    goals_filter: list[str] | None = None
    if is_child:
        shardable, aggregate = sharding.classify_goals(_as_symbolic_plan(workplan))
        if args._finalize_only:
            goals_filter = [g.id for g in aggregate]
        else:
            shard = sharding.split_goals(shardable, args._shard_total)[args._shard_index]
            goals_filter = [g.id for g in shard]

    execution_result = None
    if args.execute:
        if args.profile is not None and not args.engine:
            logger.warning("--profile has no effect with --no-engine (lazy strategy doesn't support it)")
        storage = NoCacheStorageBackend() if args.no_cache else SQLiteResultsDatabase(
            db_path=args.store_db, max_bytes=int(args.cache_max_gb * 1024 ** 3))
        execution_result = ExecutionEngine(
            storage_backend=storage,
            no_cache=args.no_cache,
            use_engine=args.engine,
            threads=args.threads,
            engine_debug=args.engine_debug,
            dynamic_expansion=args.dynamic_expansion,
        ).execute_workplan(workplan, goals=goals_filter, apply_side_effects=is_child, profile=args.profile)
        if not is_child:  # a child's parent prints ONE combined summary instead
            print(json.dumps(_summary_payload(workplan, execution_result), indent=2))
            print(f"Execution time: {execution_result.execution_time:.2f} seconds")
        if not execution_result.success:
            logger.error("DAG execution failed")
            for node_id, error_msg in execution_result.failed_operations.items():
                logger.error(f"Node {node_id} failed:\n{error_msg}")
            return 1

    return 0


def _child_argv(args: argparse.Namespace, resolved_db: str, threads: int,
                extra: list[str]) -> list[str]:
    """Reconstruct a `voxlogica run` invocation for a sharding child/finalize pass.

    Forwards only what affects execution (not the parent-only export flags —
    --save-task-graph etc. — which the top-level invocation already handled).
    """
    argv = [sys.executable, "-m", "voxlogica.main", "run", args.filename,
           "--store-db", resolved_db,
           "--cache-max-gb", str(args.cache_max_gb),
           "--threads", str(threads),
           "--for-expansion-cap", str(args.for_expansion_cap)]
    if not args.engine:
        argv.append("--no-engine")
    if not args.dynamic_expansion:
        argv.append("--no-dynamic-expansion")
    if args.engine_debug:
        argv.append("--engine-debug")
    if args.debug:
        argv.append("--debug")
    return argv + extra


def _run_sharded(args: argparse.Namespace, workplan, shard_plan: "sharding.ShardPlan") -> int:
    """Fork shard_plan.shard_count children over disjoint goal shards, then one
    finalize pass for the aggregate goals, against a shared persistent cache.

    Children inherit this process's stdout/stderr directly, so their `print`
    goal side effects appear live and interleaved (harmless — goal print
    order was never a documented guarantee even single-process). Only ONE
    combined JSON summary is printed, by this orchestrator, at the end.
    """
    db_path, _ = results_store_paths(args.store_db)
    resolved_db = str(db_path)
    p, threads = shard_plan.shard_count, shard_plan.threads_per_shard
    print(f"[auto-shard] {shard_plan.reason} -> {p} shards x {threads} threads "
          f"(+1 finalize pass), shared cache: {resolved_db}", file=sys.stderr)

    # Initialize the shared db HERE, once, before forking anything: if the file
    # doesn't exist yet, schema creation + `PRAGMA journal_mode=WAL` need an
    # exclusive lock, which WAL's "one writer across processes" guarantee
    # doesn't cover — WAL isn't active yet. Two children racing to create the
    # same fresh db file can lose that race hard enough to crash (observed:
    # `sqlite3.OperationalError: database is locked` with no persistence
    # try/except to catch it, unlike the ordinary in-run write path). Doing it
    # once, single-threaded, right here means every child only ever connects
    # to an already-initialized, already-WAL db.
    SQLiteResultsDatabase(db_path=resolved_db, max_bytes=int(args.cache_max_gb * 1024 ** 3)).close()

    started = time.perf_counter()
    procs = []
    for i in range(p):
        argv = _child_argv(args, resolved_db, threads,
                           ["--_shard-index", str(i), "--_shard-total", str(p)])
        procs.append(subprocess.Popen(argv))

    failures = []
    for i, proc in enumerate(procs):
        rc = proc.wait()
        if rc != 0:
            failures.append(i)
    if failures:
        logger.error(f"auto-shard: shard(s) {failures} failed (exit != 0); "
                     f"skipping finalize pass, results are incomplete")
        return 1

    finalize_argv = _child_argv(args, resolved_db, detect_finalize_threads(args),
                                ["--_finalize-only"])
    finalize_rc = subprocess.run(finalize_argv).returncode
    elapsed = time.perf_counter() - started
    print(json.dumps({
        "nodes": len(workplan.nodes), "goals": len(workplan.goals),
        "auto_shard": {"shards": p, "threads_per_shard": threads,
                      "shardable_goals": shard_plan.shardable_goal_count,
                      "aggregate_goals": shard_plan.aggregate_goal_count},
        "execution": {"success": finalize_rc == 0, "execution_time": elapsed},
    }, indent=2))
    print(f"Execution time: {elapsed:.2f} seconds")
    return 1 if finalize_rc != 0 else 0


def detect_finalize_threads(args: argparse.Namespace) -> int:
    """The finalize pass runs alone (all shards done) — give it every core."""
    return args.threads or sharding.detect_cpu_count()


def list_primitives_command(_args: argparse.Namespace) -> int:
    """Implement the ``list-primitives`` subcommand."""
    engine = ExecutionEngine(no_cache=True)
    payload = {
        "namespaces": engine.primitives.list_namespaces(),
        "primitives": engine.primitives.list_primitives(),
    }
    print(json.dumps(payload, indent=2))
    return 0

def shell_command(args: argparse.Namespace) -> int:
    """Implement the ``repl`` subcommand."""
    start_repl()
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
    run_parser.add_argument(
        "--delete-cache",
        action="store_true",
        help="Delete the persistent results database and payload files before running (prompts for confirmation)",
    )
    run_parser.add_argument("--store-db", help="Path to the persistent results SQLite database")
    run_parser.add_argument("--cache-max-gb", type=float, default=100.0, metavar="GB",
                            help="Persistent cache byte budget in GB; LRU-evict past it (0 = unbounded)")
    run_parser.add_argument("--debug", action="store_true")
    run_parser.add_argument("--engine", action=argparse.BooleanOptionalAction, default=True,
                            help="Use the live computation engine (default); --no-engine selects the lazy strategy")
    run_parser.add_argument("--threads", type=int, default=0, metavar="N",
                            help="Concurrent kernels (default: CPU count)")
    run_parser.add_argument("--engine-debug", action="store_true",
                            help="On engine failure, dump the stuck node frontier")
    run_parser.add_argument("--dynamic-expansion", action=argparse.BooleanOptionalAction, default=True,
                            help="Unroll runtime-valued for-loops into parallel nodes (lazy strategy)")
    run_parser.add_argument("--for-expansion-cap", type=int, default=4096, metavar="N",
                            help="Max constant-loop static unroll length (0 disables)")
    run_parser.add_argument("--profile", nargs="?", const="", default=None, metavar="PATH",
                            help="Profile the run with cProfile (engine strategy only). "
                                 "Bare --profile prints top-30 cumulative+tottime to stderr; "
                                 "--profile=PATH dumps raw .pstats to PATH (open with "
                                 "pstats.Stats(PATH) or snakeviz).")
    run_parser.add_argument("--shards", type=int, default=None, metavar="P",
                            help="Auto-shard into P processes for full core utilization "
                                 "past the GIL (default: auto-detected from goal count/cores/RAM; "
                                 "--shards 1 disables sharding). Requires a persistent cache "
                                 "(not --no-cache) since shards share state through it.")
    # Internal flags used only by auto-sharding's own child/finalize subprocesses
    # (see voxlogica/sharding.py) — never set these by hand.
    run_parser.add_argument("--_shard-index", type=int, default=None, help=argparse.SUPPRESS)
    run_parser.add_argument("--_shard-total", type=int, default=None, help=argparse.SUPPRESS)
    run_parser.add_argument("--_finalize-only", action="store_true", help=argparse.SUPPRESS)
    run_parser.set_defaults(handler=run_command)

    list_parser = subparsers.add_parser("list-primitives", help="List primitive kernels.")
    list_parser.set_defaults(handler=list_primitives_command)

    shell_parser = subparsers.add_parser("repl", help="Start an interactive REPL session")
    shell_parser.set_defaults(handler=shell_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
