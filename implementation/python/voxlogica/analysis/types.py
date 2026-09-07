"""The abstract domain of the type checker: VoxLogicA's static types.

These are the *abstract values* manipulated by ``analysis.type_checker`` when it
runs a plan abstractly. Each concrete runtime value has one of these as its
description; ``VoxAny`` is the top of the lattice and stands for "not governed
by any declared rule", which is what an unannotated primitive produces.

The domain is deliberately structural and closed: there are no type variables
and no unification. Polymorphism is expressed by the *rules* (a ``TypeRule`` is
an arbitrary function of the argument types) rather than by the types, which
keeps checking a single bottom-up pass over the DAG with no constraint solver.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping


class VoxType(ABC):
    """Base class of every static type."""

    def __str__(self) -> str:  # pragma: no cover - overridden by every subclass
        return type(self).__name__


@dataclass(frozen=True)
class VoxAny(VoxType):
    """Top of the lattice: an unconstrained value.

    Produced by any primitive without a declared type rule, by a literal whose
    Python type is not in the literal map, and by every construct the checker
    declines to reason about. ``VoxAny`` is compatible with everything in both
    directions, which is what makes the system *gradual*: adding rules can only
    ever turn silence into a diagnostic, never a working program into a
    rejected one.
    """

    def __str__(self) -> str:
        return "any"


@dataclass(frozen=True)
class VoxNumber(VoxType):
    """A number of unspecified precision; supertype of ``VoxInt``/``VoxFloat``."""

    def __str__(self) -> str:
        return "number"


@dataclass(frozen=True)
class VoxInt(VoxNumber):
    def __str__(self) -> str:
        return "int"


@dataclass(frozen=True)
class VoxFloat(VoxNumber):
    def __str__(self) -> str:
        return "float"


@dataclass(frozen=True)
class VoxBool(VoxType):
    def __str__(self) -> str:
        return "bool"


@dataclass(frozen=True)
class VoxString(VoxType):
    def __str__(self) -> str:
        return "string"


@dataclass(frozen=True)
class VoxImage(VoxType):
    def __str__(self) -> str:
        return "image"


@dataclass(frozen=True)
class VoxSequence(VoxType):
    """A homogeneous sequence. Covariant in its element type."""

    element_type: VoxType

    def __str__(self) -> str:
        return f"sequence({self.element_type})"


@dataclass(frozen=True)
class VoxClosure(VoxType):
    """A one-argument closure. Contravariant in the argument, covariant in the result."""

    argument_type: VoxType
    return_type: VoxType

    def __str__(self) -> str:
        return f"{self.argument_type} -> {self.return_type}"


@dataclass(frozen=True)
class VoxRecord(VoxType):
    """A record with statically known field names.

    Constructed from a mapping (``VoxRecord({"mean": VoxFloat()})``) but stored
    as a sorted tuple of pairs so the type stays hashable — the checker keys its
    closure-application memo on types, so every type must be usable as a dict key.
    """

    fields: tuple[tuple[str, VoxType], ...]

    def __init__(self, fields: Mapping[str, VoxType] | Iterable[tuple[str, VoxType]]):
        object.__setattr__(self, "fields", tuple(sorted(dict(fields).items())))

    @property
    def field_map(self) -> dict[str, VoxType]:
        """Return the fields as an ordinary dictionary."""
        return dict(self.fields)

    def __str__(self) -> str:
        body = ", ".join(f"{name}: {value}" for name, value in self.fields)
        return "{" + body + "}"


@dataclass(frozen=True)
class VoxMap(VoxType):
    """A homogeneous mapping. Covariant in both key and value type."""

    key_type: VoxType
    value_type: VoxType

    def __str__(self) -> str:
        return f"map({self.key_type}, {self.value_type})"


#: A primitive's abstract transfer function: argument types in, result type out.
#: It raises ``VoxTypeError`` (see ``analysis.type_helpers``) to reject a call.
TypeRule = Callable[[list[VoxType]], VoxType]


def is_subtype(actual: VoxType, expected: VoxType) -> bool:
    """Return whether a value of type ``actual`` is acceptable where ``expected`` is required.

    ``VoxAny`` is compatible with everything in both directions; see ``VoxAny``
    for why the checker is gradual rather than sound.
    """
    if isinstance(actual, VoxAny) or isinstance(expected, VoxAny):
        return True
    if actual == expected:
        return True

    # int <= float <= number: numeric widening, so a rule asking for a float
    # accepts an integer literal and a rule asking for "a number" accepts both.
    if isinstance(expected, VoxNumber) and isinstance(actual, VoxNumber):
        if type(expected) is VoxNumber:
            return True
        if isinstance(expected, VoxFloat) and isinstance(actual, VoxInt):
            return True
        return type(actual) is type(expected)

    if isinstance(expected, VoxSequence) and isinstance(actual, VoxSequence):
        return is_subtype(actual.element_type, expected.element_type)

    if isinstance(expected, VoxMap) and isinstance(actual, VoxMap):
        return is_subtype(actual.key_type, expected.key_type) and is_subtype(
            actual.value_type, expected.value_type
        )

    if isinstance(expected, VoxRecord) and isinstance(actual, VoxRecord):
        # Width plus depth subtyping: a wider record is usable where a narrower
        # one is expected, provided every shared field is itself a subtype.
        available = actual.field_map
        return all(
            name in available and is_subtype(available[name], field_type)
            for name, field_type in expected.fields
        )

    if isinstance(expected, VoxClosure) and isinstance(actual, VoxClosure):
        return is_subtype(expected.argument_type, actual.argument_type) and is_subtype(
            actual.return_type, expected.return_type
        )

    return False


def join(left: VoxType, right: VoxType) -> VoxType:
    """Return the least upper bound of two types in this lattice.

    Used wherever one static type has to describe several runtime values at
    once — the elements of a sequence literal, or a fold's accumulator. Types
    with no common supertype below ``VoxAny`` join to ``VoxAny`` rather than
    producing a union, because the domain has no unions.
    """
    if left == right:
        return left
    if isinstance(left, VoxAny) or isinstance(right, VoxAny):
        return VoxAny()

    if isinstance(left, VoxNumber) and isinstance(right, VoxNumber):
        if is_subtype(left, right):
            return right
        if is_subtype(right, left):
            return left
        return VoxNumber()

    if isinstance(left, VoxSequence) and isinstance(right, VoxSequence):
        return VoxSequence(join(left.element_type, right.element_type))

    if isinstance(left, VoxMap) and isinstance(right, VoxMap):
        return VoxMap(join(left.key_type, right.key_type), join(left.value_type, right.value_type))

    if isinstance(left, VoxRecord) and isinstance(right, VoxRecord):
        # The common fields, joined: the widest record both values satisfy.
        other = right.field_map
        shared = {
            name: join(field_type, other[name])
            for name, field_type in left.fields
            if name in other
        }
        return VoxRecord(shared)

    if isinstance(left, VoxClosure) and isinstance(right, VoxClosure):
        return VoxClosure(VoxAny(), join(left.return_type, right.return_type))

    return VoxAny()


def join_all(types: Iterable[VoxType]) -> VoxType:
    """Join a whole iterable of types; an empty iterable joins to ``VoxAny``."""
    result: VoxType | None = None
    for candidate in types:
        result = candidate if result is None else join(result, candidate)
    return result if result is not None else VoxAny()
