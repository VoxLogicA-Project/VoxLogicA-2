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


def _run_and_kill(db_path: Path, program_file: Path) -> None:
    """Start a real run and SIGKILL it once a checkpoint has certainly fired."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PYTHON_IMPL) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("VOXLOGICA_DEV_STOP_AFTER", None)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "voxlogica.main", "run", "--no-serve",
         "--store-db", str(db_path), str(program_file)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
    )
    try:
        deadline = time.monotonic() + KILL_AFTER_SECONDS
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(
                    "the run finished before it could be killed: raise the "
                    "element count in PROGRAM so it outlives a checkpoint tick")
            time.sleep(0.5)
        proc.kill()                      # SIGKILL: no handler, no shutdown path
    finally:
        proc.wait(timeout=60)
    assert proc.returncode != 0, "the process was not killed"


@pytest.mark.e2e
@pytest.mark.slow
def test_a_killed_run_resumes_from_its_last_checkpoint(tmp_path: Path) -> None:
    pytest.importorskip("SimpleITK")

    program_file = tmp_path / "killed.imgql"
    program_file.write_text(PROGRAM)

    reference = _run_in_process(tmp_path / "reference.db")
    total = reference["completed"]

    killed_db = tmp_path / "killed.db"
    _run_and_kill(killed_db, program_file)
    assert killed_db.exists(), "the killed run left no store at all"

    resumed = _run_in_process(killed_db)

    assert resumed["pruned_available"] > 0, (
        "after a kill the store answered for NO node: either the periodic "
        "frontier checkpoint never ran, or nothing it wrote survived")
    # A resume that learnt nothing from the killed run does `total`. The killed
    # run had a minute of a ~1-minute program, so most of the work should be
    # gone from the resume's bill; 85% is a floor loose enough to survive
    # machine load deciding how far the killed run got, and still impossible to
    # pass by replanning from the goals.
    assert resumed["completed"] < total * 0.85, (
        f"the killed run's work was lost: the resume did {resumed['completed']} "
        f"completions against a whole-program {total} "
        f"(pruned={resumed['pruned_available']})")
