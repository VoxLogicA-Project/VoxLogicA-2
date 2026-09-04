"""Bounded loop unrolling: a wide independent fan-out must not make the whole
DAG live at once.

Admission is demand-driven (queue-depth), not merely window-capped: a loop
body is admitted only when the ready queue would otherwise starve the
workers, regardless of how large the window is. So even a huge window does
not make the whole fan-out live at once — the frontier tracks how many ready
nodes are needed to keep the workers fed, not the window's size. This bounds
the set of incomplete ("live") nodes, so a bounded cache always has *dead*
values to evict rather than being forced to evict live ones (the cause of the
eviction⇄recompute thrash). Crucially, unrolling in waves must not change
results.
"""

from __future__ import annotations

import contextlib
import io

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

PROGRAM = 'out = for g in range(0, 400) do g * g + g\nprint "r" out\n'


def _run(window: int, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOXLOGICA_LOOP_WINDOW", str(window))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = ExecutionEngine(no_cache=True, use_engine=True).execute_workplan(
            reduce_program(parse_program_content(PROGRAM))
        )
    printed = next((line for line in buffer.getvalue().splitlines() if line.startswith("r=")), None)
    return result, printed


@pytest.mark.unit
def test_bounded_unroll_bounds_frontier_and_preserves_results(monkeypatch: pytest.MonkeyPatch) -> None:
    windowed, windowed_out = _run(4, monkeypatch)
    unrolled, unrolled_out = _run(10_000, monkeypatch)

    assert windowed.success and unrolled.success
    # Demand-driven admission bounds the frontier regardless of window size: a
    # huge window (10_000, for 400 bodies) does NOT make the whole fan-out
    # live at once — admission only opens enough bodies to keep the workers
    # fed, so both runs keep the frontier small.
    assert windowed.cache_summary["peak_frontier"] < 60, windowed.cache_summary
    assert unrolled.cache_summary["peak_frontier"] < 150, unrolled.cache_summary
    # Same numeric results either way — unrolling in waves only reorders work.
    assert windowed_out == unrolled_out
    assert windowed_out is not None and windowed_out.startswith("r=[0.0, 2.0, 6.0")
    # Windowing this independent fan-out costs no recomputes.
    assert windowed.cache_summary["recomputes"] == 0, windowed.cache_summary
