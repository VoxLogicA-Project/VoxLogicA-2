"""Two goals that hash-cons to the same node are two lines of output, not one.

`train_count = 50` and `eval_start = 50` are one constant node. The drain loop
that materializes goals while the engine is alive remembered what it had
emitted by NODE id, so the second goal was never printed: 32 lines for 33
prints, exit 0, on the nnU-Net sweep.
"""

from __future__ import annotations

import contextlib
import io

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

PROGRAM = """
a = 50
b = 50
print "first" a
print "second" b
"""


@pytest.mark.unit
def test_two_goals_sharing_a_node_are_both_emitted() -> None:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        ExecutionEngine(use_engine=True).execute_workplan(
            reduce_program(parse_program_content(PROGRAM)))
    lines = sorted(l for l in buffer.getvalue().splitlines() if l.startswith(("first=", "second=")))
    assert lines == ["first=50.0", "second=50.0"]
