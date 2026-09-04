"""A handle is a tagged merkle hash, and nothing else.

Every argument the executor passes to a kernel is a reference to a node. Until
now that reference was resolved to a VALUE before the kernel ran, which is why
`default.sequence` -- whose kernel is `[value for _index, value in ordered]` and
which never looks at what it is given -- was once handed 51.4 GB in order to
build a list (issue #51).

A `Handle` is that reference, kept. It carries no resolver, no table, no cached
value, so it is a value in its own right: serializable, content-addressable, safe
to place inside another value and persist, and meaning the same node in a later
process. The tag is what separates a list of hashes from a list of strings, which
a bare `NodeId` could not.

Resolution is deliberately NOT a method here. A kernel that could resolve would
be a kernel that waits, and a wait inside kernel code makes the DAG stop being
the only witness of what depends on what. An operator that needs a value either
declares itself eager -- and the engine materializes its arguments as it always
has -- or rewrites itself into nodes that do.

See `doc/dev/handles-design.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

from voxlogica.lazy.ir import NodeId


#: JSON key that marks a handle in a stored value. A reserved dict key rather
#: than a string prefix, so it cannot collide with a string a program produced.
HANDLE_TAG = "__vox_handle__"


@dataclass(frozen=True, slots=True)
class Handle:
    """A reference to a node, by its merkle hash."""

    node: NodeId

    def __repr__(self) -> str:  # short, because these appear in sequences
        return f"@{self.node[:8]}"


def contains_handle(value: object) -> bool:
    """True if a handle is reachable from ``value``.

    Cheap negative answer for the common case: an eager operator never receives
    a handle, so it cannot produce one, and its completion skips the walk.
    """
    return _first_handle(value) is not None


def iter_handles(value: object):
    """Yield every handle reachable from ``value``.

    Containers only, and ITERATIVE: nothing in this codebase may recurse, because
    Python has no tail calls and this engine builds millions of nodes in one
    process. A value's nesting is usually shallow, but "usually" is not a bound,
    and a stack overflow inside a completion is not a failure anyone can read.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Handle):
            yield item
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, dict):
            stack.extend(item.values())


def _first_handle(value: object) -> Handle | None:
    for handle in iter_handles(value):
        return handle
    return None


def resolve_deep(value: object, resolve):
    """Replace every handle reachable from ``value`` with what it names.

    This is the eager adapter. A value with no handle in it is returned AS IS,
    not rebuilt: the guard costs one short-circuiting walk and no allocation.

    The walk itself is avoidable and the caller should avoid it. Only a lazy
    operator can put a handle in a value, so the executor decides from the
    PRODUCING node's spec whether to call this at all, and for the engine as it
    exists today the answer is never. That is what keeps "declaring nothing costs
    nothing" true rather than merely cheap; this guard is the safety net under
    it, not the mechanism.
    """
    if not contains_handle(value):
        return value
    return _rebuild(value, resolve)


def resolve_shallow(value: object, resolve):
    """Give back the container, without unpacking what is inside it.

    For an operator that reaches into a value without caring what it holds:
    `index` wants element *i*, and resolving the whole sequence to hand back one
    element is issue #51 in miniature.

    The value ITSELF is still resolved, and repeatedly: with nested indexing the
    inner `index` returns a handle, so the outer one is handed a handle where a
    container belongs and cannot subscript it. Measured as exactly that error.
    Only the CONTENTS are left alone.
    """
    while isinstance(value, Handle):
        value = resolve(value.node)
    return value


def _rebuild(value: object, resolve):
    """Replace handles throughout a value, without recursing.

    Containers are created empty, their slots filled from an explicit stack, and
    tuples frozen at the end -- innermost first, which is the reverse of the
    order they were discovered in. A handle naming a handle is followed by the
    inner `while`, so a chain of them costs no depth either.

    Nothing here may recurse: Python has no tail calls, and this engine builds
    millions of nodes in one process.
    """
    holder: list = [None]
    stack: list[tuple] = [(holder, 0, value)]
    freeze: list[tuple] = []
    while stack:
        target, key, item = stack.pop()
        while isinstance(item, Handle):
            item = resolve(item.node)
        if isinstance(item, (list, tuple)):
            made: list = [None] * len(item)
            target[key] = made
            if isinstance(item, tuple):
                freeze.append((target, key))
            for index, element in enumerate(item):
                stack.append((made, index, element))
        elif isinstance(item, dict):
            made_map: dict = {}
            target[key] = made_map
            for map_key, element in item.items():
                stack.append((made_map, map_key, element))
        else:
            target[key] = item
    for target, key in reversed(freeze):
        target[key] = tuple(target[key])
    return holder[0]



def revive_handles(value: object) -> object:
    """Turn the stored form of a handle back into one, anywhere in a value.

    Iterative, like everything else that walks a value here: a stored container
    is whatever a program produced, and its nesting is not something this code
    gets to assume a bound on.
    """
    holder: list = [None]
    stack: list[tuple] = [(holder, 0, value)]
    freeze: list[tuple] = []
    while stack:
        target, key, item = stack.pop()
        if isinstance(item, dict):
            node = item.get(HANDLE_TAG)
            if isinstance(node, str) and len(item) == 1:
                target[key] = Handle(node)
                continue
            made_map: dict = {}
            target[key] = made_map
            for map_key, element in item.items():
                stack.append((made_map, map_key, element))
        elif isinstance(item, (list, tuple)):
            made: list = [None] * len(item)
            target[key] = made
            if isinstance(item, tuple):
                freeze.append((target, key))
            for index, element in enumerate(item):
                stack.append((made, index, element))
        else:
            target[key] = item
    for target, key in reversed(freeze):
        target[key] = tuple(target[key])
    return holder[0]
