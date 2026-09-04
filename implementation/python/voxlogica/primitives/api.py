"""Public contracts for defining primitives.

Primitive authors use these types to describe both the symbolic DAG node shape
and the runtime kernel that implements the operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TYPE_CHECKING
from voxlogica.analysis.types import TypeRule

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
class StencilSpec:
    """Opt-in metadata marking a primitive as a fusable NEIGHBOURHOOD op.

    An ``ElementwiseSpec`` member reads its inputs at the voxel being written;
    a stencil member reads them across a ``(2*radius+1)**3`` box around it.
    That difference is why the two cannot share one spec: the fused loop must
    switch from flat 1D indexing to a real ``(z, y, x)`` nest with clamped
    boundary reads, which is a different calling convention (see
    ``engine/numba_fusion.py::ConeShape.spatial``).

    The generated code is::

        acc = <reduce> over the box of ``neighbour_expr``
        out = ``result_expr`` formatted with (acc, centre voxel)

    ``neighbour_expr`` and ``result_expr`` take ``{0}``/``{1}`` placeholders
    rather than being hardcoded because a morphological op's exact ITK
    semantics are rarely the naive reduction: ``vox1.near`` is not "max over
    the box", it is "1 where a voxel *equal to 1* is in the box, else the
    ORIGINAL voxel", since sitk.BinaryDilate copies its input and then sets
    dilated pixels to the foreground value. Encoding that faithfully is the
    whole point — a fused kernel that is 24x faster but not bit-identical is
    not a fused kernel, it is a bug.

    ``input_dtypes`` names the numpy dtypes for which the expressions above
    are known bit-identical to the real kernel. Anything else refuses Stage B
    and takes the normal path, so widening a stencil's applicability is a
    deliberate act that has to be backed by a test.
    """

    radius: int
    reduce: str  # "max" or "min"
    neighbour_expr: str  # over {0} = a neighbour's value
    result_expr: str  # over {0} = the reduction, {1} = the centre voxel
    out_dtype: str
    input_dtypes: tuple[str, ...]


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
    stencil: StencilSpec | None = None
    #: Receive HANDLES instead of values -- one field, because laziness that is
    #: awkward to opt into will not be opted into. A lazy kernel is handed a
    #: `Handle` per argument (a merkle hash, see voxlogica/handles.py) and may
    #: pass them on, reorder them, put them in its result or drop them. It may
    #: NOT resolve them: a kernel that waits for a value puts a wait inside
    #: kernel code, and the DAG stops being the only witness of what depends on
    #: what. An operator that genuinely needs a value stays eager, which is the
    #: default, and being wrong about that costs performance and never
    #: correctness.
    lazy: bool = False
    #: Receive VALUES, but with the handles inside them left alone. For an
    #: operator that reaches into a container without caring what is in it:
    #: `index` wants element *i* of a sequence, and deep-resolving to get it
    #: would materialize all N elements to hand back one.
    #:
    #: `True` applies to every argument, which is right when the operator only
    #: ever reaches into containers -- it is harmless on an argument holding no
    #: handle, so `index`'s integer is unaffected. A tuple names the argument
    #: POSITIONS instead, for an operator that wants one argument untouched and
    #: the others resolved: `gather` pairs computed flags with element handles,
    #: so it declares `shallow=(1,)`.
    shallow: bool | tuple[int, ...] = False
    #: Evaluating this GROWS THE GRAPH: the engine expands it into new nodes and
    #: forwards their result, rather than calling the kernel. Orthogonal to the
    #: argument mode above -- `for_loop` rewrites and never sees a handle, while
    #: `default.sequence` is lazy and never rewrites.
    #:
    #: Note this is not "has no kernel". `for_loop` has one; it belongs to the
    #: strict runtime, which reconstructs a closure the engine never builds.
    #: Reaching it from the engine is the defect this flag exists to prevent.
    rewrite: bool = False
    #: HOW it rewrites, for operators whose rewrite is not a loop unroll.
    #: `(node, ctx) -> NodeId | None`: given the node and a context offering
    #: `resolve` (materialize one argument) and `node` (intern a new node),
    #: return the node whose value this one takes -- or None to decline, and be
    #: computed by the kernel after all. Declining is how an operator that can
    #: usually rewrite handles the shape it cannot. Called on
    #: the event loop, like the loop expander's own `_materialize` -- so the
    #: resolution is the engine's, not a kernel's.
    #:
    #: `rewrite=True` with no rewriter means the engine's loop machinery, which
    #: is too entangled with admission and chunking to be a plain callable.
    #: Anything simpler -- a conditional, a projection, a dispatch on a tag --
    #: writes one function here and needs no engine change at all.
    rewriter: Any = None
    type_rule: TypeRule | None = None

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
