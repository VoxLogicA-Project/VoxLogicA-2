"""Short-cut fusion of positional sequence access (reducer).

A slice/index over a position-independent producer (``for``/``map``, or a
literal sequence) is pushed into the producer at reduction time, so only the
demanded elements are ever computed. These tests pin both the *semantics*
(output unchanged) and the *optimization* (the plan shrinks / the loop iterates
only the demanded range).
"""

from __future__ import annotations

import pytest

from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.execution import ExecutionEngine
from voxlogica.storage import NoCacheStorageBackend


def _run(program: str, *, use_engine: bool = False):
    """Reduce and execute a program, returning the ExecutionResult."""
    workplan = reduce_program(parse_program_content(program))
    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(),
                             no_cache=True, use_engine=use_engine)
    result = engine.execute_workplan(workplan)
    assert result.success is True
    return result


def _print_map(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    out = capsys.readouterr().out
    values: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line and not line.lstrip().startswith(("{", '"')):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


@pytest.mark.unit
def test_slice_and_index_over_for_loop_are_correct(capsys: pytest.CaptureFixture[str]) -> None:
    _run(
        """
        xs = range(0, 10)
        sq = for x in xs do x * x
        print "slice"  subsequence(sq, 2, 7)
        print "openL"  sq[:3]
        print "openR"  sq[7:]
        print "idx"    index(sq, 4)
        print "nested" subsequence(subsequence(sq, 1, 9), 0, 3)
        print "full"   sq
        """
    )
    values = _print_map(capsys)
    assert values["slice"] == "[4.0, 9.0, 16.0, 25.0, 36.0]"
    assert values["openL"] == "[0.0, 1.0, 4.0]"
    assert values["openR"] == "[49.0, 64.0, 81.0]"
    assert values["idx"] == "16.0"
    assert values["nested"] == "[1.0, 4.0, 9.0]"
    assert values["full"] == "[0.0, 1.0, 4.0, 9.0, 16.0, 25.0, 36.0, 49.0, 64.0, 81.0]"


@pytest.mark.unit
@pytest.mark.parametrize("use_engine", [False, True])
def test_slice_over_loop_computes_only_demanded_elements(use_engine: bool) -> None:
    """A slice over a for-loop must not force the whole sequence: far fewer
    operations are actually computed than when the full sequence is demanded."""
    demanded = _run(
        """
        xs = range(0, 100)
        sq = for x in xs do x * x
        print "r" subsequence(sq, 0, 3)
        """,
        use_engine=use_engine,
    )
    whole = _run(
        """
        xs = range(0, 100)
        sq = for x in xs do x * x
        print "r" sq
        """,
        use_engine=use_engine,
    )
    assert len(demanded.completed_operations) < len(whole.completed_operations)


@pytest.mark.unit
def test_fusion_preserves_results_vs_full_computation(capsys: pytest.CaptureFixture[str]) -> None:
    """The fused slice yields exactly the corresponding prefix of the full result."""
    _run(
        """
        xs = range(0, 6)
        sq = for x in xs do x + 100
        print "full"  sq
        print "head"  subsequence(sq, 0, 2)
        print "tail"  index(sq, 5)
        """
    )
    values = _print_map(capsys)
    assert values["full"] == "[100.0, 101.0, 102.0, 103.0, 104.0, 105.0]"
    assert values["head"] == "[100.0, 101.0]"
    assert values["tail"] == "105.0"
