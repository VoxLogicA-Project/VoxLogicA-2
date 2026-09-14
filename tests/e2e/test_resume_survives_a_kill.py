"""A SIGKILL must cost the run its last few seconds, not its whole history.

The sibling test (`test_resume_starts_from_the_frontier`) proves the CLEAN stop:
the dev guard stops between completions and the strategy then checkpoints the
frontier, flushes the lineage buffer and drains the writers on its way out. That
is the easy half. A `kill -9` gives the process no way out at all, so nothing
can be written *in response* to it -- whatever a resume is going to use has to
have been on disk already when the signal landed.

Two engine properties make that true, and this test exists to keep them true:

1. **The frontier is checkpointed periodically while running**
   (`FRONTIER_CHECKPOINT_SECONDS`), not only at a clean stop. Without it a
   killed run leaves only what best-effort persistence happened to write, which
   is decided by writer-queue timing -- measured at 32% on the BraTS ladder, and
   a resume then recomputes two thirds of what it already did.

2. **Committed writes survive the process.** The store is WAL with
   `synchronous=NORMAL` (`storage.py`), so a transaction that returned is in the
   OS page cache and outlives `SIGKILL`; SQLite replays the WAL on the next
   open. `synchronous=NORMAL` trades only *power loss* for speed, which is a
   different failure and not this one.

The test kills a real subprocess, because that is the only way to get a real kill:
a clean shutdown path that has been asked politely not to run is not the same
thing as never getting the chance.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from voxlogica.engine.core import FRONTIER_CHECKPOINT_SECONDS
from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_IMPL = REPO_ROOT / "implementation" / "python"

# ~80,000 completions, about a minute of work. Sized so the killed run outlives
# at least one periodic checkpoint with room to spare -- the point of the test
# is what the checkpoint left behind, so a program that finishes before the
# first tick would prove nothing. Same shape as the sibling test's program (ten
# distinct loops, real images, calibrated ~2.31 completions per element).
_LOOP = (
    "let l{n} = for i in range(0, 3500) do "
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

#: When to kill. One checkpoint tick plus enough margin that the tick has
#: certainly happened and written something, while still leaving most of the
#: program undone.
KILL_AFTER_SECONDS = FRONTIER_CHECKPOINT_SECONDS + 12.0


def _run_in_process(db_path: Path) -> dict:
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


def _run_and_kill(db_path: Path, program_file: Path, log: Path) -> int:
    """Start a real run, SIGKILL it after a checkpoint has fired, and report
    how many completions it had got through when it died.

    `--threads 8` rather than the default: on a 24-core host this program
    finishes inside the kill deadline at full width, and a run that exits
    normally proves nothing about SIGKILL. Throttling the killed run is the
    honest way to hold it open past a checkpoint tick -- the alternative, a
    program big enough to outlast one at full width, makes the reference run
    (which has to do the whole thing) the slowest part of the suite.

    The count comes from the engine's own memory log, whose path it prints on
    startup. It is sampled, so it lags the true moment of death slightly -- and
    that bias makes `remaining` larger and the assertion easier, never the
    reverse.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PYTHON_IMPL) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("VOXLOGICA_DEV_STOP_AFTER", None)
    with log.open("wb") as stderr:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "voxlogica.main", "run", "--no-serve",
             "--threads", "8", "--store-db", str(db_path), str(program_file)],
            env=env, stdout=subprocess.DEVNULL, stderr=stderr,
            cwd=str(REPO_ROOT),
        )
        try:
            deadline = time.monotonic() + KILL_AFTER_SECONDS
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail(
                        "the run finished before it could be killed: raise the "
                        "element count in PROGRAM so it outlives a checkpoint "
                        "tick")
                time.sleep(0.5)
            proc.kill()                  # SIGKILL: no handler, no shutdown path
        finally:
            proc.wait(timeout=60)
    assert proc.returncode != 0, "the process was not killed"
    return _completions_from_memlog(log)


def _completions_from_memlog(log: Path) -> int:
    """Last sampled completion count, from the memory log the run announced."""
    text = log.read_text(errors="replace")
    marker = "memory log: "
    assert marker in text, f"the run never announced a memory log:\n{text[-2000:]}"
    path = Path(text.split(marker, 1)[1].splitlines()[0].strip())
    rows = [r for r in path.read_text(errors="replace").splitlines() if r.strip()]
    assert len(rows) > 1, "the memory log has no samples: the run died too early"
    header, last = rows[0].split("\t"), rows[-1].split("\t")
    return int(float(last[header.index("completed")]))


@pytest.mark.e2e
@pytest.mark.slow
def test_a_killed_run_resumes_from_its_last_checkpoint(tmp_path: Path) -> None:
    pytest.importorskip("SimpleITK")

    program_file = tmp_path / "killed.imgql"
    program_file.write_text(PROGRAM)

    reference = _run_in_process(tmp_path / "reference.db")
    total = reference["completed"]

    killed_db = tmp_path / "killed.db"
    killed = _run_and_kill(killed_db, program_file, tmp_path / "killed.stderr")
    assert killed_db.exists(), "the killed run left no store at all"
    assert killed > 0, "the killed run completed nothing, so there is nothing to reuse"

    resumed = _run_in_process(killed_db)

    assert resumed["pruned_available"] > 0, (
        "after a kill the store answered for NO node: either the periodic "
        "frontier checkpoint never ran, or nothing it wrote survived")
    # The same statement as the clean-stop test, against a process that was
    # given no chance to tidy up: the resume must do what is LEFT. Slack is
    # wider here (1.5x) because the kill lands wherever it lands -- mid-write,
    # mid-expansion, mid-cone -- and the sampled count of what the dead run
    # achieved is a lower bound. A resume that learnt nothing does `total`, and
    # `total` is far outside this budget.
    remaining = total - killed
    assert resumed["completed"] <= remaining * 1.5, (
        f"the killed run's work was lost: the resume did {resumed['completed']} "
        f"completions, but only {remaining} of {total} were left after the kill "
        f"(which had reached {killed}); pruned={resumed['pruned_available']}")
