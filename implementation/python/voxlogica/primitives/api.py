"""Stable primitives API contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from voxlogica.lazy.ir import NodeSpec

PrimitiveKind = Literal["scalar", "sequence", "tree", "dataset", "effect"]
AttrType = type[Any] | tuple[type[Any], ...]
NodeId = str


@dataclass(frozen=True)
class AritySpec:
    """Arity contract for primitive calls."""

    min_args: int
    max_args: int | None = None

    @classmethod
    def fixed(cls, count: int) -> "AritySpec":
        return cls(min_args=count, max_args=count)

    @classmethod
    def variadic(cls, min_args: int = 0) -> "AritySpec":
        return cls(min_args=min_args, max_args=None)

    def validate(self, count: int) -> None:
        if count < self.min_args:
            raise ValueError(
                f"Expected at least {self.min_args} arguments, got {count}"
            )
        if self.max_args is not None and count > self.max_args:
            raise ValueError(
                f"Expected at most {self.max_args} arguments, got {count}"
            )


@dataclass(frozen=True)
class PrimitiveCall:
    """A purely symbolic primitive invocation."""

    args: tuple[NodeId, ...] = ()
    kwargs: tuple[tuple[str, NodeId], ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict)

    def kwargs_dict(self) -> dict[str, NodeId]:
        return dict(self.kwargs)


PlannerFn = Callable[[PrimitiveCall], "NodeSpec"]
KernelFn = Callable[..., Any]


@dataclass(frozen=True)
class PrimitiveSpec:
    """Primitive descriptor consumed by the planner and runtime."""

    name: str
    kind: PrimitiveKind
    arity: AritySpec
    attrs_schema: dict[str, AttrType]
    planner: PlannerFn
    kernel_name: str
    namespace: str = "default"
    description: str = ""
    is_legacy_adapter: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"


def default_planner_factory(operator_name: str, kind: PrimitiveKind = "scalar") -> PlannerFn:
    """Return a planner that maps PrimitiveCall directly to a primitive NodeSpec."""

    def _planner(call: PrimitiveCall) -> "NodeSpec":
        from voxlogica.lazy.ir import NodeSpec

        return NodeSpec(
            kind="primitive",
            operator=operator_name,
            args=call.args,
            kwargs=call.kwargs,
            attrs=call.attrs,
            output_kind=kind,
        )

    return _planner


def validate_spec(spec: PrimitiveSpec) -> None:
    """Validate a primitive spec before registration."""

    if not spec.name:
        raise ValueError("Primitive name cannot be empty")
    if "." in spec.name:
        raise ValueError("Primitive name must be unqualified")
    if not spec.namespace:
        raise ValueError("Primitive namespace cannot be empty")
    if not spec.kernel_name:
        raise ValueError("Primitive kernel_name cannot be empty")
    if spec.kind not in {"scalar", "sequence", "tree", "dataset", "effect"}:
        raise ValueError(f"Invalid primitive kind: {spec.kind}")
