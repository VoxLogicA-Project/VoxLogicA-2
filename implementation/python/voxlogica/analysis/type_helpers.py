"""Helpers primitive authors use to declare a type rule, plus literal inference.

A primitive declares its abstract behaviour by attaching a ``TypeRule`` to its
kernel (``@primitive_type(...)``, picked up by the namespace's
``register_specs``) or by passing ``type_rule=`` to its ``PrimitiveSpec``. The
rule is the primitive's transfer function under abstract execution: it stands to
the type checker exactly as the kernel stands to the engine.
"""

from __future__ import annotations

import collections.abc
import inspect
import typing
from typing import Any, Callable, Sequence

import numpy as np
import SimpleITK as sitk

from voxlogica.analysis.types import (
    TypeRule,
    VoxAny,
    VoxBool,
    VoxFloat,
    VoxImage,
    VoxInt,
    VoxMap,
    VoxNumber,
    VoxSequence,
    VoxString,
    VoxType,
    is_subtype,
    join_all,
)


class VoxTypeError(Exception):
    """Raised by a type rule when a primitive is called with the wrong argument types."""


# Order matters: ``bool`` is a subclass of ``int`` in Python, so it has to be
# tested before it. Everything not listed here infers as ``VoxAny``.
_PYTHON_TO_VOX_TYPE: tuple[tuple[type, VoxType], ...] = (
    (bool, VoxBool()),
    (int, VoxInt()),
    (float, VoxFloat()),
    (str, VoxString()),
    (sitk.Image, VoxImage()),
    (np.ndarray, VoxImage()),
)

#: Kept as a mapping for callers that want to inspect or extend the literal map.
PYTHON_TO_VOX_TYPE_MAP = dict(_PYTHON_TO_VOX_TYPE)


def primitive_type(rule: TypeRule) -> Callable[[Any], Any]:
    """Attach a type rule to a kernel function.

    ``registry`` reads the attribute back off the kernel when it synthesizes the
    ``PrimitiveSpec`` for namespaces that register kernels in bulk (``arrays``,
    ``vox1``). Namespaces that write their own ``PrimitiveSpec`` pass
    ``type_rule=`` directly instead.
    """

    def decorator(kernel):
        kernel.type_rule = rule
        return kernel

    return decorator


def is_number(candidate: VoxType) -> bool:
    """Return whether a type is a number (``int``, ``float``, or unspecified)."""
    return isinstance(candidate, VoxNumber)


def simple_type(argument_types: Sequence[VoxType], return_type: VoxType) -> TypeRule:
    """Build a rule for a primitive with one fixed signature.

    Arguments are matched by ``is_subtype``, so declaring ``VoxNumber()`` accepts
    both integers and floats, declaring ``VoxFloat()`` also accepts an integer,
    and ``VoxAny()`` accepts anything.
    """
    expected = list(argument_types)

    def rule(actual: list[VoxType]) -> VoxType:
        if len(actual) != len(expected):
            raise VoxTypeError(
                f"expected {len(expected)} argument(s) "
                f"({', '.join(str(t) for t in expected) or 'none'}), got {len(actual)}"
            )
        for index, (given, want) in enumerate(zip(actual, expected)):
            if not is_subtype(given, want):
                raise VoxTypeError(
                    f"argument {index} has type {given}, expected {want}"
                )
        return return_type

    return rule


#: Retained name for rules written against the number hierarchy. ``simple_type``
#: already accepts any ``VoxNumber`` where one is declared, so the two are the
#: same function; the alias keeps existing declarations readable.
overloaded_type = simple_type


def signature_type(
    required: Sequence[VoxType],
    return_type: VoxType,
    optional: Sequence[VoxType] = (),
) -> TypeRule:
    """Build a rule for a primitive with trailing optional arguments."""
    required_types = list(required)
    optional_types = list(optional)
    all_types = required_types + optional_types

    def rule(actual: list[VoxType]) -> VoxType:
        if not len(required_types) <= len(actual) <= len(all_types):
            bound = (
                f"{len(required_types)}"
                if not optional_types
                else f"{len(required_types)} to {len(all_types)}"
            )
            raise VoxTypeError(f"expected {bound} argument(s), got {len(actual)}")
        for index, (given, want) in enumerate(zip(actual, all_types)):
            if not is_subtype(given, want):
                raise VoxTypeError(f"argument {index} has type {given}, expected {want}")
        return return_type

    return rule


def overloads(alternatives: Sequence[TypeRule]) -> TypeRule:
    """Combine several signatures into one rule for an overloaded primitive.

    Every alternative is tried and the results of **all** that match are joined.
    Taking the first match instead would be wrong in the one case that matters:
    an argument of type ``any`` satisfies every alternative, so the first would
    win by accident and the call would be given a precise result type nobody
    proved. Joining keeps the answer as precise as the evidence allows and no
    more — for the SimpleITK overload sets, where every alternative returns an
    image, the join is still exactly ``image``.

    The call is rejected only when *no* alternative matches; the diagnostic then
    lists what each one wanted, because "no overload matches" without the
    candidates is not something a reader can act on.
    """
    candidates = list(alternatives)
    if not candidates:
        raise ValueError("overloads() needs at least one alternative")

    def rule(actual: list[VoxType]) -> VoxType:
        matched: list[VoxType] = []
        failures: list[str] = []
        for candidate in candidates:
            try:
                matched.append(candidate(actual))
            except VoxTypeError as exc:
                failures.append(str(exc))
        if not matched:
            given = ", ".join(str(t) for t in actual) or "no arguments"
            detail = "; ".join(dict.fromkeys(failures))
            raise VoxTypeError(f"no overload accepts ({given}): {detail}")
        return join_all(matched)

    return rule


def variadic_type(element_type: VoxType, return_type: VoxType) -> TypeRule:
    """Build a rule for a primitive taking any number of arguments of one type."""

    def rule(actual: list[VoxType]) -> VoxType:
        for index, given in enumerate(actual):
            if not is_subtype(given, element_type):
                raise VoxTypeError(f"argument {index} has type {given}, expected {element_type}")
        return return_type

    return rule


def sequence_of_arguments() -> TypeRule:
    """Build the rule of a sequence constructor: the join of the element types."""

    def rule(actual: list[VoxType]) -> VoxType:
        return VoxSequence(join_all(actual))

    return rule


def index_type() -> TypeRule:
    """Build the rule of an element-access primitive: ``(sequence, i) -> element``."""

    def rule(actual: list[VoxType]) -> VoxType:
        if not actual:
            raise VoxTypeError("expected a sequence argument, got none")
        element = element_of(actual[0])
        if element is None:
            raise VoxTypeError(f"argument 0 has type {actual[0]}, expected a sequence")
        for index, given in enumerate(actual[1:], start=1):
            if not is_subtype(given, VoxNumber()):
                raise VoxTypeError(f"argument {index} has type {given}, expected number")
        return element

    return rule


def slice_type() -> TypeRule:
    """Build the rule of a windowing primitive: the sequence type is preserved."""

    def rule(actual: list[VoxType]) -> VoxType:
        if not actual:
            raise VoxTypeError("expected a sequence argument, got none")
        element = element_of(actual[0])
        if element is None:
            raise VoxTypeError(f"argument 0 has type {actual[0]}, expected a sequence")
        for index, given in enumerate(actual[1:], start=1):
            if not is_subtype(given, VoxNumber()):
                raise VoxTypeError(f"argument {index} has type {given}, expected number")
        return VoxSequence(element)

    return rule


def element_of(container: VoxType) -> VoxType | None:
    """Return the element type of a sequence, or ``None`` if it is not one.

    ``VoxAny`` yields ``VoxAny``: nothing is known about the container, so
    nothing can be known about its elements either, and that is not an error.
    """
    if isinstance(container, VoxAny):
        return VoxAny()
    if isinstance(container, VoxSequence):
        return container.element_type
    return None


def broadcasting_type(inner: TypeRule) -> TypeRule:
    """Lift a binary rule through element-wise sequence broadcasting.

    ``default._sequence_math.apply_binary_op`` — which every arithmetic,
    comparison and boolean operator in ``default`` and ``vox1`` runs through —
    applies the scalar operation element by element as soon as either operand is
    sequence-like, and returns a list. The type of ``xs + 1`` is therefore the
    type of ``x + 1`` wrapped in a sequence, which is what this expresses,
    recursively for a sequence of sequences.
    """

    def rule(actual: list[VoxType]) -> VoxType:
        if any(isinstance(given, VoxSequence) for given in actual):
            elements = [
                given.element_type if isinstance(given, VoxSequence) else given
                for given in actual
            ]
            return VoxSequence(rule(elements))
        return inner(actual)

    return rule


def dispatching_binary_type(scalar_result: VoxType) -> TypeRule:
    """Build the rule of a binary operator over scalars, images and sequences.

    Every arithmetic, comparison and boolean operator in ``default`` and
    ``vox1`` has the same shape: if either operand is an image the result is an
    image, otherwise both are scalars and the result is ``scalar_result``; and
    all of them run through ``apply_binary_op``, which maps element-wise over a
    sequence operand.

    ``bool`` sits beside ``number`` among the scalar operands on purpose. The
    scalar paths call ``float()`` or ``bool()`` on what they are given, so
    ``x == true`` runs, and a rule that rejected it would invent an error.
    """
    scalars: tuple[VoxType, ...] = (VoxBool(), VoxNumber())
    alternatives = [simple_type([VoxImage(), VoxImage()], VoxImage())]
    alternatives += [simple_type([VoxImage(), s], VoxImage()) for s in scalars]
    alternatives += [simple_type([s, VoxImage()], VoxImage()) for s in scalars]
    alternatives += [
        simple_type([left, right], scalar_result)
        for left in scalars
        for right in scalars
    ]
    return broadcasting_type(overloads(alternatives))


def python_annotation_type(annotation: Any, *, argument: bool) -> VoxType:
    """Map a Python annotation to a static type, permissively for arguments.

    Directional for the same reason the SimpleITK mapping is: an ``int``-shaped
    parameter has to accept any number, because every numeric literal in a
    VoxLogicA program is a float, while an ``int`` *result* really is one.
    ``bool`` in an argument position maps to ``any``: a kernel that takes one
    calls ``bool()`` on whatever it gets, so a program passing a number runs.
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return VoxAny()

    if isinstance(annotation, str):
        # Unresolved string annotation (a module using `from __future__ import
        # annotations` whose names could not be resolved). Match on the name.
        name = annotation.rsplit(".", 1)[-1]
        return {
            "Image": VoxImage(),
            "ndarray": VoxImage(),
            "str": VoxString(),
            "bool": VoxAny() if argument else VoxBool(),
            "int": VoxNumber() if argument else VoxInt(),
            "float": VoxNumber() if argument else VoxFloat(),
        }.get(name, VoxAny())

    origin = typing.get_origin(annotation)
    if origin is not None:
        arguments = [a for a in typing.get_args(annotation) if a is not type(None)]
        if not arguments:
            return VoxAny()
        mapped = {python_annotation_type(a, argument=argument) for a in arguments}
        if origin in (dict, collections.abc.Mapping, collections.abc.MutableMapping):
            key, value = (typing.get_args(annotation) + (Any, Any))[:2]
            return VoxMap(
                python_annotation_type(key, argument=argument),
                python_annotation_type(value, argument=argument),
            )
        if origin in (list, tuple, set, frozenset, collections.abc.Iterable,
                      collections.abc.Sequence, collections.abc.Collection):
            return VoxSequence(mapped.pop() if len(mapped) == 1 else VoxAny())
        # A union is only as precise as its least precise member.
        return mapped.pop() if len(mapped) == 1 else VoxAny()

    if not isinstance(annotation, type):
        return VoxAny()
    if issubclass(annotation, (sitk.Image, np.ndarray)):
        return VoxImage()
    if issubclass(annotation, bool):
        return VoxAny() if argument else VoxBool()
    if issubclass(annotation, int):
        return VoxNumber() if argument else VoxInt()
    if issubclass(annotation, float):
        return VoxNumber() if argument else VoxFloat()
    if issubclass(annotation, str):
        return VoxString()
    return VoxAny()


def rule_from_signature(func: Callable[..., Any], arity: Any = None) -> TypeRule | None:
    """Derive a rule from a kernel's own Python annotations, or ``None``.

    ``None`` when the function carries no usable annotation at all, or when it
    is variadic and therefore has no positional structure to describe.

    When ``arity`` is given it, not the signature, decides how many arguments
    the rule accepts, and the mapped types are padded with ``any`` to fit. A
    kernel's declared ``AritySpec`` and its Python signature can legitimately
    disagree — the spec is what the reducer enforces — and a rule that rejected
    a call the reducer accepts would be a defect of this analysis, not of the
    program.
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return None

    parameters = list(signature.parameters.values())

    # Resolve string annotations against the defining module where possible.
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # noqa: BLE001 - an unresolvable hint is not fatal here
        hints = {}

    if any(
        parameter.kind
        in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in parameters
    ):
        # A ``*args``/``**kwargs`` kernel has no positional structure to
        # describe, but its result annotation still says what comes out — which
        # is what a caller's type depends on. Arguments go unchecked.
        declared = python_annotation_type(
            hints.get("return", signature.return_annotation), argument=False
        )
        if isinstance(declared, VoxAny):
            return None
        return lambda actual: declared

    def annotation_of(parameter: inspect.Parameter) -> Any:
        return hints.get(parameter.name, parameter.annotation)

    if not any(
        annotation_of(p) is not inspect.Parameter.empty for p in parameters
    ) and "return" not in hints and signature.return_annotation is signature.empty:
        return None

    mapped = [
        python_annotation_type(annotation_of(p), argument=True) for p in parameters
    ]
    return_type = python_annotation_type(
        hints.get("return", signature.return_annotation), argument=False
    )

    if arity is None:
        required = [
            mapped[index]
            for index, parameter in enumerate(parameters)
            if parameter.default is inspect.Parameter.empty
        ]
        optional = mapped[len(required):]
        return signature_type(required, return_type, optional=optional)

    if arity.max_args is None:
        # Variadic by declaration: only the result is describable.
        def rule(actual: list[VoxType]) -> VoxType:
            if len(actual) < arity.min_args:
                raise VoxTypeError(
                    f"expected at least {arity.min_args} argument(s), got {len(actual)}"
                )
            return return_type

        return rule

    expected = (mapped + [VoxAny()] * arity.max_args)[: arity.max_args]
    return signature_type(
        expected[: arity.min_args], return_type, optional=expected[arity.min_args:]
    )


def infer_literal_type(value: Any) -> VoxType:
    """Infer the static type of a constant node's Python value.

    Never raises: an unrecognized literal infers as ``VoxAny`` so that a
    program using a value the map does not describe still type-checks.
    """
    for python_type, vox_type in _PYTHON_TO_VOX_TYPE:
        if isinstance(value, python_type):
            return vox_type
    if isinstance(value, (list, tuple)):
        return VoxSequence(join_all(infer_literal_type(item) for item in value))
    return VoxAny()
