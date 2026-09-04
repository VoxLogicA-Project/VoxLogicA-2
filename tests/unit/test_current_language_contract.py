"""Current-engine contracts retained from the retired legacy test suite."""

from __future__ import annotations

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import Declaration, EArray, ESlice, parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.storage import NoCacheStorageBackend


@pytest.mark.unit
def test_current_parser_preserves_operator_and_sequence_syntax() -> None:
    program = parse_program_content(
        """
        B+(a)=a
        xs = [1, 2, 3, 4]
        mid = xs[1:3]
        """
    )

    assert isinstance(program.commands[0], Declaration)
    assert program.commands[0].identifier == "B+"
    assert isinstance(program.commands[1].expression, EArray)
    assert isinstance(program.commands[2].expression, ESlice)


@pytest.mark.unit
def test_current_engine_executes_nested_sequence_indexing(capsys: pytest.CaptureFixture[str]) -> None:
    workplan = reduce_program(
        parse_program_content(
            """
            rows = [[1, 2], [3, 4 + 1]]
            print "result" rows[1][1]
            """
        )
    )

    result = ExecutionEngine(
        storage_backend=NoCacheStorageBackend(), no_cache=True
    ).execute_workplan(workplan)

    assert result.success, result.failed_operations
    assert "result=5" in capsys.readouterr().out
