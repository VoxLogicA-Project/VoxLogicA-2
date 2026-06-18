from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.reducer import StaticAnalysisError


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
def test_map_reduction_accepts_imported_uppercase_primitive_callable(reduce_from_text):
    program = """
import "simpleitk"
inputs = range(0,2)
mapped = map(ReadImage, inputs)
"""
    workplan = reduce_from_text(program)

    primitive_operators = [node.operator for node in workplan.nodes.values() if node.kind == "primitive"]
    assert any(op.endswith("map") for op in primitive_operators)
    closure_nodes = [node for node in workplan.nodes.values() if node.kind == "closure"]
    assert closure_nodes
    assert str(closure_nodes[0].attrs.get("body", "")).startswith("ReadImage(")


@pytest.mark.unit
def test_parse_program_content_records_source_positions():
    from voxlogica.parser import ECall, Print, parse_program_content

    program = parse_program_content('print "x" foo(1)', source_name="demo.imgql")
    command = program.commands[0]
    assert isinstance(command, Print)
    expr = command.expression
    assert isinstance(expr, ECall)
    assert expr.position == "demo.imgql:1:11"


@pytest.mark.unit
def test_unknown_primitive_fails_static_resolution(reduce_from_text):
    with pytest.raises(StaticAnalysisError) as exc_info:
        reduce_from_text('print "x" UnknownCallable(1)')

    diagnostics = list(exc_info.value.diagnostics)
    assert diagnostics
    assert diagnostics[0].code == "E_UNBOUND_IDENTIFIER"
    assert diagnostics[0].symbol == "UnknownCallable"


@pytest.mark.unit
def test_unknown_symbol_without_arguments_fails_static_resolution(reduce_from_text):
    with pytest.raises(StaticAnalysisError) as exc_info:
        reduce_from_text('print "x" missing_symbol')

    diagnostics = list(exc_info.value.diagnostics)
    assert diagnostics
    assert diagnostics[0].code == "E_UNBOUND_IDENTIFIER"
    assert diagnostics[0].symbol == "missing_symbol"


@pytest.mark.unit
def test_unbound_closure_capture_reports_variable_not_primitive(reduce_from_text):
    program = """
import "nnunet"

save_segmentation(case) =
  let image = index(case, 1) in
  nnunet.predict(predictor, image)

predictor = 1
print "out" save_segmentation([1, 2])
"""
    with pytest.raises(StaticAnalysisError) as exc_info:
        reduce_from_text(program)

    diagnostics = list(exc_info.value.diagnostics)
    assert diagnostics
    assert diagnostics[0].code == "E_UNBOUND_IDENTIFIER"
    assert diagnostics[0].symbol == "predictor"
    assert "Unbound variable 'predictor'" in diagnostics[0].message
    assert "Call chain:" in diagnostics[0].message
    assert ":1:" in diagnostics[0].message or ":" in diagnostics[0].location
