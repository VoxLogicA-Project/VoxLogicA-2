"""How a node is evaluated -- asked here, decided nowhere else.

The engine has TWO roads to a node's value: the dispatch path, which calls the
kernel, and the miss path (`_rematerialize`), which rebuilds a value that was
evicted or never resident. Both must agree about what a node IS, and until this
module they did not: dispatch knew that `for_loop` is expanded rather than
computed, and the miss path called its kernel -- a real kernel, belonging to the
strict runtime, which reconstructs a closure the engine never builds. It failed
with `for_loop requires closure argument at key 'closure' or '1'`.

That was not a missing guard. It was a decision taken in one place and consulted
in another, and the same shape had already appeared twice in this design: the
`_materialize` boundary that nobody adapted, and `_SEQUENCE_OPERATORS` answering
three unrelated questions under one name.

So the decision lives in one function, and everything that evaluates or
classifies a node calls it. A new operator that grows the graph writes
`rewrite=True` in its spec and is routed correctly everywhere, because there is
nowhere else to tell.

See doc/dev/handles-design.md section 15.
"""

from __future__ import annotations

from dataclasses import dataclass

from voxlogica.primitives.registry import PrimitiveRegistry


@dataclass(frozen=True, slots=True)
class Modes:
    """What an operator asks of the engine.

    ``lazy`` and ``shallow`` describe how its ARGUMENTS arrive and are mutually
    exclusive. ``rewrite`` is orthogonal to both and describes what evaluating it
    DOES: `default.sequence` is lazy and never rewrites; `for_loop` rewrites and
    never sees a handle.
    """

    lazy: bool = False
    shallow: bool = False
    rewrite: bool = False


#: The registry-free answer to "does this operator grow the graph".
#:
#: `modes_of` is the real answer and reads the spec, but it needs a registry, and
#: one caller legitimately has none: NodeTable reports loop nodes as they are
#: interned and must not depend on the scheduler. That caller used to keep its
#: own copy of this list -- a third copy, in a design whose whole defect was one
#: fact recorded twice. So the fact lives here, once, and the table imports a
#: pure function.
#:
#: An operator that declares `rewrite=True` without appearing here is still
#: routed correctly everywhere it matters; what it loses is being counted in the
#: table's loop-nesting report, which is telemetry.
_REWRITE_BY_NAME = frozenset({"for_loop", "default.for_loop", "map", "default.map"})


def grows_the_graph_by_name(operator: str | None) -> bool:
    """Whether an operator expands, answered without a registry."""
    return bool(operator) and operator in _REWRITE_BY_NAME


_DEFAULT = Modes()
_CACHE: dict[tuple[int, str], Modes] = {}


def modes_of(registry: PrimitiveRegistry, operator: str) -> Modes:
    """The modes of one operator. Memoized: this sits on every dispatch."""
    key = (id(registry), operator)
    cached = _CACHE.get(key)
    if cached is None:
        try:
            spec = registry.get_spec(operator)
        except Exception:  # noqa: BLE001 -- an unknown operator asks for nothing
            cached = _DEFAULT
        else:
            cached = Modes(
                lazy=bool(getattr(spec, "lazy", False)),
                shallow=bool(getattr(spec, "shallow", False)),
                rewrite=bool(getattr(spec, "rewrite", False)),
            )
            if cached.lazy and cached.shallow:
                raise ValueError(
                    f"{operator}: lazy and shallow are exclusive -- arguments "
                    f"arrive either as handles or as values")
        _CACHE[key] = cached
    return cached


def reset_cache() -> None:
    """Forget memoized modes. For tests that build registries in a loop."""
    _CACHE.clear()


class NeedsExpansion(Exception):
    """A node that grows the graph cannot be rebuilt by calling a kernel.

    Raised by the miss path so the scheduler re-registers the node for
    expansion. It is an outcome, not a failure: the value is still obtainable,
    just not by the road the caller took.
    """

    def __init__(self, node_id: str):
        super().__init__(f"{node_id} must be expanded, not computed")
        self.node_id = node_id
