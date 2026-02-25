from __future__ import annotations

import pytest

from voxlogica.parser import Declaration, ECall, parse_program_content
from voxlogica.reducer import reduce_program
from voxlogica.execution_strategy.strict import StrictExecutionStrategy


@pytest.mark.unit
def test_parser_supports_dotted_operators_and_symbol_identifiers():
    program = parse_program_content(
        """
        let B+(a)=a
        let x = 1 .<=. 2
        let y = 1 .<= 2
        let z = B+(3)
        """
    )
    assert len(program.commands) == 4
    decl = program.commands[0]
    assert isinstance(decl, Declaration)
    assert decl.identifier == "B+"


@pytest.mark.unit
def test_parser_keeps_qualified_identifiers_with_operator_support():
    program = parse_program_content(
        """
        import "simpleitk"
        let img = simpleitk.ReadImage("tests/chris_t1.nii.gz")
        let t = 1 .+ 2
        """
    )
    assert len(program.commands) == 3
    image_decl = program.commands[1]
    assert isinstance(image_decl, Declaration)
    assert isinstance(image_decl.expression, ECall)
    assert image_decl.expression.identifier == "simpleitk.ReadImage"


@pytest.mark.unit
def test_namespace_imgql_exports_are_applied_on_namespace_import():
    program = parse_program_content(
        """
        import "vox1"
        print "res" 2 .+. 3
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    goal_id = prepared.plan.goals[0].id
    assert prepared.materialization_store.get(goal_id) == 5.0


@pytest.mark.unit
def test_uppercase_identifier_can_be_used_infix():
    program = parse_program_content(
        """
        let SUM(a,b) = a + b
        print "res" 4 SUM 5
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.materialization_store.get(res_goal.id) == 9.0


@pytest.mark.unit
def test_symbol_identifier_can_be_used_infix():
    program = parse_program_content(
        """
        let +?(a,b) = a + b
        print "res" 4 +? 5
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.materialization_store.get(res_goal.id) == 9.0


@pytest.mark.unit
def test_symbolic_operator_can_be_used_prefix_for_unary_call():
    program = parse_program_content(
        """
        let NEG(x) = 0 - x
        print "res" NEG 7
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.materialization_store.get(res_goal.id) == -7.0
