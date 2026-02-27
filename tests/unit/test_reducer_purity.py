from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.policy import StaticAnalysisError


@pytest.mark.unit
def test_load_reduction_is_symbolic_only(reduce_from_text, tmp_path: Path):
    missing_path = tmp_path / "missing-dataset.txt"

    workplan = reduce_from_text(
        f'print "out" load("{missing_path}")'
    )

    primitive_operators = {node.operator for node in workplan.nodes.values() if node.kind == "primitive"}
    assert any(op.endswith("load") for op in primitive_operators)


@pytest.mark.unit
def test_map_reduction_is_symbolic_only(reduce_from_text):
    program = """
let double(x)=x+x
print "mapped" map(double, range(0,4))
"""
    workplan = reduce_from_text(program)

    primitive_operators = [node.operator for node in workplan.nodes.values() if node.kind == "primitive"]
    assert any(op.endswith("map") for op in primitive_operators)
    assert any(op.endswith("range") for op in primitive_operators)

    closure_nodes = [node for node in workplan.nodes.values() if node.kind == "closure"]
    assert closure_nodes, "expected symbolic closure node for map function argument"


@pytest.mark.unit
def test_unknown_primitive_fails_static_resolution(reduce_from_text):
    with pytest.raises(StaticAnalysisError) as exc_info:
        reduce_from_text('print "x" UnknownCallable(1)')

    diagnostics = list(exc_info.value.diagnostics)
    assert diagnostics
    assert diagnostics[0].code == "E_UNKNOWN_CALLABLE"
    assert diagnostics[0].symbol == "UnknownCallable"


@pytest.mark.unit
def test_unknown_symbol_without_arguments_fails_static_resolution(reduce_from_text):
    with pytest.raises(StaticAnalysisError) as exc_info:
        reduce_from_text('print "x" missing_symbol')

    diagnostics = list(exc_info.value.diagnostics)
    assert diagnostics
    assert diagnostics[0].code == "E_UNKNOWN_CALLABLE"
    assert diagnostics[0].symbol == "missing_symbol"
