"""Command-line entry point for the DAG-only VoxLogicA2 toolchain.

The CLI deliberately stays small: it reads a program, builds the DAG, optionally
exports the syntax or graph, and optionally executes the plan.
"""

from __future__ import annotations

import os
import sys
import sysconfig


def _reexec_without_gil() -> None:
    """On a free-threaded build, re-launch with the GIL genuinely disabled.

    This interpreter is a free-threading build, but SimpleITK does not declare
    GIL-safety, so importing it silently re-enables the GIL process-wide —
    every run, every benchmark, the whole engine. That default costs measured
    throughput: the same cold BraTS eval-30 program runs 2:13:16 with the GIL
    off against 2:22:28 with it on, bit-identical results, and the percentile
    kernels alone go ~9x faster off-GIL in isolation (295 vs 33 iterations/s
    across 16 threads).

    Safety is not assumed, it was measured: 63/63 comparable unit tests and
    20/20 fan-out stress runs byte-identical under no-GIL execution, plus a
    full cold eval-30 reproducing `avg_oracle_brats021` exactly.

    This must run BEFORE the imports below, which pull SimpleITK in — once it
    is loaded the GIL is already back on and only a fresh process can undo it.
    Hence exec rather than a runtime switch. `sys._is_gil_enabled()` is useless
    as the trigger here: at this point it still reads False, because nothing
    has imported SimpleITK yet.

    An explicit `-X gil=...` or `PYTHON_GIL=...` from the operator is always
    respected and also breaks the exec loop.
    """
    if not sysconfig.get_config_var("Py_GIL_DISABLED"):
        return  # not a free-threading build: nothing to disable
    if sys._xoptions.get("gil") is not None or "PYTHON_GIL" in os.environ:
        return  # explicitly chosen (or this is the re-exec'd process)
    os.execv(sys.executable,
             [sys.executable, "-X", "gil=0", "-m", "voxlogica.main", *sys.argv[1:]])


def _tune_glibc_for_volumes() -> None:
    """Keep volume-sized allocations reusable instead of mmap/munmap'd.

    glibc serves any allocation above M_MMAP_THRESHOLD (default 128 KB) with a
    private mmap and returns it with munmap on free. Every volume this engine
    touches is 9-35 MB, so each alloc/free cycle unmaps the pages and the next
    allocation faults them back one by one, zeroed by the kernel. Measured on
    the 369 sweep: 881,158 minor faults/s and 20.3% system time -- about five
    of 24 cores doing nothing but page bookkeeping, with the fault traffic
    (faults x 4 KB ~ 3.4 GB/s) equal to the engine's entire measured value
    traffic, i.e. essentially every byte written landed on a fresh page.

    Raising the threshold keeps those blocks on the heap's free lists, where
    free/alloc is a pointer swap and the pages stay mapped and warm. The heap
    then holds freed volumes instead of returning them to the OS; that memory
    is bounded by the engine's own budget/ceiling machinery, which measures
    RSS-level usage (accounted_bytes) and throttles admission, so the
    retention is governed, not hidden.

    ctypes into libc, not an environment variable: the engine must self-tune
    on any host it lands on (AGENT.md: no env-var fixes). Non-Linux or a
    missing symbol is a silent no-op.
    """
    import ctypes
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        M_MMAP_THRESHOLD, M_TRIM_THRESHOLD = -3, -1
        libc.mallopt(M_MMAP_THRESHOLD, 64 * 1024 * 1024)
        libc.mallopt(M_TRIM_THRESHOLD, 256 * 1024 * 1024)
        # NOT M_MMAP_MAX. Tried and refuted: forbidding malloc's mmap, on the
        # theory that ITK's aligned image buffers were being served by mmap and
        # returned by munmap regardless of the threshold above, changed nothing.
        # A/B on the 60-case sweep, mallopt accepted (returns 1) in both arms:
        # 281,927 minor faults/s and 263 node/s with mmap allowed, 287,627 and
        # 268 with it forbidden. The faults are not malloc's mmap.
        #
        # And the faults are not the lever either: at a live budget where
        # throughput is nearly twice as high they are still 226,089/s. ~1 GB/s
        # of first-touch is simply what allocating this much memory costs. What
        # collapses throughput is DIRECT RECLAIM, and that is a function of how
        # close the process runs to the machine -- see governor._RSS_SHARE.
    except (OSError, AttributeError, TypeError):
        pass


if __name__ == "__main__":
    _reexec_without_gil()
    _tune_glibc_for_volumes()

import argparse
import faulthandler
import io
import time
from pathlib import Path
import json
import logging
import socket
from typing import Any
from dataclasses import replace

from voxlogica import __version__
from voxlogica.converters.dot_converter import to_dot
from voxlogica.converters.json_converter import WorkPlanJSONEncoder, to_json
from voxlogica.execution import ExecutionEngine
from voxlogica.parser import ProgramParseError, parse_program_content
from voxlogica.reducer import StaticAnalysisError, reduce_program
from voxlogica.storage import NoCacheStorageBackend, ReadOnlyStorageBackend, SQLiteResultsDatabase, delete_results_store, results_store_paths
from voxlogica.repl import start_repl
from voxlogica.diagnostics.classify import build_report
from voxlogica.diagnostics.render import (
    color_enabled,
    render_diagnostic,
    render_legacy_error_block,
    render_report_json,
)
from voxlogica.diagnostics.store import load_report, store_report
# Imports nothing heavier than the stdlib: fastapi/uvicorn are pulled in only
# when a UI is actually started.
from voxlogica.ui.server import DEFAULT_PORT as UI_DEFAULT_PORT

logger = logging.getLogger("voxlogica.main")


def _configure_logging(debug: bool, stream=None) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s: %(message)s",
        # `voxlogica mcp` speaks the protocol on stdout, so its logs must go the
        # other way or the first log line breaks the session.
        stream=stream,
    )


def build_workplan(program_text: str, source_name: str = "<input>", for_expansion_cap: int = 4096):
    """Parse source text and reduce it into a symbolic work plan."""
    syntax = parse_program_content(program_text, source_name=source_name)
    workplan = reduce_program(syntax, source_name=source_name, for_expansion_cap=for_expansion_cap)
    workplan.source_text = program_text
    return syntax, workplan


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
            "diagnostics": [diagnostic.to_dict() for diagnostic in execution_result.diagnostics],
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


def _start_ui(args: argparse.Namespace, *, program: str | None):
    """Bring the browser UI up, or return None if it is disabled or unavailable.

    The UI is never allowed to break a run: a failure to bind a port or import
    the server is logged and the computation proceeds without it.
    """
    if not getattr(args, "serve", False) or os.environ.get("VOXLOGICA_NO_UI"):
        return None
    try:
        from voxlogica.ui import start_ui
    except ImportError as exc:  # fastapi/uvicorn absent in a minimal install
        logger.warning("UI unavailable (%s); continuing without it", exc)
        return None
    try:
        session = start_ui(
            port=args.ui_port,
            open_browser=getattr(args, "open_browser", False),
            store_db=getattr(args, "store_db", None),
            instance_info={
                "version": __version__,
                "program": program,
                "storeDb": getattr(args, "store_db", None),
            },
            # The program under run is the workspace document: opening the UI on
            # a run shows the cards that file describes, or the whole program as
            # one card when it has never been arranged.
            program=Path(program) if program else None,
        )
    except Exception as exc:  # noqa: BLE001 - the UI is optional, the run is not
        logger.warning("Could not start the UI (%s); continuing without it", exc)
        return None
    print(f"[voxlogica] UI at {session.url}", file=sys.stderr)
    if session.dev_url is not None:
        print(f"[voxlogica] dev page at {session.dev_url} (or press Cmd/Ctrl+.)",
              file=sys.stderr)
    return session


def run_command(args: argparse.Namespace) -> int:
    """Implement the ``run`` subcommand."""
    _configure_logging(args.debug)
    cancelled = _delete_cache_if_requested(args)
    if cancelled is not None:
        return cancelled

    ui = _start_ui(args, program=args.filename)
    aborted = False
    try:
        return _run_command_inner(args, ui)
    except BaseException as exc:
        aborted = True
        # An unhandled error is rendered by main()'s CLI boundary -- which is
        # downstream of the finally below. Waiting for a browser there would
        # leave the terminal silent and apparently hung, with the UI still
        # showing "running", until someone thought to close the tab.
        _publish(ui, {"status": "failed", "program": args.filename,
                      "finishedAt": time.time(),
                      "error": "interrupted" if isinstance(exc, KeyboardInterrupt)
                               else f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        if ui is not None:
            if aborted:
                # Ctrl-C, or a crash, means "stop now".
                ui.stop()
            else:
                # Exit now if nobody is watching; otherwise keep serving until
                # the last browser disconnects, so a run you were looking at
                # does not disappear the moment it ends.
                ui.serve_until_idle()


def _publish(ui, run: dict) -> None:
    """Push a run state change to the UI, if one is up."""
    if ui is not None:
        ui.publish({"type": "run", "run": run}, sticky_key="run")


def _run_command_inner(args: argparse.Namespace, ui) -> int:
    try:
        program_text = Path(args.filename).read_text(encoding="utf-8")
    except OSError as exc:
        _render_exception(exc, args)
        return 3
    try:
        syntax, workplan = build_workplan(program_text, source_name=args.filename,
                                          for_expansion_cap=args.for_expansion_cap)
    except ProgramParseError as exc:
        print(render_legacy_error_block(exc.format_block(), color=color_enabled(stream=sys.stderr)), file=sys.stderr)
        return 2
    except StaticAnalysisError as exc:
        print(render_legacy_error_block(exc.format_block(), color=color_enabled(stream=sys.stderr)), file=sys.stderr)
        return 2

    if getattr(args, "typecheck", True):
        result = _typecheck(workplan)
        _print_type_warnings(result)
        if not result.ok:
            print(
                render_legacy_error_block(
                    StaticAnalysisError(result.diagnostics).format_block(),
                    color=color_enabled(stream=sys.stderr),
                ),
                file=sys.stderr,
            )
            return 2

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
        if args.measure is not None and not args.engine:
            logger.warning("--measure has no effect with --no-engine (only the engine is instrumented)")
        if args.no_cache:
            storage = NoCacheStorageBackend()
        else:
            storage = SQLiteResultsDatabase(
                db_path=args.store_db,
                max_bytes=int(args.cache_max_gb * 1024 ** 3) if args.cache_max_gb else None)
            if args.no_write_cache:
                # Reads still hit, writes are dropped and counted. The control
                # for "how much of this run is the write path" -- see
                # ReadOnlyStorageBackend for why --no-cache cannot answer that.
                storage = ReadOnlyStorageBackend(storage)
        _publish(ui, {"status": "running", "program": args.filename,
                      "startedAt": time.time(), "summary": None, "elapsed": None})
        execution_result = ExecutionEngine(
            storage_backend=storage,
            no_cache=args.no_cache,
            strategy=args.engine,
            threads=args.threads,
            threads_auto=args.threads_auto,
            engine_debug=args.engine_debug,
            dynamic_expansion=args.dynamic_expansion,
            sparse_cache=args.sparse_cache,
            # What makes a result card say `computing` while it computes. None
            # when nobody is serving a UI, which is the case that must stay
            # free: an observer that is `None` is not called at all.
            observe=ui.results.observe if ui is not None and ui.results else None,
        ).execute_workplan(workplan, measure=args.measure,
                             measure_period=args.measure_period,
                             measure_series=args.measure_series,
                             control=args.control,
                             control_eval=args.control_eval,
                             verify=args.verify)
        if not execution_result.success:
            for diagnostic in execution_result.diagnostics:
                _render_diagnostic(diagnostic, args)
                if args.error_details and diagnostic.details_id:
                    stored = load_report(diagnostic.details_id)
                    if stored:
                        print(stored, file=sys.stderr)
            if not execution_result.diagnostics:
                _render_exception(RuntimeError("DAG execution failed"), args)
            _publish(ui, {"status": "failed", "program": args.filename,
                          "finishedAt": time.time(),
                          "elapsed": execution_result.execution_time})
            return 1
        summary = _summary_payload(workplan, execution_result)
        _publish(ui, {"status": "completed", "program": args.filename,
                      "finishedAt": time.time(), "summary": summary,
                      "elapsed": execution_result.execution_time})
        print(json.dumps(summary, indent=2))
        print(f"Execution time: {execution_result.execution_time:.2f} seconds")

    return 0


def _typecheck(workplan):
    """Run the type checker over a reduced plan as an abstract execution.

    Deliberately shares nothing with the executor but the plan itself: the
    checker walks the same DAG with types where the engine puts values, so a
    program that is rejected here would have failed there.
    """
    from voxlogica.analysis.type_checker import TypeChecker

    return TypeChecker(workplan.registry).check_plan(workplan.to_symbolic_plan())


def _print_type_warnings(result) -> None:
    """Report where the checker widened to ``any`` instead of looking further.

    These never fail a run: a limit of the analysis is not a defect in the
    program. They go to stderr so a clean stdout stays machine-readable.
    """
    for warning in result.warnings:
        prefix = warning.code
        if warning.location:
            prefix = f"{prefix} at {warning.location}"
        print(f"{prefix}: {warning.message}", file=sys.stderr)


def typecheck_command(args: argparse.Namespace) -> int:
    """Implement the ``typecheck`` subcommand: abstract execution, no run."""
    _configure_logging(args.debug)
    try:
        program_text = Path(args.filename).read_text(encoding="utf-8")
    except OSError as exc:
        _render_exception(exc, args)
        return 3
    try:
        _, workplan = build_workplan(program_text, source_name=args.filename,
                                     for_expansion_cap=args.for_expansion_cap)
    except ProgramParseError as exc:
        print(render_legacy_error_block(exc.format_block(), color=color_enabled(stream=sys.stderr)), file=sys.stderr)
        return 2
    except StaticAnalysisError as exc:
        print(render_legacy_error_block(exc.format_block(), color=color_enabled(stream=sys.stderr)), file=sys.stderr)
        return 2

    result = _typecheck(workplan)
    goal_types = {name: str(vox_type) for name, vox_type in result.goal_types.items()}

    if args.json:
        print(json.dumps({
            "ok": result.ok,
            "goals": goal_types,
            "diagnostics": [
                {"code": d.code, "message": d.message, "location": d.location, "symbol": d.symbol}
                for d in result.diagnostics
            ],
            "warnings": [
                {"code": w.code, "message": w.message, "location": w.location, "symbol": w.symbol}
                for w in result.warnings
            ],
        }, indent=2))
    elif goal_types:
        for name, rendered in goal_types.items():
            print(f"{name}: {rendered}")
    else:
        # Nothing is demanded, so nothing was executed abstractly -- exactly what
        # `run` would do with this program. Say so rather than printing nothing.
        print("No goals to check (the program has no print or save command).")

    if not args.json:
        _print_type_warnings(result)

    if not result.ok:
        if not args.json:
            print(
                render_legacy_error_block(
                    StaticAnalysisError(result.diagnostics).format_block(),
                    color=color_enabled(stream=sys.stderr),
                ),
                file=sys.stderr,
            )
        return 2
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

def mcp_command(args: argparse.Namespace) -> int:
    """The stdio bridge an MCP client launches.

    Registered automatically with the clients installed on this machine (see
    voxlogica/ui/registration.py), so an agent finds the workspace without
    anybody hand-editing a config file. It names no port: it looks up the live
    instance every time it is asked something.
    """
    # Never to stdout: stdout is the MCP transport, and one stray print would
    # corrupt the protocol.
    _configure_logging(getattr(args, "debug", False), stream=sys.stderr)
    from voxlogica.ui.bridge import main as bridge_main

    return bridge_main()


def serve_command(args: argparse.Namespace) -> int:
    """Implement the ``serve`` subcommand: the UI with no computation attached.

    Stops when the last window closes, the way an application does. A server
    outliving every window it was opened for is a process nobody knows to stop
    -- and each one holds a WebGL context per volume card, which is a cost that
    keeps being paid for a page nobody is looking at.

    ``--stay`` is for the case where that is wrong on purpose: a shared instance,
    a machine somebody connects to more than once, a session left open between
    two visits. It has to be asked for, because the harm of guessing runs the
    other way -- a server that lingers is invisible.
    """
    _configure_logging(args.debug)
    from voxlogica.ui import start_ui

    session = start_ui(
        port=args.ui_port,
        open_browser=args.open_browser,
        workspace_path=_workspace_path(args),
        store_db=args.store_db,
        instance_info={"version": __version__, "program": None, "storeDb": args.store_db},
    )
    how = "Ctrl-C to stop" if args.stay else "stops when the last window closes"
    print(f"[voxlogica] UI at {session.url} ({how})", file=sys.stderr)
    if session.dev_url is not None:
        print(f"[voxlogica] dev page at {session.dev_url} (or press Cmd/Ctrl+.)",
              file=sys.stderr)
    if args.stay:
        session.serve_forever()
    else:
        session.serve_until_closed()
    return 0


def _workspace_path(args: argparse.Namespace):
    """The file this session works on: the one named, then the last one open.

    `None` when there is neither, and that is a real state rather than a
    problem to paper over -- the window offers to make one. Starting up used to
    create a file named after the minute, every time, so a week of opening the
    application left a week of empty documents nobody asked for.
    """
    from voxlogica.ui import home

    named = getattr(args, "workspace", None)
    if named:
        return Path(named).expanduser()
    return home.last_opened()


def open_command(args: argparse.Namespace) -> int:
    """Implement the bare ``voxlogica``: a workspace, in a window, now.

    Quits when the window is closed, the way an application does. Nothing is
    asked on the way in and nothing is left behind if nothing was written: the
    scratch file appears the first time there is something to put in it.
    """
    _configure_logging(getattr(args, "debug", False))
    from voxlogica.ui import start_ui, window

    path = _workspace_path(args)
    session = start_ui(
        port=args.ui_port,
        open_browser=False,
        workspace_path=path,
        store_db=getattr(args, "store_db", None),
        instance_info={"version": __version__, "program": None,
                       "storeDb": getattr(args, "store_db", None)},
    )
    print(f"[voxlogica] {path}", file=sys.stderr)
    print(f"[voxlogica] UI at {session.url}", file=sys.stderr)

    if window.native_available():
        # The system's own web view, on this thread, for the rest of the
        # process's life. Nothing is inferred: the call returns when the window
        # closes, and that is the application ending.
        from voxlogica.ui import home

        try:
            window.run_native(
                session.url,
                storage=home.window_state_path(),
            )
        finally:
            session.stop()
        return 0

    # No web view runtime here. Somebody else's window then, and its lifetime
    # read off the hub: an application that outlived its only window would be a
    # process nobody knows to stop.
    how = window.open_window(session.url)
    if how == "browser":
        print("[voxlogica] no application window available; opened in your browser",
              file=sys.stderr)
    elif how == "none":
        # Nothing on this machine can hold a window -- no display, typically an
        # ssh session on a compute server. The workspace is running regardless,
        # and the only thing missing is a way to reach it, so say what that is.
        # Waiting the usual half-minute for a window that was never opened would
        # shut the server down just as the person finished reading how to
        # connect to it, which is why the patience is dropped here: this one
        # ends on Ctrl-C, the way any other server does.
        port = session.server.port
        print("[voxlogica] no display here, so no window to open.", file=sys.stderr)
        print(f"[voxlogica] open {session.url} yourself. Over ssh, forward the port first:",
              file=sys.stderr)
        print(f"[voxlogica]     ssh -L {port}:127.0.0.1:{port} {socket.gethostname()}",
              file=sys.stderr)
        print("[voxlogica] serving until interrupted (Ctrl-C).", file=sys.stderr)
        session.serve_until_closed(patience=float("inf"))
        return 0
    session.serve_until_closed()
    return 0


def shell_command(args: argparse.Namespace) -> int:
    """Implement the ``repl`` subcommand."""
    start_repl()
    return 0


def errors_command(args: argparse.Namespace) -> int:
    """Print a stored technical diagnostic report."""
    report = load_report(args.details_id)
    if report is None:
        print(f"No diagnostic report found for {args.details_id}", file=sys.stderr)
        return 2
    print(report)
    return 0


def _render_diagnostic(diagnostic, args: argparse.Namespace) -> None:
    if getattr(args, "error_format", "human") == "json":
        print(json.dumps(diagnostic.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
    else:
        print(render_diagnostic(diagnostic, color=color_enabled(stream=sys.stderr)), file=sys.stderr)


def _render_exception(exc: BaseException, args: argparse.Namespace) -> None:
    report = build_report(exc)
    diagnostic = replace(report.diagnostic, details_id=store_report(report))
    _render_diagnostic(diagnostic, args)
    if getattr(args, "error_details", False) or getattr(args, "debug", False):
        if getattr(args, "error_format", "human") == "json":
            print(render_report_json(report), file=sys.stderr)
        else:
            print(report.traceback_text, file=sys.stderr)


def calibrate_command(args: argparse.Namespace) -> int:
    """DEPRECATED. Implement the ``calibrate`` subcommand: measure this host's actual
    optimal thread count instead of relying on engine/topology.py's
    heuristic, AND (at that worker count) the ITK internal thread count that
    works best alongside it -- no fixed formula for that second value survives
    across hosts/worker-counts, per manuscripts/engine-scaling-2026-07.md Part
    I sec 5 and Part II sec 10-11, so it is measured the same way. See
    engine/calibration.py's module docstring for the full rationale
    (idle-gated, min-of-N interleaved sweep, machine-fingerprinted cache)."""
    from voxlogica.engine.calibration import run_calibration

    try:
        result = run_calibration(
            n_cases=args.n_cases, reps=args.reps, force_ignore_idle=args.force,
            progress=lambda msg: print(msg, file=sys.stderr),
        )
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    print(json.dumps({
        "chosen_threads": result["chosen_threads"],
        "candidates_wall_seconds": {str(k): round(v, 3) for k, v in result["candidates_wall_seconds"].items()},
        "chosen_itk_threads": result["chosen_itk_threads"],
        "itk_candidates_wall_seconds": {
            str(k): round(v, 3) for k, v in result["itk_candidates_wall_seconds"].items()
        },
    }, indent=2))
    print(f"\nCalibration saved for this machine. Future runs with --threads 0 "
          f"(the default) will use {result['chosen_threads']} threads and "
          f"{result['chosen_itk_threads']} ITK-internal threads automatically.",
          file=sys.stderr)
    return 0

def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser and register all supported subcommands."""
    parser = argparse.ArgumentParser(prog="voxlogica", description="Build and execute VoxLogicA DAGs.")
    # Not required: `voxlogica` on its own is the application, and an
    # application that answers a bare invocation with a usage error is one that
    # thinks the CLI is the point.
    subparsers = parser.add_subparsers(dest="command")
    parser.add_argument("--ui-port", type=int, default=UI_DEFAULT_PORT, metavar="PORT",
                        help=f"First port to try for the UI (default: {UI_DEFAULT_PORT})")
    parser.add_argument("--debug", action="store_true")
    parser.set_defaults(handler=open_command, workspace=None, store_db=None)

    run_parser = subparsers.add_parser("run", help="Parse, build, export, and optionally execute a DAG.")
    run_parser.add_argument("filename", help="VoxLogicA program file")
    run_parser.add_argument("--save-task-graph")
    run_parser.add_argument("--save-task-graph-as-dot")
    run_parser.add_argument("--save-task-graph-as-json")
    run_parser.add_argument("--save-syntax")
    run_parser.add_argument("--execute", action=argparse.BooleanOptionalAction, default=True)
    run_parser.add_argument("--typecheck", action=argparse.BooleanOptionalAction, default=True,
                            help="Type-check the plan before running it (default). The check is "
                                 "gradual -- a primitive that declares no type rule constrains "
                                 "nothing -- so it only rejects a mismatch it can prove.")
    run_parser.add_argument("--no-cache", action="store_true", help="Force recomputation without reading or writing the store")
    run_parser.add_argument("--no-write-cache", action="store_true",
                            help="Read from the store, write nothing. Payload files, SQLite "
                                 "inserts, the persist queue, spilling and the byte budget are "
                                 "bypassed, while a value already in the store is still served, "
                                 "so a warm run stays warm and the recompute count is unchanged. "
                                 "The control for 'how much of this run is the write path': "
                                 "--no-cache cannot answer that, because it also removes every "
                                 "read hit AND turns every evicted value into a recomputation, "
                                 "and those three effects move the wall clock in opposite "
                                 "directions. Ignored with --no-cache.")
    run_parser.add_argument(
        "--delete-cache",
        action="store_true",
        help="Delete the persistent results database and payload files before running (prompts for confirmation)",
    )
    run_parser.add_argument("--store-db", help="Path to the persistent results SQLite database")
    run_parser.add_argument(
        "--sparse-cache", action="store_true",
        help="Persist less, on two independent grounds. (1) Skip values that "
             "are already dead: every consumer has run, so nothing in this run "
             "can ask for them again and only a later run could reuse them. "
             "(2) Skip values that would have to WAIT for a writer -- if every "
             "writer is busy and work is already queued, the value is dropped "
             "rather than made to join the line, so persistence never costs "
             "the run time. Only values the engine can rebuild are dropped, "
             "ever. Measured on a 60-case sweep: (1) alone cut writes from 350 "
             "to 314 MB/s, because the writers kept up and the values were "
             "still live when their batch came up; (2) does not care whether a "
             "value is live. Use for a large parameter sweep whose "
             "intermediates are read once; leave off for iterative work, where "
             "cross-run reuse is the point.")
    run_parser.add_argument("--cache-max-gb", type=float, default=0.0, metavar="GB",
                            help="Persistent cache byte budget in GB; LRU-evict past it. "
                                 "Default 0 = size it automatically from free disk (the cache is the "
                                 "engine's spill space, so a fixed budget smaller than a run's working "
                                 "set breaks the memory bound).")
    run_parser.add_argument("--debug", action="store_true")
    run_parser.add_argument("--error-details", action="store_true",
                            help="Show the technical exception chain and traceback after the concise error")
    run_parser.add_argument("--error-format", choices=["human", "json"], default="human",
                            help="Render runtime diagnostics for people (default) or tools")
    from voxlogica.execution_strategy import registry as _strategies
    run_parser.add_argument("--engine", nargs="?", metavar="NAME",
                            choices=sorted(_strategies.available()),
                            const=_strategies.DEFAULT, default=_strategies.DEFAULT,
                            help="Which runtime evaluates the program: "
                                 + "; ".join(f"{name} -- {what}"
                                             for name, what in _strategies.available().items()))
    run_parser.add_argument("--no-engine", dest="engine", action="store_const", const="lazy",
                            help="Older spelling of --engine lazy")
    run_parser.add_argument("--threads", type=int, default=0, metavar="N",
                            help="Concurrent kernels (default: 0 = auto-detect, see --threads-auto)")
    run_parser.add_argument("--threads-auto", choices=["balanced", "p-cores", "logical"],
                            default="logical",
                            help="Auto-detection heuristic used when --threads is 0 (engine strategy "
                                 "only), on a hybrid Intel P/E CPU. 'logical' (default) uses the plain "
                                 "CPU count. 'balanced' uses every P-core plus half the E-cores: it was "
                                 "the default, from a measurement on the previous engine where 16 "
                                 "threads beat 24 (6.85s vs 7.84s). That no longer reproduces -- "
                                 "re-measured on the AIIM threshold sweep, 24 threads is 24.60s against "
                                 "25.09s at 16, a 2%% gap inside an 8%% drift between repetitions, and "
                                 "about 1900%% of CPU is busy even at --threads 8 because the kernels "
                                 "are internally parallel. 'p-cores' uses only performance cores: "
                                 "slower, but ~2.5x less CPU and RAM, the right choice on a shared box. "
                                 "All three collapse to the plain CPU count on a non-hybrid CPU or "
                                 "non-Linux host. Ignored when --threads is nonzero. See "
                                 "doc/dev/free-threaded-handover.md.")
    run_parser.add_argument("--engine-debug", action="store_true",
                            help="On engine failure, dump the stuck node frontier")
    run_parser.add_argument("--dynamic-expansion", action=argparse.BooleanOptionalAction, default=True,
                            help="Unroll runtime-valued for-loops into parallel nodes (lazy strategy)")
    run_parser.add_argument("--for-expansion-cap", type=int, default=4096, metavar="N",
                            help="Max constant-loop static unroll length (0 disables)")
    run_parser.add_argument("--measure", nargs="?", const="measurement.json", default=None,
                            metavar="PATH",
                            help="Write one self-describing performance measurement to PATH "
                                 "(default measurement.tsv). Off by default and free when off: "
                                 "no sampler thread and no branch on any dispatch path. The file "
                                 "carries a JSON header (commit, host, CPU model, core count, "
                                 "GIL state, flags, achieved sampling interval, and what the "
                                 "instrument itself cost) plus a per-sample series; the total-CPU "
                                 "figure comes from getrusage read once at start and once at "
                                 "exit, so it has no sampling error. Rates in the series must be "
                                 "computed from consecutive timestamps, never from the nominal "
                                 "period -- doing the latter overstated CPU by 39%%. See "
                                 "engine/measure.py and tools/measure/.")
    run_parser.add_argument("--measure-period", type=float, default=None, metavar="SECONDS",
                            help="Sampling period for --measure (default 0.25 s). Lower resolves "
                                 "shorter stalls at a higher instrument cost, which the report "
                                 "states; the totals are unaffected either way, since they come "
                                 "from getrusage rather than from the samples.")
    run_parser.add_argument("--measure-series", action="store_true",
                            help="Also write the raw per-sample rows beside the report, as "
                                 "<report>.samples.tsv. For plotting; every figure the report "
                                 "quotes is already derived in the report itself.")
    run_parser.add_argument("--verify", nargs="?", const="report", default=None,
                            choices=["report", "strict"],
                            help="Check the scheduler's progress invariant while the "
                                 "run proceeds (engine/verify.py). Three clauses: (P) a "
                                 "frontier node waits only for something that is being "
                                 "produced; (T) the frontier is empty exactly when no "
                                 "run-completion units remain; (V) a settled goal "
                                 "resolves to the bottom without further scheduling. "
                                 "Ten of the eleven scheduler defects fixed on "
                                 "2026-09-10 violate (P), and each surfaced hours later "
                                 "at whichever goal happened to need the node. "
                                 "'report' (the default when the flag is given) prints "
                                 "counterexamples and continues; 'strict' raises. Off "
                                 "entirely by default and free when off: the completion "
                                 "path costs one `is None` test.")
    run_parser.add_argument("--control", nargs="?", const="voxlogica.ctl", default=None,
                            metavar="SOCKET",
                            help="Serve a live control channel on this unix socket "
                                 "(default voxlogica.ctl). It answers Domain.method "
                                 "requests as newline-delimited JSON -- "
                                 "Runtime.describe, Probe.get, Knob.list/get/set, "
                                 "Series.start/stop, Measure.write -- so a running "
                                 "sweep can be interrogated and retuned without "
                                 "being restarted. Independent of --measure. Costs "
                                 "nothing while no client is connected (one daemon "
                                 "thread blocked in accept). Every accepted Knob.set "
                                 "is stamped into the report, because a run whose "
                                 "parameters moved while it ran is not comparable "
                                 "with one whose did not. Client: tools/measure/ctl.py.")
    run_parser.add_argument("--control-eval", action="store_true",
                            help="Allow Runtime.eval on the control channel. Off by "
                                 "default: it executes arbitrary code inside the "
                                 "measured process, so it perturbs the run and can "
                                 "wedge it. Every eval is recorded in the report. Use "
                                 "it to read something no probe exposes, then add the "
                                 "probe.")
    run_parser.add_argument("--serve", action=argparse.BooleanOptionalAction, default=True,
                            help="Serve the browser UI alongside the run (default). The process "
                                 "exits when the run ends if no browser is connected, and keeps "
                                 "serving until the last one disconnects otherwise. Set "
                                 "VOXLOGICA_NO_UI=1 to disable it for a whole shell.")
    run_parser.add_argument("--ui-port", type=int, default=UI_DEFAULT_PORT, metavar="PORT",
                            help=f"First port to try for the UI (default: {UI_DEFAULT_PORT}); "
                                 "concurrent runs take the next free one, and each prints its URL")
    run_parser.add_argument("--open", dest="open_browser", action="store_true",
                            help="Open the UI in a browser once it is up")
    run_parser.set_defaults(handler=run_command)

    serve_parser = subparsers.add_parser(
        "serve", help="Serve the browser UI with no computation attached (Ctrl-C to stop).")
    serve_parser.add_argument("workspace", nargs="?",
                              help="An .imgql workspace to open (default: a new one)")
    serve_parser.add_argument("--ui-port", type=int, default=UI_DEFAULT_PORT, metavar="PORT",
                              help=f"First port to try (default: {UI_DEFAULT_PORT})")
    serve_parser.add_argument("--open", dest="open_browser", action="store_true",
                              help="Open the UI in a browser once it is up")
    serve_parser.add_argument("--store-db", help="Path to the persistent results SQLite database")
    serve_parser.add_argument("--stay", action="store_true",
                              help="Keep serving after the last window closes "
                                   "(default: stop, the way an application does)")
    serve_parser.add_argument("--debug", action="store_true")
    serve_parser.set_defaults(handler=serve_command)

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Speak MCP on stdio for whichever VoxLogicA instance is running.")
    mcp_parser.add_argument("--debug", action="store_true")
    mcp_parser.set_defaults(handler=mcp_command)

    typecheck_parser = subparsers.add_parser(
        "typecheck",
        help="Type-check a program by executing its plan abstractly, without running it.")
    typecheck_parser.add_argument("filename", help="VoxLogicA program file")
    typecheck_parser.add_argument("--json", action="store_true",
                                  help="Emit goal types and diagnostics as JSON")
    typecheck_parser.add_argument("--for-expansion-cap", type=int, default=4096, metavar="N",
                                  help="Reducer loop-unrolling cap (matches `run`)")
    typecheck_parser.add_argument("--debug", action="store_true")
    typecheck_parser.add_argument("--error-details", action="store_true")
    typecheck_parser.add_argument("--error-format", choices=["human", "json"], default="human")
    typecheck_parser.set_defaults(handler=typecheck_command)

    list_parser = subparsers.add_parser("list-primitives", help="List primitive kernels.")
    list_parser.set_defaults(handler=list_primitives_command)

    shell_parser = subparsers.add_parser("repl", help="Start an interactive REPL session")
    shell_parser.set_defaults(handler=shell_command)

    errors_parser = subparsers.add_parser("errors", help="Inspect a stored technical diagnostic report")
    errors_subparsers = errors_parser.add_subparsers(dest="errors_command", required=True)
    errors_show_parser = errors_subparsers.add_parser("show", help="Show one diagnostic report")
    errors_show_parser.add_argument("details_id", help="Diagnostic id printed by a failed run")
    errors_show_parser.set_defaults(handler=errors_command)

    calibrate_parser = subparsers.add_parser(
        "calibrate",
        help="DEPRECATED -- measures a thread-count optimum that is not worth acting on. "
             "Measured on the reference host: its own winner (20 workers) beat the shipped "
             "heuristic (16) by 0.1%%, i.e. noise, and it records no ITK thread count at all, "
             "so the value it caches changes nothing. Leaving ITK unpinned at the shipped "
             "defaults measured fastest. Kept for re-measurement on new hardware.")
    calibrate_parser.add_argument("--n-cases", type=int, default=16, metavar="N",
                                   help="Synthetic cases per candidate thread count (default: 16 -- "
                                        "needs to be large enough that each candidate's run takes "
                                        "several seconds, or the bandwidth-saturation signal this is "
                                        "measuring is smaller than run-to-run noise; see "
                                        "engine/calibration.py's module docstring)")
    calibrate_parser.add_argument("--reps", type=int, default=3, metavar="N",
                                   help="Repetitions per candidate, interleaved, min taken (default: 3)")
    calibrate_parser.add_argument("--force", action="store_true",
                                   help="Skip the idle check and calibrate even if the machine looks busy "
                                        "(results will be less trustworthy)")
    calibrate_parser.set_defaults(handler=calibrate_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    # A native crash (SIGSEGV/SIGABRT inside ITK or a numpy view over a recycled
    # buffer) otherwise leaves NOTHING to debug: the process dies with a bare
    # signal and the log ends mid-progress-bar. faulthandler costs nothing while
    # the process is healthy — it only installs signal handlers — and prints the
    # C-level Python traceback of every thread at the moment of the fault, which
    # is the difference between diagnosing such a crash and guessing at it.
    try:
        faulthandler.enable()
    except (ValueError, AttributeError, io.UnsupportedOperation):
        # stderr is captured or replaced (pytest, an embedding host) and has no
        # real file descriptor to write a fault report to. Crash diagnostics are
        # a nicety; refusing to start the CLI over them is not.
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    # Deprecations are only useful if the person running the command sees them.
    if getattr(args, "command", None) == "calibrate":
        print("[voxlogica] WARNING: calibrate is DEPRECATED. On the reference host its winner beat "
              "the shipped heuristic by 0.1% (noise) and it caches no ITK thread count, so it "
              "changes nothing. The shipped defaults measured fastest.", file=sys.stderr)
    try:
        return int(args.handler(args))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # final CLI boundary: never dump an implementation traceback by default
        _render_exception(exc, args)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
