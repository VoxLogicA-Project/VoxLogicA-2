"""Helpers primitive authors use to declare a type rule, plus literal inference.

A primitive declares its abstract behaviour by attaching a ``TypeRule`` to its
kernel (``@primitive_type(...)``, picked up by the namespace's
``register_specs``) or by passing ``type_rule=`` to its ``PrimitiveSpec``. The
rule is the primitive's transfer function under abstract execution: it stands to
the type checker exactly as the kernel stands to the engine.
"""

from __future__ import annotations

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
