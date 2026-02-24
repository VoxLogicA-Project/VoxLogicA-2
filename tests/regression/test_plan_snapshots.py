from __future__ import annotations

from pathlib import Path
import json

import pytest

from voxlogica.execution import ExecutionEngine


def _normalize_plan(workplan):
    nodes = []
    for node in workplan.nodes.values():
        nodes.append(
            {
                "kind": node.kind,
                "operator": node.operator,
                "output_kind": node.output_kind,
                "argc": len(node.args),
                "kwarg_names": [name for name, _ in sorted(node.kwargs)],
                "attr_keys": sorted(node.attrs.keys()),
            }
        )
    nodes.sort(key=lambda item: (item["kind"], item["operator"], item["argc"]))

    goals = [
        {
            "operation": goal.operation,
            "name": goal.name,
            "target_kind": workplan.nodes[goal.id].kind,
        }
        for goal in workplan.goals
    ]

    return {"nodes": nodes, "goals": goals}


@pytest.mark.regression
def test_golden_symbolic_plan_snapshot(reduce_from_text):
    program = """
let inc(x)=x+1
print "out" for x in range(0,3) do inc(x)
"""
    workplan = reduce_from_text(program)
    normalized = _normalize_plan(workplan)

    golden_path = Path(__file__).parent / "golden" / "simple_for_loop_plan.json"
    expected = json.loads(golden_path.read_text(encoding="utf-8"))

    assert normalized == expected


@pytest.mark.regression
def test_qualified_addition_regression(reduce_from_text):
    program = 'print "sum" default.addition(2,3)'
    workplan = reduce_from_text(program)
    plan = workplan.to_symbolic_plan()
    goal_id = plan.goals[0].id

    engine = ExecutionEngine()
    prepared = engine.compile_plan(workplan, strategy="strict")
    page = engine.page(prepared, goal_id, offset=0, limit=1, strategy="strict")

    assert page.items == [5.0]
