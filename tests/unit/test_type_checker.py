"""Type checking as abstract execution over a reduced plan.

These tests pin the two properties that make the checker safe to run before
every execution: it types a plan the way the engine executes it (including loop
bodies, which only exist as source text until they are reduced), and it stays
silent about everything no primitive has declared a rule for.
"""

from __future__ import annotations

import pytest

from voxlogica.analysis.type_checker import TypeChecker
from voxlogica.analysis.type_helpers import VoxTypeError, simple_type, signature_type
from voxlogica.analysis.types import (
    VoxAny,
    VoxBool,
    VoxFloat,
    VoxImage,
    VoxInt,
    VoxNumber,
    VoxRecord,
    VoxSequence,
    VoxString,
    is_subtype,
    join,
)
from voxlogica.main import build_workplan

pytestmark = pytest.mark.unit


def check(program: str):
    """Reduce a program and abstractly execute the resulting plan."""
    _, workplan = build_workplan(program, source_name="<test>")
    return TypeChecker(workplan.registry).check_plan(workplan.to_symbolic_plan())


# ── The abstract domain ──────────────────────────────────────────────────────


def test_numeric_widening_is_a_subtype_relation():
    assert is_subtype(VoxInt(), VoxNumber())
    assert is_subtype(VoxFloat(), VoxNumber())
    assert is_subtype(VoxInt(), VoxFloat())
    assert not is_subtype(VoxFloat(), VoxInt())
    assert not is_subtype(VoxString(), VoxNumber())


def test_any_is_compatible_in_both_directions():
    assert is_subtype(VoxAny(), VoxImage())
    assert is_subtype(VoxImage(), VoxAny())


def test_sequences_are_covariant():
    assert is_subtype(VoxSequence(VoxInt()), VoxSequence(VoxNumber()))
    assert not is_subtype(VoxSequence(VoxString()), VoxSequence(VoxNumber()))


def test_records_admit_width_and_depth_subtyping():
    wide = VoxRecord({"mean": VoxInt(), "std": VoxFloat()})
    narrow = VoxRecord({"mean": VoxNumber()})
    assert is_subtype(wide, narrow)
    assert not is_subtype(narrow, wide)


def test_records_are_hashable_so_they_can_key_the_closure_memo():
    assert hash(VoxRecord({"a": VoxInt()})) == hash(VoxRecord({"a": VoxInt()}))


def test_join_falls_back_to_any_rather_than_inventing_a_union():
    assert join(VoxInt(), VoxFloat()) == VoxFloat()
    assert join(VoxString(), VoxImage()) == VoxAny()
    assert join(VoxSequence(VoxInt()), VoxSequence(VoxFloat())) == VoxSequence(VoxFloat())


# ── Rules ────────────────────────────────────────────────────────────────────


def test_simple_type_accepts_a_subtype_and_rejects_an_unrelated_type():
    rule = simple_type([VoxNumber()], VoxBool())
    assert rule([VoxInt()]) == VoxBool()
    with pytest.raises(VoxTypeError):
        rule([VoxString()])
    with pytest.raises(VoxTypeError):
        rule([])


def test_signature_type_bounds_the_optional_arguments():
    rule = signature_type([VoxImage()], VoxFloat(), optional=[VoxInt()])
    assert rule([VoxImage()]) == VoxFloat()
    assert rule([VoxImage(), VoxInt()]) == VoxFloat()
    with pytest.raises(VoxTypeError):
        rule([VoxImage(), VoxInt(), VoxInt()])


# ── Abstract execution of a plan ─────────────────────────────────────────────


def test_literals_and_sequence_construction():
    result = check('let xs = [1,2,3]\nprint "xs" xs\n')
    assert result.ok
    assert result.goal_types["xs"] == VoxSequence(VoxFloat())


def test_index_yields_the_element_type():
    result = check('let xs = [1,2,3]\nprint "head" xs[0]\n')
    assert result.ok
    assert result.goal_types["head"] == VoxFloat()


def test_range_is_a_sequence_of_integers():
    result = check('print "r" range(0,5)\n')
    assert result.ok
    assert result.goal_types["r"] == VoxSequence(VoxInt())


def test_loop_body_is_typed_through_a_typed_hole():
    """A ``for`` over a non-constant sequence keeps its body as source text.

    The checker must reduce that body at the element type to see through it;
    ``[x, x]`` is only ``sequence(sequence(int))`` if ``x`` was bound to an int.
    """
    result = check('let ys = for x in range(0,5) do [x, x]\nprint "ys" ys\n')
    assert result.ok
    assert result.goal_types["ys"] == VoxSequence(VoxSequence(VoxInt()))


def test_nested_loops_are_typed_through_both_holes():
    program = (
        "let ys = for x in range(0,5) do (for y in range(0,3) do [x, y])\n"
        'print "ys" ys\n'
    )
    result = check(program)
    assert result.ok
    assert result.goal_types["ys"] == VoxSequence(VoxSequence(VoxSequence(VoxInt())))


def test_fold_yields_the_element_type():
    result = check('print "sum" (fold + range(0,5))\n')
    assert result.ok
    assert result.goal_types["sum"] == VoxInt()


def test_type_checking_does_not_add_nodes_to_the_plan():
    """Reducing a loop body interns nodes; they must not reach the engine."""
    _, workplan = build_workplan(
        'let ys = for x in range(0,5) do [x, x]\nprint "ys" ys\n', source_name="<test>"
    )
    plan = workplan.to_symbolic_plan()
    before = set(plan.nodes)
    TypeChecker(workplan.registry).check_plan(plan)
    assert set(plan.nodes) == before


# ── Diagnostics ──────────────────────────────────────────────────────────────


def test_a_declared_rule_rejects_a_wrong_argument_type():
    result = check('import "vox1"\nprint "x" num_add("a", 1)\n')
    assert not result.ok
    assert result.diagnostics[0].code == "E_TYPE"
    assert "num_add" in result.diagnostics[0].message


def test_a_type_error_inside_a_loop_body_is_reported():
    program = (
        'import "vox1"\n'
        "let ys = for x in range(0,5) do num_add(\"a\", x)\n"
        'print "ys" ys\n'
    )
    result = check(program)
    assert not result.ok
    assert any(d.code == "E_TYPE" for d in result.diagnostics)


def test_indexing_a_non_sequence_is_reported():
    result = check('import "vox1"\nlet n = num_add(1,2)\nprint "bad" n[0]\n')
    assert not result.ok
    assert any("expected a sequence" in d.message for d in result.diagnostics)


def test_a_primitive_without_a_rule_constrains_nothing():
    """Gradual by construction: silence, not a diagnostic, and ``any``."""
    from voxlogica.primitives.registry import PrimitiveRegistry

    registry = PrimitiveRegistry()
    assert registry.resolve("load").type_rule is None, (
        "`load` has gained a type rule; pick another undeclared primitive here"
    )
    result = check('print "x" load("nonexistent.nii.gz")\n')
    assert result.ok
    assert result.goal_types["x"] == VoxAny()


def test_scalar_arithmetic_is_typed_through_the_operator():
    result = check('import "vox1"\nprint "x" (1 + 2)\n')
    assert result.ok
    assert result.goal_types["x"] == VoxFloat()


def test_diagnostics_are_deduplicated_per_location():
    program = (
        'import "vox1"\n'
        'let bad = num_add("a", 1)\n'
        'print "one" bad\n'
        'print "two" bad\n'
    )
    result = check(program)
    assert len(result.diagnostics) == 1


def test_raise_on_error_surfaces_the_standard_static_analysis_error():
    from voxlogica.reducer import StaticAnalysisError

    result = check('import "vox1"\nprint "x" num_add("a", 1)\n')
    with pytest.raises(StaticAnalysisError):
        result.raise_on_error()


# ── CLI ──────────────────────────────────────────────────────────────────────


def _write(tmp_path, program: str):
    path = tmp_path / "program.imgql"
    path.write_text(program, encoding="utf-8")
    return str(path)


def test_typecheck_subcommand_prints_goal_types(tmp_path, capsys):
    from voxlogica.main import main

    path = _write(tmp_path, 'print "r" range(0,5)\n')
    assert main(["typecheck", path]) == 0
    assert "r: sequence(int)" in capsys.readouterr().out


def test_typecheck_subcommand_exits_two_on_a_type_error(tmp_path, capsys):
    from voxlogica.main import main

    path = _write(tmp_path, 'import "vox1"\nprint "x" num_add("a", 1)\n')
    assert main(["typecheck", path]) == 2
    assert "E_TYPE" in capsys.readouterr().err


def test_typecheck_subcommand_reports_a_program_with_no_goals(tmp_path, capsys):
    from voxlogica.main import main

    path = _write(tmp_path, "let x = 1\n")
    assert main(["typecheck", path]) == 0
    assert "No goals to check" in capsys.readouterr().out


def test_typecheck_json_output_is_machine_readable(tmp_path, capsys):
    import json

    from voxlogica.main import main

    path = _write(tmp_path, 'import "vox1"\nprint "x" num_add("a", 1)\n')
    assert main(["typecheck", "--json", path]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "E_TYPE"


# ── The closure-nesting bound ────────────────────────────────────────────────


def _nested_loops(depth: int) -> str:
    """A program nesting ``depth`` loops over a non-constant sequence."""
    body = f"[x{depth}]"
    for level in range(depth, 0, -1):
        body = f"(for x{level} in s do {body})"
    return f"let s = range(0,2)\nprint \"r\" {body}\n"


def test_nesting_below_the_bound_is_typed_exactly():
    _, workplan = build_workplan(_nested_loops(3), source_name="<test>")
    result = TypeChecker(workplan.registry).check_plan(workplan.to_symbolic_plan())
    assert result.ok and not result.warnings
    assert result.goal_types["r"] == VoxSequence(
        VoxSequence(VoxSequence(VoxSequence(VoxInt())))
    )


def test_reaching_the_bound_widens_to_any_and_says_so():
    """Precision is allowed to run out; doing it silently is not."""
    _, workplan = build_workplan(_nested_loops(3), source_name="<test>")
    checker = TypeChecker(workplan.registry, max_closure_depth=2)
    result = checker.check_plan(workplan.to_symbolic_plan())

    assert result.goal_types["r"] == VoxSequence(VoxSequence(VoxSequence(VoxAny())))
    assert [w.code for w in result.warnings] == ["W_TYPE_DEPTH"]
    assert "nested closure applications" in result.warnings[0].message


def test_the_bound_is_a_limit_not_an_error():
    """A run must not be refused because the checker stopped looking."""
    _, workplan = build_workplan(_nested_loops(3), source_name="<test>")
    result = TypeChecker(workplan.registry, max_closure_depth=1).check_plan(
        workplan.to_symbolic_plan()
    )
    assert result.warnings
    assert result.ok
    assert result.diagnostics == []


def test_a_warning_is_located_at_the_loop_that_runs_the_body():
    """A closure node carries no provenance; the applying node does."""
    _, workplan = build_workplan(_nested_loops(3), source_name="<test>")
    result = TypeChecker(workplan.registry, max_closure_depth=2).check_plan(
        workplan.to_symbolic_plan()
    )
    assert result.warnings[0].location is not None
    assert result.warnings[0].location.startswith("<test>:")


def test_a_body_that_cannot_be_reduced_is_located_at_the_loop_too():
    program = 'let ys = for x in range(0,5) do undefined_name(x)\nprint "ys" ys\n'
    _, workplan = build_workplan(program, source_name="<test>")
    result = TypeChecker(workplan.registry).check_plan(workplan.to_symbolic_plan())
    assert not result.ok
    assert result.diagnostics[0].location.startswith("<test>:")


# ── Overloaded primitives ────────────────────────────────────────────────────


def test_overloads_accepts_any_matching_alternative():
    from voxlogica.analysis.type_helpers import overloads

    rule = overloads([
        simple_type([VoxImage(), VoxImage()], VoxImage()),
        simple_type([VoxImage(), VoxNumber()], VoxImage()),
        simple_type([VoxNumber(), VoxNumber()], VoxFloat()),
    ])
    assert rule([VoxImage(), VoxImage()]) == VoxImage()
    assert rule([VoxImage(), VoxInt()]) == VoxImage()
    assert rule([VoxInt(), VoxFloat()]) == VoxFloat()


def test_overloads_joins_when_several_alternatives_match():
    """An `any` argument matches everything, so no single result is proven."""
    from voxlogica.analysis.type_helpers import overloads

    rule = overloads([
        simple_type([VoxImage()], VoxImage()),
        simple_type([VoxNumber()], VoxFloat()),
    ])
    assert rule([VoxAny()]) == VoxAny()
    # Alternatives that agree still give the precise answer.
    agreeing = overloads([
        simple_type([VoxImage()], VoxImage()),
        simple_type([VoxNumber()], VoxImage()),
    ])
    assert agreeing([VoxAny()]) == VoxImage()


def test_overloads_lists_the_candidates_when_none_match():
    from voxlogica.analysis.type_helpers import overloads

    rule = overloads([
        simple_type([VoxImage()], VoxImage()),
        simple_type([VoxNumber()], VoxFloat()),
    ])
    with pytest.raises(VoxTypeError) as raised:
        rule([VoxString()])
    message = str(raised.value)
    assert "no overload accepts (string)" in message
    assert "expected image" in message and "expected number" in message


# ── Rules derived from SimpleITK's own signatures ────────────────────────────


def test_a_swig_docstring_overload_set_becomes_one_rule():
    from voxlogica.primitives.simpleitk._signatures import type_rule_for

    def add(*args):
        """
        Add(Image image1, Image image2) -> Image
        Add(Image image1, double constant) -> Image
        Add(double constant, Image image2) -> Image
        """

    rule = type_rule_for("Add", add)
    assert rule([VoxImage(), VoxImage()]) == VoxImage()
    assert rule([VoxImage(), VoxFloat()]) == VoxImage()
    assert rule([VoxFloat(), VoxImage()]) == VoxImage()
    with pytest.raises(VoxTypeError):
        rule([VoxString(), VoxString()])


def test_defaulted_parameters_become_optional_arguments():
    from voxlogica.primitives.simpleitk._signatures import type_rule_for

    def threshold(*args):
        """BinaryThreshold(Image image1, double lowerThreshold=0.0, uint8_t insideValue=1) -> Image"""

    rule = type_rule_for("BinaryThreshold", threshold)
    assert rule([VoxImage()]) == VoxImage()
    assert rule([VoxImage(), VoxFloat(), VoxInt()]) == VoxImage()
    with pytest.raises(VoxTypeError):
        rule([])


def test_an_integer_parameter_accepts_any_number():
    """Every numeric literal in a VoxLogicA program is a float.

    The wrapper casts it, so demanding `int` here would reject working code.
    """
    from voxlogica.primitives.simpleitk._signatures import type_rule_for

    def cast(*args):
        """Cast(Image image, uint32_t pixelID) -> Image"""

    assert type_rule_for("Cast", cast)([VoxImage(), VoxFloat()]) == VoxImage()


def test_a_returned_integer_stays_precise():
    from voxlogica.primitives.simpleitk._signatures import type_rule_for

    def depth(*args):
        """GetDepth(Image image) -> unsigned int"""

    assert type_rule_for("GetDepth", depth)([VoxImage()]) == VoxInt()


def test_an_opaque_parameter_type_constrains_nothing():
    from voxlogica.primitives.simpleitk._signatures import type_rule_for

    def resample(*args):
        """Resample(Image image1, Transform transform) -> Image"""

    rule = type_rule_for("Resample", resample)
    assert rule([VoxImage(), VoxString()]) == VoxImage()


def test_a_python_annotated_function_falls_back_to_its_signature():
    """The ten wrappers SimpleITK annotates are the ones with no doc signature."""
    import SimpleITK as sitk

    from voxlogica.primitives.simpleitk._signatures import type_rule_for

    rule = type_rule_for("SmoothingRecursiveGaussian", sitk.SmoothingRecursiveGaussian)
    assert rule is not None
    assert rule([VoxImage()]) == VoxImage()
    with pytest.raises(VoxTypeError):
        rule([VoxString()])


def test_the_derived_rules_cover_almost_the_whole_namespace():
    from voxlogica.primitives.simpleitk.runtime import register_specs

    specs = register_specs()
    typed = sum(1 for spec, _ in specs.values() if spec.type_rule is not None)
    assert typed >= 340, f"only {typed} of {len(specs)} SimpleITK primitives are typed"


def test_real_simpleitk_calls_type_without_false_positives():
    program = (
        'import "simpleitk"\n'
        'let img = ReadImage("x.nii.gz")\n'
        'print "t" BinaryThreshold(img, 0.0, 1.0, 1, 0)\n'
    )
    result = check(program)
    assert result.ok, [d.message for d in result.diagnostics]
    assert result.goal_types["t"] == VoxImage()


def test_a_string_where_an_image_belongs_is_caught():
    result = check('import "simpleitk"\nprint "bad" Median("not an image")\n')
    assert not result.ok
    assert "expected image" in result.diagnostics[0].message


# ── Operators that dispatch over scalars, images and sequences ───────────────


def test_a_dispatching_operator_follows_its_operands():
    from voxlogica.analysis.type_helpers import dispatching_binary_type

    rule = dispatching_binary_type(VoxFloat())
    assert rule([VoxImage(), VoxImage()]) == VoxImage()
    assert rule([VoxImage(), VoxFloat()]) == VoxImage()
    assert rule([VoxFloat(), VoxImage()]) == VoxImage()
    assert rule([VoxInt(), VoxFloat()]) == VoxFloat()


def test_a_dispatching_operator_broadcasts_over_sequences():
    """`apply_binary_op` maps element-wise as soon as one operand is a sequence."""
    from voxlogica.analysis.type_helpers import dispatching_binary_type

    rule = dispatching_binary_type(VoxFloat())
    assert rule([VoxSequence(VoxInt()), VoxFloat()]) == VoxSequence(VoxFloat())
    assert rule([VoxSequence(VoxImage()), VoxFloat()]) == VoxSequence(VoxImage())
    assert rule([VoxSequence(VoxSequence(VoxInt())), VoxInt()]) == VoxSequence(
        VoxSequence(VoxFloat())
    )


def test_a_dispatching_operator_accepts_a_boolean_operand():
    """The scalar path calls float()/bool(), so `x == true` runs."""
    from voxlogica.analysis.type_helpers import dispatching_binary_type

    rule = dispatching_binary_type(VoxBool())
    assert rule([VoxBool(), VoxInt()]) == VoxBool()
    assert rule([VoxImage(), VoxBool()]) == VoxImage()


def test_a_dispatching_operator_stays_precise_under_a_known_operand():
    from voxlogica.analysis.type_helpers import dispatching_binary_type

    rule = dispatching_binary_type(VoxFloat())
    # One image operand proves the result is an image whatever the other is.
    assert rule([VoxImage(), VoxAny()]) == VoxImage()
    # Two unknowns prove nothing.
    assert rule([VoxAny(), VoxAny()]) == VoxAny()


def test_a_dispatching_operator_rejects_an_operand_it_cannot_combine():
    from voxlogica.analysis.type_helpers import dispatching_binary_type

    with pytest.raises(VoxTypeError):
        dispatching_binary_type(VoxFloat())([VoxString(), VoxInt()])


# ── Rules derived by the registry from a kernel's own annotations ────────────


def test_the_registry_derives_a_rule_from_kernel_annotations():
    """`vox1.dt` declares no rule but annotates `-> sitk.Image`."""
    from voxlogica.primitives.registry import PrimitiveRegistry

    registry = PrimitiveRegistry()
    registry.import_namespace("vox1")
    assert registry.load_type("vox1.dt")([VoxImage()]) == VoxImage()


def test_a_derived_rule_is_computed_once_and_reused():
    """Derivation is lazy so a run that never type-checks does not pay for it."""
    from voxlogica.primitives.registry import PrimitiveRegistry

    registry = PrimitiveRegistry()
    registry.import_namespace("vox1")
    assert registry.resolve("vox1.dt").type_rule is None
    assert registry._derived_type_rules == {}
    assert registry.load_type("vox1.dt") is registry.load_type("vox1.dt")


def test_a_derived_rule_never_narrows_the_declared_arity():
    """The spec's AritySpec, not the signature, bounds the derived rule."""
    from voxlogica.primitives.registry import PrimitiveRegistry

    registry = PrimitiveRegistry()
    registry.import_namespace("vox1")
    for name in ("vox1.dt", "vox1.near", "vox1.interior"):
        spec = registry.resolve(name)
        arguments = [VoxAny()] * spec.arity.min_args
        registry.load_type(name)(arguments)  # must not raise


def test_a_kwargs_kernel_still_declares_its_result():
    """A legacy `**kwargs` adapter has no argument structure, only a result."""
    from voxlogica.primitives.registry import PrimitiveRegistry

    registry = PrimitiveRegistry()
    registry.import_namespace("strings")
    assert registry.load_type("strings.concat")([VoxAny(), VoxAny()]) == VoxString()


def test_an_image_pipeline_keeps_its_type_through_the_operators():
    """What the BraTS programs actually do: threshold, dilate, combine."""
    program = (
        'import "vox1"\n'
        'import "simpleitk"\n'
        'let img = ReadImage("x.nii.gz")\n'
        "let mask = img > 0.5\n"
        'print "grown" (N(mask) & mask)\n'
        'print "size" volume(mask)\n'
    )
    result = check(program)
    assert result.ok, [d.message for d in result.diagnostics]
    assert result.goal_types["grown"] == VoxImage()
    assert result.goal_types["size"] == VoxFloat()


def test_a_string_operand_to_an_image_operator_is_caught():
    result = check('import "vox1"\nlet bad = "text" > 0.5\nprint "b" bad\n')
    assert not result.ok
    assert "no overload accepts" in result.diagnostics[0].message
