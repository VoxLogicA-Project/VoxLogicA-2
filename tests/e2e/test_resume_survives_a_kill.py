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

Everything here runs through the real CLI in a subprocess, including the
reference run. That is not ceremony: SIGKILL cannot be simulated in-process, and
once the killed run is a subprocess, measuring the other two the same way is the
only way the three numbers are comparable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from voxlogica.engine.core import FRONTIER_CHECKPOINT_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_IMPL = REPO_ROOT / "implementation" / "python"

# ~208,000 completions. Sized from measurement, not taste: at 3,500 elements per
# loop the whole program runs in 23.5 s even throttled to two threads, which is
# inside the kill deadline -- and a run that exits normally proves nothing about
# SIGKILL. 9,000 puts the throttled run near a minute, so the kill lands with
# most of the program still undone. Same shape as the sibling test's program
# (ten distinct loops, real images, ~2.31 completions per element).
_LOOP = (
    "let l{n} = for i in range(0, 9000) do "
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

#: When to kill: one checkpoint tick plus margin, so a tick has certainly
#: happened and written something while most of the program is still undone.
KILL_AFTER_SECONDS = FRONTIER_CHECKPOINT_SECONDS + 12.0

#: Threads for the run that gets killed. Throttling is the honest way to hold it
#: open past a checkpoint tick; the alternative -- a program big enough to
#: outlast one at full width -- would make the reference run, which has to do
#: the whole thing, by far the slowest item in the suite.
KILLED_RUN_THREADS = "2"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PYTHON_IMPL) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("VOXLOGICA_DEV_STOP_AFTER", None)   # this test never stops cleanly
    return env


def _command(db_path: Path, program_file: Path, threads: str | None) -> list[str]:
    command = [sys.executable, "-u", "-m", "voxlogica.main", "run", "--no-serve",
               "--store-db", str(db_path)]
    if threads:
        command += ["--threads", threads]
    return command + [str(program_file)]


def _metrics(stdout: str) -> dict:
    """The `cache_summary` out of the CLI's JSON report.

    The report is preceded by the program's own `print` output, so the JSON is
    found by its opening brace at column zero rather than by parsing the lot.
    """
    lines = stdout.splitlines()
    start = next((i for i, line in enumerate(lines) if line == "{"), None)
    assert start is not None, f"no JSON report in the run's output:\n{stdout[-2000:]}"
    end = next(i for i in range(len(lines) - 1, start, -1) if lines[i] == "}")
    report = json.loads("\n".join(lines[start:end + 1]))
    summary = report.get("cache_summary") or report.get("result", {}).get("cache_summary")
    assert summary, f"no cache_summary in the report: {sorted(report)}"
    return summary


def _run(db_path: Path, program_file: Path, threads: str | None = None) -> dict:
    done = subprocess.run(_command(db_path, program_file, threads), env=_env(),
                          cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=1800)
    assert done.returncode == 0, f"the run failed:\n{done.stderr[-2000:]}"
    return _metrics(done.stdout)


def _run_and_kill(db_path: Path, program_file: Path, log: Path) -> int:
    """Start a run, SIGKILL it after a checkpoint has fired, and report how many
    completions it had got through when it died.

    The count comes from the engine's own memory log, whose path it announces on
    startup. It is sampled, so it lags the moment of death slightly -- and that
    bias makes `remaining` larger and the assertion easier, never the reverse.
    """
    with log.open("wb") as stderr:
        proc = subprocess.Popen(
            _command(db_path, program_file, KILLED_RUN_THREADS), env=_env(),
            cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=stderr)
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
    rows = [row for row in path.read_text(errors="replace").splitlines() if row.strip()]
    assert len(rows) > 1, "the memory log has no samples: the run died too early"
    header, last = rows[0].split("\t"), rows[-1].split("\t")
    return int(float(last[header.index("completed")]))


@pytest.mark.e2e
@pytest.mark.slow
def test_a_killed_run_resumes_from_its_last_checkpoint(tmp_path: Path) -> None:
    pytest.importorskip("SimpleITK")

    program_file = tmp_path / "killed.imgql"
    program_file.write_text(PROGRAM)

    total = _run(tmp_path / "reference.db", program_file)["completed"]

    killed_db = tmp_path / "killed.db"
    killed = _run_and_kill(killed_db, program_file, tmp_path / "killed.stderr")
    assert killed_db.exists(), "the killed run left no store at all"
    assert killed > 0, "the killed run completed nothing, so there is nothing to reuse"

    resumed = _run(killed_db, program_file)

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
