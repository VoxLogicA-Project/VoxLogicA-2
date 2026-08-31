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
    """Yield every handle reachable from ``value``, depth first.

    Containers only. Depth is the nesting depth of the value and cannot cycle:
    handles name nodes, and nodes form a DAG.
    """
    if isinstance(value, Handle):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_handles(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_handles(item)


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


def _rebuild(value: object, resolve):
    if isinstance(value, Handle):
        return _rebuild(resolve(value.node), resolve)
    if isinstance(value, list):
        return [_rebuild(item, resolve) for item in value]
    if isinstance(value, tuple):
        return tuple(_rebuild(item, resolve) for item in value)
    if isinstance(value, dict):
        return {key: _rebuild(item, resolve) for key, item in value.items()}
    return value
