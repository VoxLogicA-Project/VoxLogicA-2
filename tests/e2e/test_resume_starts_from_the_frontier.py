"""A run stopped halfway must resume from the frontier, not from the goals.

THIS IS THE TEST THE WHOLE `perf-saturation` BRANCH HAS BEEN ARGUING WITHOUT.
Every resume claim so far came from a progress bar or a ratio whose denominator
was disputed (§37c/§37f/§37g of HANDOVER-2026-09-11-engine-stability.md). The
claim is simple and it deserves a simple, falsifiable statement:

    run half a computation, stop, start it again -- and the second run must do
    the OTHER half, not the whole thing.

Design, and why each part is the way it is:

* **Three runs, not two.** `total` is measured, never assumed: a full cold run
  on its own store says how much work the program is. Then a run stopped at
  half, then a resume off that store. Without the reference run the assertion
  would have to hard-code a node count, which drifts the moment the program or
  the reducer changes, and a test that drifts gets its threshold relaxed until
  it asserts nothing.

* **The stop is the DEV guard, not a kill.** `VOXLOGICA_DEV_STOP_AFTER` stops on
  the event loop between completions and then flushes and drains exactly as a
  finished run does (see `EngineExecutionStrategy`, which calls
  `checkpoint_frontier()` first). A `SIGKILL` mid-write would leave a store
  missing its last transaction -- a different experiment, and not this one.

* **Images, not scalars.** `blank` makes real SimpleITK volumes, so a node is
  worth persisting. A scalar workload would measure
  `persist_min_compute_ms`'s (correct) decision to skip values cheaper to
  recompute than to store, and prove nothing about resume.

* **The assertion is on WORK, not on a ratio.** `completed` is what the engine
  actually computed. If the resume restarts from the frontier, the two runs'
  completions sum to roughly one full run; if it restarts from the goals, the
  resume alone is a full run. Those two outcomes are far apart, so the test
  needs no delicate threshold.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase

# TEN loops, not one, and that is load-bearing. A program with a single `for`
# cannot exercise the expansion memo at all: the stopped run either finishes
# that loop's expansion or it does not, and at half the work it does not, so
# there is no memo to write and nothing for the resume to hit. Measured that
# way first -- `expanded_loops=0` and zero rows in `expansion` after the stop.
# With ten independent loops the stop lands with some fully expanded and some
# not, which is both the realistic case and the one the memo exists for.
#
# Each loop is made distinct by its own constant, or they would hash-cons into
# one node and this would be the single-loop program again.
#
# 870 elements x 10 loops was CALIBRATED, not guessed: 500 x 10 measured 11,553
# completions, i.e. ~2.31 per element rather than the ~4 the source suggests,
# because fusion elides cone interiors and they never complete. Scaling by
# 20,000/11,553 gives 870, and the reference run asserts the total is really
# above 1.5x the stop -- so if fusion or the reducer changes this shape, the
# test says "raise the range" instead of quietly measuring something else.
# 32x32 keeps a kernel well under a millisecond while still producing a real
# image, so the whole program is seconds, not minutes.
_LOOP = (
    "let l{n} = for i in range(0, 870) do "
    "array_stats(Add(Add(blank(32, 32, i), base), blank(32, 32, {n}.0)))"
)
PROGRAM = (
    'import "simpleitk"\n'
    'import "geom"\n'
    'import "arrays"\n'
    "let base = blank(32, 32, 1.0)\n"
    + "\n".join(_LOOP.format(n=n) for n in range(10)) + "\n"
    + "\n".join(f'print "l{n}" l{n}' for n in range(10)) + "\n"
)

#: Completions after which the half-run stops. Half of the measured total is
#: the interesting case: a resume that prunes nothing does ~2x this, a resume
#: that starts from the frontier does ~1x.
STOP_AFTER = 10_000


def _run(db_path: Path, monkeypatch: pytest.MonkeyPatch,
         dev_stop_after: int = 0) -> dict:
    """Run PROGRAM against `db_path`, returning the engine's own metrics."""
    if dev_stop_after:
        monkeypatch.setenv("VOXLOGICA_DEV_STOP_AFTER", str(dev_stop_after))
    else:
        monkeypatch.delenv("VOXLOGICA_DEV_STOP_AFTER", raising=False)
    backend = SQLiteResultsDatabase(db_path=str(db_path))
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            result = ExecutionEngine(
                storage_backend=backend, use_engine=True
            ).execute_workplan(reduce_program(parse_program_content(PROGRAM)))
    finally:
        backend.close()
    return dict(result.cache_summary or {})


@pytest.mark.e2e
def test_a_resume_does_the_other_half(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("SimpleITK")

    reference = _run(tmp_path / "reference.db", monkeypatch)
    total = reference["completed"]
    assert total > STOP_AFTER * 1.5, (
        f"the program is too small to halve: {total} completions against a stop "
        f"at {STOP_AFTER}. Raise the range in PROGRAM.")

    half = _run(tmp_path / "resume.db", monkeypatch, dev_stop_after=STOP_AFTER)
    assert half["dev_stopped_at"] >= STOP_AFTER, "the dev guard did not fire"
    assert half["completed"] < total, "the stopped run finished the whole program"

    resumed = _run(tmp_path / "resume.db", monkeypatch)

    remaining = total - half["completed"]
    # SLACK, and why this much. A resume cannot land exactly on `remaining`:
    # fused-cone interiors are elided rather than completed, so the two runs
    # partition the work slightly differently, and the frontier itself is
    # recomputed where a value was too cheap to persist. Half of what is left
    # is far more slack than any of that needs, and still nowhere near the
    # "restarted from the goals" outcome, which is `total`.
    budget = remaining * 1.5
    assert resumed["completed"] <= budget, (
        f"the resume recomputed instead of restarting from the frontier: "
        f"{resumed['completed']} completions, but only {remaining} were left "
        f"of {total} (the stopped run did {half['completed']}). "
        f"pruned={resumed['pruned_available']} "
        f"memo_hits={resumed['memo_hits']} memo_misses={resumed['memo_misses']}")
    assert resumed["pruned_available"] > 0, (
        "nothing was pruned: the store answered for no node at all")


@pytest.mark.e2e
def test_a_finished_expansion_is_memoised_and_reused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A loop that finished expanding must never be expanded again.

    Expansion is what NAMES the per-element nodes, so this is upstream of every
    reuse question: an unnamed body root cannot be looked up, pruned or reused
    however much the store holds.

    The contract is deliberately stated over a FINISHED run rather than a
    stopped one. A memo is written when a loop's expansion completes
    (`_memoise_expansion`, called from `_on_spliced`), so a run stopped
    mid-expansion writes none -- measured, and the explanation for §37g's "1 hit
    against 1,989 misses" on the BraTS ladder, where the loops are large enough
    that a stop at 100,000 completions leaves every one of them partly expanded.
    That costs a resume its DAG rebuild, which §37f/§37g measured at about 1% of
    wall clock, and costs it no recomputation at all -- which is why the test
    above still passes. Asserting it here would be pinning a known gap, not
    testing the contract.
    """
    pytest.importorskip("SimpleITK")

    db = tmp_path / "memo.db"
    first = _run(db, monkeypatch)
    assert first["expanded_loops"] > 0, "no loop expanded, so this proves nothing"

    second = _run(db, monkeypatch)
    assert second["memo_hits"] == first["expanded_loops"], (
        f"expansions were redone: {second['memo_hits']} memos hit against "
        f"{first['expanded_loops']} loops expanded the first time "
        f"(misses={second['memo_misses']})")
    assert second["expanded_loops"] == 0, (
        f"{second['expanded_loops']} loops were expanded again despite a memo")
    assert second["completed"] < first["completed"] // 100, (
        f"a rerun of a finished program recomputed {second['completed']} of "
        f"{first['completed']} nodes")
