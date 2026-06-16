from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.parser import (
    Declaration,
    EArray,
    ECall,
    ESlice,
    ProgramParseError,
    parse_program_content,
)
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
def test_parser_accepts_declarations_without_let_keyword():
    program = parse_program_content(
        """
        B+(a)=a
        x = 1 .<= 2
        y = B+(3)
        """
    )
    assert len(program.commands) == 3
    for cmd in program.commands:
        assert isinstance(cmd, Declaration)
    assert program.commands[0].identifier == "B+"
    assert program.commands[1].identifier == "x"
    assert program.commands[2].identifier == "y"


@pytest.mark.unit
def test_parser_keeps_qualified_identifiers_with_operator_support():
    program = parse_program_content(
        """
        import "simpleitk"
        let img = simpleitk.ReadImage("tests/data/chris_t1.nii.gz")
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
    assert prepared.values.get(goal_id) == 5.0


@pytest.mark.unit
def test_uppercase_identifier_uses_regular_call_syntax():
    program = parse_program_content(
        """
        let SUM(a,b) = a + b
        print "res" SUM(4,5)
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.values.get(res_goal.id) == 9.0


@pytest.mark.unit
def test_plain_scalar_comparison_operator_resolves_without_unknown_callable():
    program = parse_program_content(
        """
        c = 32
        print "res" c > 20
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.values.get(res_goal.id) is True


@pytest.mark.unit
def test_brats_segmentation_example_reduces_plain_vox1_comparison_operators():
    program = parse_program_content(
        Path("tests/brats_brain_tumour_segmentation.imgql").read_text(encoding="utf-8")
        + '\nprint "pdt_check" pdt(tt)\n'
        + '\nprint "smoothen_check" smoothen(tt, 1.0)\n'
    )
    work_plan = reduce_program(program)
    operators = {node.operator for node in work_plan.nodes.values()}
    assert "vox1.>" in operators
    assert "vox1.<=" in operators
    assert "vox1.>=" in operators


@pytest.mark.unit
def test_plain_scalar_boolean_and_inequality_operators_resolve():
    program = parse_program_content(
        """
        left = 3 != 4
        right = !false
        print "res" left && right
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.values.get(res_goal.id) is True


@pytest.mark.unit
def test_parser_supports_array_literals():
    program = parse_program_content(
        """
        xs = [1, 2, 3]
        """
    )
    decl = program.commands[0]
    assert isinstance(decl, Declaration)
    assert isinstance(decl.expression, EArray)
    assert len(decl.expression.items) == 3


@pytest.mark.unit
def test_parser_supports_slice_syntax():
    program = parse_program_content(
        """
        xs = [1, 2, 3, 4]
        mid = xs[1:3]
        """
    )
    decl = program.commands[1]
    assert isinstance(decl, Declaration)
    assert isinstance(decl.expression, ESlice)


@pytest.mark.unit
def test_array_literals_and_bracket_access_execute():
    program = parse_program_content(
        """
        rows = [[1, 2], [3, 4 + 1]]
        print "res" rows[1][1]
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.values.get(res_goal.id) == 5.0


@pytest.mark.unit
def test_slice_syntax_variants_execute():
    program = parse_program_content(
        """
        xs = [0, 1, 2, 3, 4]
        print "mid" xs[1:4]
        print "head" xs[:2]
        print "tail" xs[3:]
        print "all" xs[:]
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    goal_values = {
        goal.name: prepared.values.get(goal.id)
        for goal in prepared.plan.goals
    }
    assert goal_values["mid"] == [1.0, 2.0, 3.0]
    assert goal_values["head"] == [0.0, 1.0]
    assert goal_values["tail"] == [3.0, 4.0]
    assert goal_values["all"] == [0.0, 1.0, 2.0, 3.0, 4.0]


@pytest.mark.unit
def test_slice_syntax_works_inside_runtime_closure_bodies():
    program = parse_program_content(
        """
        tail(xs) = xs[1:]
        ys = map(tail, [[1, 2, 3], [4, 5]])
        print "res" ys[1][0]
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    assert prepared.values.get(res_goal.id) == 5.0


@pytest.mark.unit
def test_array_literals_materialize_as_sequences():
    program = parse_program_content(
        """
        xs = [1, 2 + 1, 4]
        print "res" xs
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    value = prepared.values.get(res_goal.id)
    assert [float(item) for item in value] == [1.0, 3.0, 4.0]


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
    assert prepared.values.get(res_goal.id) == 9.0


@pytest.mark.unit
def test_uppercase_identifier_is_not_treated_as_prefix_operator():
    with pytest.raises(ProgramParseError):
        parse_program_content(
            """
            let NEG(x) = 0 - x
            print "res" NEG 7
            """
        )


@pytest.mark.unit
def test_uppercase_identifier_is_not_treated_as_infix_operator():
    with pytest.raises(ProgramParseError):
        parse_program_content(
            """
            let SUM(a,b) = a + b
            print "res" 4 SUM 5
            """
        )


@pytest.mark.unit
def test_map_accepts_uppercase_function_identifier():
    program = parse_program_content(
        """
        let F(x) = x + 1
        let ys = map(F, range(0,3))
        print "res" ys
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    value = prepared.values.get(res_goal.id)
    values = value.iter_values() if hasattr(value, "iter_values") else value
    assert [float(item) for item in values] == [1.0, 2.0, 3.0]


@pytest.mark.unit
def test_filter_expression_executes():
    program = parse_program_content(
        """
        let xs = filter x in range(0, 10) do num_gt(x, 5)
        print "res" xs
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    res_goal = next(goal for goal in prepared.plan.goals if goal.name == "res")
    value = prepared.values.get(res_goal.id)
    values = value.iter_values() if hasattr(value, "iter_values") else value
    assert [float(item) for item in values] == [6.0, 7.0, 8.0, 9.0]


@pytest.mark.unit
def test_fold_expression_executes():
    program = parse_program_content(
        """
        print "sum" fold + 0 range(0, 5)
        print "prod" fold * 1 range(1, 5)
        print "maxv" fold max range(0, 8)
        """
    )
    work_plan = reduce_program(program)
    strategy = StrictExecutionStrategy(registry=work_plan.registry)
    prepared = strategy.compile(work_plan.to_symbolic_plan())
    result = strategy.run(prepared)
    assert result.success
    goal_values = {
        goal.name: prepared.values.get(goal.id)
        for goal in prepared.plan.goals
    }
    assert goal_values["sum"] == 10.0
    assert goal_values["prod"] == 24.0
    assert goal_values["maxv"] == 7.0


@pytest.mark.unit
def test_program_parse_error_has_clickable_vscode_format():
    with pytest.raises(ProgramParseError) as exc_info:
        parse_program_content(
            """
            x = 1
            y = let z = 2 in
            """,
            source_name="tests/example.imgql",
        )
    text = exc_info.value.format_block()
    assert text.startswith("tests/example.imgql:")
    assert ": error: unexpected token" in text
    assert "^" in text
