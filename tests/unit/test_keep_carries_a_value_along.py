"""`keep(path, expr)` puts a value in a file you name, and reads it back.

Three properties, and the third is the one that makes it useful today:

1. the first run computes and writes the file;
2. a later run loads it and never schedules the expression;
3. pointing `keep` at something the STORE already holds materialises it into
   the file without recomputing -- which is how a model that took twenty-one
   hours becomes a file.

And one safety property with no upside and a large downside if it is missing:
a file written from a DIFFERENT expression must be refused, not loaded. Without
that, editing a program silently resurrects yesterday's artefact.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.primitives.default import _keepfile
from voxlogica.reducer import reduce_program
from voxlogica.storage import SQLiteResultsDatabase


def _program(path: Path, value: str = "3.0") -> str:
    return (f'let x = keep("{path}", +({value}, 4.0))\n'
            'print "x" x\n')


def _run(program: str, db: Path) -> dict:
    backend = SQLiteResultsDatabase(db_path=str(db))
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            result = ExecutionEngine(
                storage_backend=backend, use_engine=True
            ).execute_workplan(reduce_program(parse_program_content(program)))
    finally:
        backend.close()
    return {"summary": dict(result.cache_summary or {}),
            "out": buffer.getvalue(), "success": result.success}


@pytest.mark.unit
def test_the_first_run_writes_the_file_and_a_later_run_reads_it(tmp_path: Path) -> None:
    kept = tmp_path / "kept" / "seven.vlx"
    program = _program(kept)

    first = _run(program, tmp_path / "a.db")
    assert first["success"], "the first run failed"
    assert kept.is_file(), "keep wrote no file"
    assert "x=7" in first["out"].replace(".0", "")

    # A FRESH store: nothing but the file can answer, so a value coming back
    # proves the file is what answered.
    second = _run(program, tmp_path / "b.db")
    assert second["success"], "the run that should have loaded the file failed"
    assert "x=7" in second["out"].replace(".0", "")


@pytest.mark.unit
def test_a_file_from_another_expression_is_refused(tmp_path: Path) -> None:
    """The safety property: same path, different expression."""
    kept = tmp_path / "kept.vlx"
    _run(_program(kept, "3.0"), tmp_path / "a.db")        # 3 + 4 = 7
    assert kept.is_file()

    other = _run(_program(kept, "10.0"), tmp_path / "b.db")   # 10 + 4 = 14
    assert other["success"]
    assert "x=14" in other["out"].replace(".0", ""), (
        "the kept file was loaded for an expression that did not write it")


@pytest.mark.unit
def test_a_kept_file_round_trips_its_id(tmp_path: Path) -> None:
    """The format's own contract, tested without the engine."""
    path = tmp_path / "v.vlx"
    _keepfile.write(path, "a" * 64, {"model": "handle", "folds": [0]})
    found, value = _keepfile.read(path, expect="a" * 64)
    assert found and value == {"model": "handle", "folds": [0]}
    assert _keepfile.read(path, expect="b" * 64) == (False, None)
    assert _keepfile.read(tmp_path / "absent.vlx", expect="a" * 64) == (False, None)
