"""Derive type rules for the SimpleITK namespace from the wrapped functions.

The namespace exposes ~350 SWIG wrappers, which is far too many to annotate by
hand — and pointless, because each one already carries its own signature. Two
sources, in this order:

1. **The SWIG docstring.** Every wrapper's docstring opens with its C++
   signature(s), e.g.::

       Add(Image image1, Image image2) -> Image
       Add(Image image1, double constant) -> Image
       Add(double constant, Image image2) -> Image

   This is the richer source: it covers 342 of the 352 wrappers and is the
   *only* place the overload sets appear, because SWIG collapses them into a
   single ``*args`` Python function. Multiple lines become an ``overloads`` rule.

2. **The Python signature**, for the handful SimpleITK annotates natively
   (``ReadImage``, ``GetArrayFromImage``, ``Resample``, ...). These are almost
   exactly the ten the docstrings do not describe, so the two sources are
   complementary rather than redundant.

The mapping from C++ names to types is deliberately **directional**. In an
argument position a type is as permissive as the wrapper really is: every
integer width maps to ``number``, because the wrapper casts a float argument to
int when the parameter wants one (``runtime._wrap_sitk_function``), so demanding
``int`` there would reject `f(image, 3)` — every numeric literal in a VoxLogicA
program is a float. In a return position the same name maps to the precise type,
because there the wrapper's own output is what it says it is.

Anything not in the tables maps to ``any``: enums, transforms, and the pixel-ID
values are passed through as opaque objects, and claiming a type for them would
manufacture errors rather than find them.
"""

from __future__ import annotations

import collections.abc
import inspect
import re
import typing
from typing import Any, Callable

from voxlogica.analysis.type_helpers import overloads, signature_type
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
)

#: One overload line of a SWIG docstring: ``Name(params) -> Return``, or without
#: the arrow for a void function.
_SIGNATURE_LINE = re.compile(r"^\s*(\w+)\s*\((.*)\)\s*(?:->\s*(.+?))?\s*$")

_INTEGER_NAMES = frozenset({
    "int", "unsigned int", "signed int", "long", "unsigned long", "short",
    "unsigned short", "size_t", "int8_t", "uint8_t", "int16_t", "uint16_t",
    "int32_t", "uint32_t", "int64_t", "uint64_t",
})
_REAL_NAMES = frozenset({"double", "float", "long double"})
_STRING_NAMES = frozenset({"std::string", "itk::simple::PathType", "char"})
_INTEGER_VECTORS = frozenset({
    "VectorUInt8", "VectorUInt16", "VectorUInt32", "VectorUInt64",
    "VectorInt8", "VectorInt16", "VectorInt32", "VectorInt64", "VectorBool",
})
_REAL_VECTORS = frozenset({"VectorDouble", "VectorFloat"})


def _normalize(name: str) -> str:
    """Strip C++ decoration so a type name can be looked up as itself."""
    cleaned = name.replace("const", " ").replace("&", " ").replace("*", " ")
    return " ".join(cleaned.split())


def _cxx_type(name: str, *, argument: bool) -> VoxType:
    """Map one C++ type name to a static type, permissively for arguments."""
    normalized = _normalize(name)

    if normalized == "Image":
        return VoxImage()
    if normalized == "VectorOfImage":
        return VoxSequence(VoxImage())
    if normalized == "VectorString":
        return VoxSequence(VoxString())
    if normalized in _STRING_NAMES:
        return VoxString()
    if normalized in _INTEGER_NAMES:
        # See the module docstring: an int-shaped parameter accepts any number.
        return VoxNumber() if argument else VoxInt()
    if normalized in _REAL_NAMES:
        return VoxNumber() if argument else VoxFloat()
    if normalized == "bool":
        # SWIG's bool typemap takes any object's truth value, so a program that
        # passes 1 here runs. Claiming ``bool`` would reject it.
        return VoxAny() if argument else VoxBool()
    if normalized in _INTEGER_VECTORS:
        return VoxSequence(VoxNumber()) if argument else VoxSequence(VoxInt())
    if normalized in _REAL_VECTORS:
        return VoxSequence(VoxNumber()) if argument else VoxSequence(VoxFloat())

    # Enums, transforms, nested vectors, label maps: opaque by design.
    return VoxAny()


def _split_arguments(text: str) -> list[str]:
    """Split a parameter list on top-level commas.

    A default value can contain commas of its own — ``VectorUInt32
    radius=std::vector< unsigned int >(3, 1)`` — so bracket depth is tracked.
    """
    parts: list[str] = []
    depth = 0
    current = ""
    for character in text:
        if character in "<([":
            depth += 1
        elif character in ">)]":
            depth -= 1
        if character == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += character
    if current.strip():
        parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def _split_default(declaration: str) -> tuple[str, bool]:
    """Split ``Type name=default`` into its declaration and whether it defaults."""
    depth = 0
    for index, character in enumerate(declaration):
        if character in "<([":
            depth += 1
        elif character in ">)]":
            depth -= 1
        elif character == "=" and depth == 0:
            return declaration[:index].strip(), True
    return declaration.strip(), False


def _parse_docstring_signature(line: str, name: str) -> TypeRule | None:
    """Build one rule from one ``Name(params) -> Return`` line, if it is one."""
    match = _SIGNATURE_LINE.match(line)
    if match is None or match.group(1) != name:
        return None

    required: list[VoxType] = []
    optional: list[VoxType] = []
    for declaration in _split_arguments(match.group(2)):
        stripped, has_default = _split_default(declaration)
        if not stripped:
            continue
        tokens = stripped.split()
        # The last token is the parameter's name unless the whole declaration is
        # just a type, which SWIG emits for unnamed parameters.
        type_name = " ".join(tokens[:-1]) if len(tokens) > 1 else stripped
        (optional if has_default else required).append(
            _cxx_type(type_name, argument=True)
        )

    return_name = (match.group(3) or "void").strip()
    return_type = VoxAny() if _normalize(return_name) == "void" else _cxx_type(
        return_name, argument=False
    )
    return signature_type(required, return_type, optional=optional)


def _rules_from_docstring(name: str, func: Callable[..., Any]) -> list[TypeRule]:
    """Build one rule per overload line of a SWIG docstring."""
    rules: list[TypeRule] = []
    for line in (func.__doc__ or "").splitlines():
        if not line.strip():
            continue
        rule = _parse_docstring_signature(line, name)
        if rule is not None:
            rules.append(rule)
    return rules


def _python_type(annotation: Any, *, argument: bool) -> VoxType:
    """Map a Python annotation to a static type, permissively for arguments."""
    if annotation is inspect.Parameter.empty:
        return VoxAny()

    if isinstance(annotation, str):
        # SimpleITK writes some annotations as strings ('numpy.ndarray').
        return VoxImage() if "ndarray" in annotation else VoxAny()

    origin = typing.get_origin(annotation)
    if origin is not None:
        arguments = [a for a in typing.get_args(annotation) if a is not type(None)]
        if not arguments:
            return VoxAny()
        mapped = {_python_type(a, argument=argument) for a in arguments}
        if origin in (list, tuple, set, frozenset, collections.abc.Iterable,
                      collections.abc.Sequence, collections.abc.Collection):
            element = mapped.pop() if len(mapped) == 1 else VoxAny()
            return VoxSequence(element)
        # A union is only as precise as its least precise member.
        return mapped.pop() if len(mapped) == 1 else VoxAny()

    if not isinstance(annotation, type):
        return VoxAny()

    import numpy as np
    import SimpleITK as sitk

    if issubclass(annotation, sitk.Image) or issubclass(annotation, np.ndarray):
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


def _rule_from_python_signature(func: Callable[..., Any]) -> TypeRule | None:
    """Build a rule from a natively annotated Python signature, if it has one."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return None

    parameters = list(signature.parameters.values())
    if any(
        parameter.kind
        in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in parameters
    ):
        # A ``*args`` wrapper carries no positional structure; only its
        # docstring knows the overloads, and that path already ran.
        return None
    if not any(p.annotation is not inspect.Parameter.empty for p in parameters):
        return None

    required: list[VoxType] = []
    optional: list[VoxType] = []
    for parameter in parameters:
        mapped = _python_type(parameter.annotation, argument=True)
        if parameter.default is inspect.Parameter.empty:
            required.append(mapped)
        else:
            optional.append(mapped)

    return_type = _python_type(signature.return_annotation, argument=False)
    return signature_type(required, return_type, optional=optional)


def type_rule_for(name: str, func: Callable[..., Any]) -> TypeRule | None:
    """Return the derived type rule for one SimpleITK function, or ``None``.

    ``None`` means "declares nothing", which the checker reads as ``any`` — the
    same as any primitive that never had a rule.
    """
    docstring_rules = _rules_from_docstring(name, func)
    if len(docstring_rules) == 1:
        return docstring_rules[0]
    if docstring_rules:
        return overloads(docstring_rules)
    return _rule_from_python_signature(func)


def derived_rules(source_functions: dict[str, Callable[..., Any]]) -> dict[str, TypeRule]:
    """Derive rules for a whole namespace, skipping what cannot be described."""
    rules: dict[str, TypeRule] = {}
    for name, func in source_functions.items():
        rule = type_rule_for(name, func)
        if rule is not None:
            rules[name] = rule
    return rules
