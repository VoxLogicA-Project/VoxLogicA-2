"""Symbolic IR types for lazy planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NodeId = str
NodeKind = Literal["constant", "primitive", "closure"]
OutputKind = Literal[
    "scalar",
    "sequence",
    "tree",
    "dataset",
    "effect",
    "closure",
    "unknown",
]


@dataclass(frozen=True)
class Ref:
    """Reference to a symbolic node."""

    node_id: NodeId


@dataclass(frozen=True)
class NodeSpec:
    """Canonical symbolic node description."""

    kind: NodeKind
    operator: str
    args: tuple[NodeId, ...] = ()
    kwargs: tuple[tuple[str, NodeId], ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict)
    output_kind: OutputKind = "unknown"

    def normalized_kwargs(self) -> tuple[tuple[str, NodeId], ...]:
        return tuple(sorted(self.kwargs))


@dataclass(frozen=True)
class GoalSpec:
    """Goal to materialize from the symbolic plan."""

    operation: str
    id: NodeId
    name: str


@dataclass
class SymbolicPlan:
    """Reducer output: immutable definition graph + goals."""

    nodes: dict[NodeId, NodeSpec] = field(default_factory=dict)
    goals: list[GoalSpec] = field(default_factory=list)
    imported_namespaces: tuple[str, ...] = ()

    @property
    def node_count(self) -> int:
        return len(self.nodes)
