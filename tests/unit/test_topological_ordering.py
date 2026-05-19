from __future__ import annotations

import pytest

from voxlogica.converters.common import iter_topological_nodes
from voxlogica.lazy.ir import NodeSpec, SymbolicPlan


def _constant(value: float) -> NodeSpec:
    return NodeSpec(
        kind="constant",
        operator="constant",
        attrs={"value": value},
        output_kind="scalar",
    )


def _primitive(operator: str, *args: str, **kwargs: str) -> NodeSpec:
    return NodeSpec(
        kind="primitive",
        operator=operator,
        args=tuple(args),
        kwargs=tuple(kwargs.items()),
        output_kind="scalar",
    )


@pytest.mark.unit
def test_iter_topological_nodes_places_dependencies_before_dependents():
    plan = SymbolicPlan(
        nodes={
            "sum": _primitive("default.addition", "left", "right"),
            "left": _primitive("default.multiplication", "a", "b"),
            "right": _constant(4.0),
            "a": _constant(2.0),
            "b": _constant(3.0),
        }
    )

    ordered_ids = [node_id for node_id, _node in iter_topological_nodes(plan)]
    positions = {node_id: index for index, node_id in enumerate(ordered_ids)}

    for node_id, node in plan.nodes.items():
        dependency_ids = tuple(node.args) + tuple(value for _key, value in node.kwargs)
        for dependency_id in dependency_ids:
            assert positions[dependency_id] < positions[node_id]


@pytest.mark.unit
def test_iter_topological_nodes_rejects_cycles():
    plan = SymbolicPlan(
        nodes={
            "a": _primitive("default.identity", "b"),
            "b": _primitive("default.identity", "a"),
        }
    )

    with pytest.raises(ValueError, match="Cycle detected"):
        list(iter_topological_nodes(plan))


@pytest.mark.unit
def test_iter_topological_nodes_rejects_missing_dependencies():
    plan = SymbolicPlan(nodes={"a": _primitive("default.identity", "missing")})

    with pytest.raises(KeyError, match="missing"):
        list(iter_topological_nodes(plan))


@pytest.mark.unit
def test_iter_topological_nodes_includes_function_capture_dependencies():
    closure = NodeSpec(
        kind="closure",
        operator="closure",
        attrs={
            "function_captures": {
                "scale": {
                    "parameters": ["x"],
                    "body": "multiplication(x,factor)",
                    "captures": {"factor": "factor"},
                    "functions": {},
                }
            }
        },
        output_kind="closure",
    )
    plan = SymbolicPlan(
        nodes={
            "mapped": _primitive("default.map", "values", "closure"),
            "closure": closure,
            "values": _primitive("default.range", "start", "stop"),
            "factor": _constant(2.0),
            "start": _constant(0.0),
            "stop": _constant(5.0),
        }
    )

    ordered_ids = [node_id for node_id, _node in iter_topological_nodes(plan)]

    assert ordered_ids.index("factor") < ordered_ids.index("closure")
    assert ordered_ids.index("closure") < ordered_ids.index("mapped")
