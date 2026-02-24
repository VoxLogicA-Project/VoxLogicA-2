from __future__ import annotations

from pathlib import Path

import pytest


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
