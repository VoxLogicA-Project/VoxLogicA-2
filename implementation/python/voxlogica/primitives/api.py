"""Public contracts for defining primitives.

Primitive authors use these types to describe both the symbolic DAG node shape
and the runtime kernel that implements the operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from voxlogica.lazy.ir import NodeSpec

PrimitiveKind = Literal["scalar", "sequence", "tree", "dataset", "effect", "overlay"]
AttrType = type[Any] | tuple[type[Any], ...]
NodeId = str


@dataclass(frozen=True)
class AritySpec:
    """Arity contract for primitive calls."""

    min_args: int
    max_args: int | None = None

    @classmethod
    def fixed(cls, count: int) -> "AritySpec":
        """Construct an arity contract that requires exactly ``count`` args."""
        return cls(min_args=count, max_args=count)

    @classmethod
    def variadic(cls, min_args: int = 0) -> "AritySpec":
        """Construct an arity contract with no upper bound."""
        return cls(min_args=min_args, max_args=None)

    def validate(self, count: int) -> None:
        """Raise when the provided argument count violates this contract."""
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
    """A purely symbolic primitive invocation.

    Primitive calls refer to dependency node ids rather than to concrete values.
    """

    args: tuple[NodeId, ...] = ()
    kwargs: tuple[tuple[str, NodeId], ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict)

    def kwargs_dict(self) -> dict[str, NodeId]:
        """Return keyword arguments as a normal dictionary for convenience."""
        return dict(self.kwargs)


PlannerFn = Callable[[PrimitiveCall], "NodeSpec"]
KernelFn = Callable[..., Any]


@dataclass(frozen=True)
class ElementwiseSpec:
    """Opt-in metadata marking a primitive as fusable into a schedule-time cone.

    ``expr`` is a scalar expression fragment over positional placeholders
    ``{0}, {1}, ...`` matching the node's ``args`` order (NOT necessarily
    "image first" — e.g. ``vox1.leq_sv(value, image)`` puts the scalar at
    ``{0}``). It feeds the Phase-2 numba codegen (see
    ``doc/specs/semantic-queueing-fusion.md`` §3.2b); Phase 1 (Stage A) does
    not evaluate it at all — Stage A batches dispatch of the real kernels
    unchanged, so ``expr`` is declared here only so the fusable set is
    defined in one place. It is UNVALIDATED until the Phase-2 property tests
    check it bit-identical against the real kernel: do not trust it as
    correct before then.
    """

    expr: str
    out_dtype: str
    commutes_scalar: bool = True

    # ``out_dtype`` is usually a literal numpy dtype name ("uint8", "float32",
    # ...): the safe default when the result type does not depend on which
    # argument type flows in (e.g. every comparison here always yields a 0/1
    # mask, regardless of the operand dtype(s) — ITK's own comparison-filter
    # convention). Some primitives are NOT like that: ``mask(image, cond)``'s
    # output dtype tracks ``image``, whatever it happens to be at that call
    # site, so a single fixed string would be silently wrong at any call
    # where ``image`` isn't the type the string names. For those, write
    # ``"argN"`` (e.g. ``"arg0"``) to mean "the runtime dtype of positional
    # argument N" — resolved per-dispatch in
    # ``engine/numba_fusion.py::resolve_out_dtype``, recursively through
    # intra-cone member references if arg N is itself a fused predecessor.


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
    elementwise: ElementwiseSpec | None = None
    # True iff this primitive's KERNEL (not just its Stage-B expr fragment)
    # takes and returns numpy arrays directly, rather than sitk.Image. Set on
    # single-pass, memory-bound ops (not/and/or/comparisons/mask) that
    # measured 2-6x faster in numpy than the equivalent ITK filter call, with
    # zero cost in the common case: sitk -> numpy is a zero-copy cached view
    # (arrays.py's PolyArray.np()), so a numpy-native kernel reading
    # ITK-produced data pays nothing for it; only numpy -> sitk (needed if
    # the very next consumer is ITK-only) is a real copy. See
    # engine/executor.py's module docstring for how this changes the
    # PolyArray adapter boundary, and manuscripts/engine-scaling-2026-07.md
    # Part IV for the measurements this was added to chase. Independent of
    # ``elementwise``: Stage B's numba codegen never calls the kernel body at
    # all (it generates its own scalar loop from ``ElementwiseSpec.expr``),
    # so converting a kernel's Python implementation to numpy does not change
    # Stage B's behavior in any way.
    numpy_native: bool = False

    @property
    def qualified_name(self) -> str:
        """Return the fully qualified `<namespace>.<name>` primitive name."""
        return f"{self.namespace}.{self.name}"


def default_planner_factory(operator_name: str, kind: PrimitiveKind = "scalar") -> PlannerFn:
    """Return the standard planner for a direct primitive-to-node mapping."""

    def _planner(call: PrimitiveCall) -> "NodeSpec":
        """Translate one symbolic primitive call into the standard node shape."""
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
    """Validate a primitive specification before registration."""

    if not spec.name:
        raise ValueError("Primitive name cannot be empty")
    if "." in spec.name:
        raise ValueError("Primitive name must be unqualified")
    if not spec.namespace:
        raise ValueError("Primitive namespace cannot be empty")
    if not spec.kernel_name:
        raise ValueError("Primitive kernel_name cannot be empty")
    if spec.kind not in {"scalar", "sequence", "tree", "dataset", "effect", "overlay"}:
        raise ValueError(f"Invalid primitive kind: {spec.kind}")
