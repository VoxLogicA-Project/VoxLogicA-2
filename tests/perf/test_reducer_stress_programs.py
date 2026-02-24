from __future__ import annotations

import pytest


def _fibonacci_chain_program(depth: int) -> str:
    lines = ["let f0 = 1", "let f1 = 1"]
    for i in range(2, depth + 1):
        lines.append(f"let f{i} = f{i-1} + f{i-2}")
    lines.append(f'print "fib{depth}" f{depth}')
    return "\n".join(lines)


def _function_explosion_program(depth: int) -> str:
    lines = ["let f0(x) = 1", "let f1(x) = 1"]
    for i in range(2, depth + 1):
        prev1 = f"f{i-1}(x+1)"
        prev2 = f"f{i-2}(x-1)"
        prev3 = f"f{i-1}(x*2)"
        prev4 = f"f{i-2}(x/2)"
        prev5 = f"f{i-1}(x)"
        prev6 = f"f{i-2}(x)"
        lines.append(
            f"let f{i}(x) = {prev1} + {prev2} + {prev3} + {prev4} + {prev5} + {prev6}"
        )
    lines.append(f'print "function_explosion_f{depth}" f{depth}(1)')
    return "\n".join(lines)


@pytest.mark.perf
def test_fibonacci_chain_reduction_sanity(reduce_from_text):
    workplan = reduce_from_text(_fibonacci_chain_program(depth=80))
    plan = workplan.to_symbolic_plan()

    # Sanity target: rich enough graph, without enforcing unstable exact counts.
    assert len(plan.nodes) >= 80
    assert len(plan.goals) == 1


@pytest.mark.perf
def test_function_explosion_reduction_sanity(reduce_from_text):
    workplan = reduce_from_text(_function_explosion_program(depth=8))
    plan = workplan.to_symbolic_plan()

    assert len(plan.nodes) > 10
    assert len(plan.goals) == 1
