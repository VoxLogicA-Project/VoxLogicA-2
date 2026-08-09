"""Stage B: numba-compiled cone execution (schedule-time fusion, §3.2b).

Stage A (``engine/fusion.py``, ``Executor.run_cone``) batches DISPATCH: every
cone member still runs its own real sitk kernel, one Python call each, just
without a separate scheduling round trip per member. Stage B goes further —
it collapses the WHOLE cone into one compiled, per-voxel loop (one pass over
memory, zero intermediate array allocations for elided members, zero
per-member Python/C++ kernel-call overhead) — using each member's
``ElementwiseSpec.expr`` fragment, which is already validated bit-identical
against its real kernel (see the bit-identical property tests in
tests/unit/test_numba_fusion.py).

THE SHAPE KEY (``ConeShape``) is a structural descriptor — which ops, in
which order, wired to which argument slots — deliberately NOT keyed by
dtype: numba's own ``@njit`` already specializes per call-signature
internally, so encoding dtype in our own key would only duplicate a cache
numba already maintains. Two cones with the same op sequence and wiring share
ONE compiled
function regardless of which concrete node ids or scalar values feed them —
this is what lets a runtime loop's thousands of structurally-identical
per-element cones (same op chain, different threshold constant, different
node ids) all warm exactly one compile.

COMPILATION happens off the pop path: ``NumbaFusionBackend.try_get`` is a
non-blocking dict lookup; a first-seen shape kicks off a background compile
(a dedicated one-thread pool — numba compilation is itself GIL-bound pure
Python/LLVM work) and returns None immediately, so THAT cone runs Stage A
this time and every time until the shape is READY. Once compiled, it serves
every future cone of that shape with no further compile cost (barring
numba's own per-new-dtype specialization, a one-time cost on whichever pool
thread first calls with that dtype — never the event loop).

GEOMETRY: a numba kernel's outputs are plain numpy arrays with no attached
geometry, unlike a real sitk kernel's output. This module inherits it from
the cone's array inputs — safe only because Stage B additionally requires
ALL of a cone's array inputs to share not just shape (already required by
``FusionPlanner``'s shape guard, §3.1) but IDENTICAL geometry too
(spacing/origin/direction can differ at equal voxel counts, e.g. two
differently-resampled images that happen to tile the same grid size).
``shape_of`` refuses (returns None, falling back to Stage A) if that
stronger check fails — Stage B is a pure optimization of an already-correct
path, never a way to introduce new behavior when uncertain.

NO CROSS-PROCESS DISK CACHE: ``@njit`` here always runs with ``cache=False``.
numba's own ``cache=True`` needs to reconstruct a function's "environment"
(its globals) on a cache hit, which means re-importing the module the
function was defined in — but a codegen'd kernel's module is a synthetic,
``exec``'d namespace with no real ``__name__``, so that reconstruction fails
(confirmed empirically: ``importlib.import_module(None)``). The in-process
``NumbaFusionBackend._compiled`` dict already delivers the benefit that
matters at runtime — one compile per shape, reused by every future cone of
that shape for the rest of THIS run — so paying LLVM compilation once per
process is an acceptable, correctness-preserving trade against a disk-cache
path this codebase can't make reliable for generated code.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from voxlogica.arrays import Geometry, PolyArray
from voxlogica.lazy.ir import NodeId

# numba's cache=True needs a real, on-disk source file to locate a cache
# directory next to (InTreeCacheLocator) or keyed off (UserWideCacheLocator) —
# a placeholder filename like "<voxlogica-cone-kernel>" has no locator at all
# and raises RuntimeError. Generated sources are content-addressed here so
# structurally-identical shapes (the common case — see module docstring)
# reuse the same file instead of growing this directory per compile.
_SOURCE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "voxlogica-numba-cones")

if TYPE_CHECKING:
    from voxlogica.engine.fusion import Cone
    from voxlogica.engine.node_table import NodeTable
    from voxlogica.primitives.registry import PrimitiveRegistry

_sitk = None


def _simpleitk():
    global _sitk
    if _sitk is None:
        import SimpleITK
        _sitk = SimpleITK
    return _sitk


@dataclass(frozen=True)
class ArgRef:
    """One positional argument's origin within a cone.

    ``kind`` is one of "member" (another cone member's computed value,
    ``index`` = its position in the cone's topological order), "array_input"
    (an external array-shaped dependency, ``index`` = its position among
    this cone's distinct array inputs, in first-seen order), or
    "scalar_input" (an external scalar dependency, same first-seen
    indexing, independent of the array-input numbering).
    """

    kind: str
    index: int


@dataclass(frozen=True)
class ConeShape:
    """Canonical, hashable structural descriptor of a cone.

    Deliberately excludes dtype (numba specializes per call signature on its
    own) and excludes every specific node id (two structurally-identical
    cones from different loop elements must hash equal and share one
    compile). ``out_positions`` are member indices (into ``ops``, i.e. topo
    order) whose value must be written to an output array — exactly
    ``cone.exits``, in topo order.
    """

    ops: tuple[str, ...]
    arg_refs: tuple[tuple[ArgRef, ...], ...]
    array_input_count: int
    scalar_input_count: int
    out_positions: tuple[int, ...]
    #: True when some member is a stencil, which changes the CALLING
    #: CONVENTION, not just the loop body: arrays come in as 3D ``(z, y, x)``
    #: views instead of pre-flattened 1D ones, because neighbour reads need
    #: real axes. It is therefore part of the cache key even though it is
    #: derivable from ``ops`` — two shapes that disagree here cannot share a
    #: compiled function.
    spatial: bool = False


@dataclass(frozen=True)
class ShapeBinding:
    """A planned cone's shape plus which concrete node ids fill its slots."""

    shape: ConeShape
    array_input_ids: tuple[NodeId, ...]
    scalar_input_ids: tuple[NodeId, ...]
    exit_ids_topo: tuple[NodeId, ...]  # cone.exits, in topo order — matches shape.out_positions


def _dtype_and_geometry(value: Any) -> tuple[Any, Geometry] | None:
    """A value's (numpy dtype, Geometry) if it is array-shaped; else None."""
    if isinstance(value, PolyArray):
        return value.np().dtype, value.geometry
    sitk = _simpleitk()
    if sitk is not None and isinstance(value, sitk.Image):
        return None  # unwrapped raw sitk shouldn't reach here; see executor._unwrap contract
    return None


def shape_of(cone: "Cone", table: "NodeTable", registry: "PrimitiveRegistry") -> ShapeBinding | None:
    """Derive a cone's Stage-B shape binding, or None if it can't be Stage-B'd.

    Refuses (falls back to Stage A, never wrong, just not optimized) when:
    a member's op has no ``ElementwiseSpec`` (shouldn't happen — the planner
    only admits elementwise members — but this stays a soft check, not an
    assertion, since a shape mismatch here must never crash a run); an
    external dependency is neither a plain number nor array-shaped; or more
    than one distinct array input's geometry disagrees (see module
    docstring — Stage B is not allowed to guess which geometry is "right").
    """
    member_index = {m: i for i, m in enumerate(cone.members_topo)}
    array_input_index: dict[NodeId, int] = {}
    scalar_input_index: dict[NodeId, int] = {}
    ops: list[str] = []
    arg_refs: list[tuple[ArgRef, ...]] = []
    reference_geometry: Geometry | None = None

    for m in cone.members_topo:
        node = table.nodes[m]
        try:
            primitive = registry.get_spec(node.operator)
        except KeyError:
            return None
        spec = primitive.elementwise
        stencil = primitive.stencil
        if spec is None and stencil is None:
            return None
        if stencil is not None:
            # The planner only ever seeds a cone with a stencil, never grows
            # into one (see engine/fusion.py) -- but shape_of must not TRUST
            # that, because admitting a stencil anywhere else silently emits
            # neighbour reads of an array that does not exist. Re-check here.
            if member_index[m] != 0:
                return None
            if stencil.radius != 1:
                # The fused form is a direct box reduction, not the separable
                # three-pass one; that is only the better trade while the box
                # stays inside cache. At radius 2 it is already 125 reads per
                # voxel against 15 for three separable passes.
                return None
            if len(node.args) != 1:
                return None
        ops.append(node.operator)
        refs: list[ArgRef] = []
        for arg_id in node.args:
            if arg_id in member_index and member_index[arg_id] < member_index[m]:
                refs.append(ArgRef("member", member_index[arg_id]))
                continue
            value = table.values.get(arg_id)
            if value is None:
                return None  # not resident — caller is expected to rematerialize cone.inputs first
            if isinstance(value, bool):
                return None  # bool is an int subclass; never a genuine scalar operand here
            if isinstance(value, (int, float)):
                if stencil is not None:
                    return None  # a stencil's operand is a volume, never a scalar
                idx = scalar_input_index.setdefault(arg_id, len(scalar_input_index))
                refs.append(ArgRef("scalar_input", idx))
                continue
            info = _dtype_and_geometry(value)
            if info is None:
                return None
            _dtype, geometry = info
            if geometry.components != 1:
                # Vector image: the real sitk comparison kernels REJECT these
                # (the run fails under Stage A), while a flat per-voxel loop
                # would silently "succeed" per-component. Bit-identical means
                # identical failures too — refuse, let Stage A raise.
                return None
            if stencil is not None:
                # Outside its declared dtypes the real kernel CASTS before it
                # dilates, and a truncating cast is not a per-voxel function of
                # the original array (float 256.0 becomes 0). Refuse rather
                # than approximate.
                if _dtype.name not in stencil.input_dtypes:
                    return None
                if len(geometry.spacing) != 3:
                    return None  # the spatial nest is written for volumes
            if reference_geometry is None:
                reference_geometry = geometry
            elif geometry != reference_geometry:
                return None  # ambiguous geometry — Stage A already handles this correctly
            idx = array_input_index.setdefault(arg_id, len(array_input_index))
            refs.append(ArgRef("array_input", idx))
        arg_refs.append(tuple(refs))

    if not array_input_index:
        return None  # a cone with no array input at all isn't the case Stage B targets

    exit_ids_topo = tuple(m for m in cone.members_topo if m in cone.exits)
    out_positions = tuple(member_index[e] for e in exit_ids_topo)

    shape = ConeShape(
        ops=tuple(ops),
        arg_refs=tuple(arg_refs),
        array_input_count=len(array_input_index),
        scalar_input_count=len(scalar_input_index),
        out_positions=out_positions,
        spatial=any(registry.get_spec(op).stencil is not None for op in ops),
    )
    array_input_ids = tuple(sorted(array_input_index, key=array_input_index.get))
    scalar_input_ids = tuple(sorted(scalar_input_index, key=scalar_input_index.get))
    return ShapeBinding(shape, array_input_ids, scalar_input_ids, exit_ids_topo)


def _expr_for(registry: "PrimitiveRegistry", operator: str) -> str:
    return registry.get_spec(operator).elementwise.expr


def _stencil_for(registry: "PrimitiveRegistry", operator: str) -> Any | None:
    try:
        return registry.get_spec(operator).stencil
    except KeyError:
        return None


_ARG_DTYPE_RE = re.compile(r"^arg(\d+)$")


def resolve_out_dtype(shape: ConeShape, member_idx: int,
                       arrays: list["np.ndarray"], scalars: list[float],
                       registry: "PrimitiveRegistry") -> "np.dtype":
    """The concrete output dtype for ``shape.ops[member_idx]``, for THIS dispatch.

    Most specs declare a literal dtype (safe: their result type never depends
    on the operand's) — return it directly. A few (``mask``: see
    ``ElementwiseSpec.out_dtype``'s docstring) declare ``"argN"``, meaning
    "whatever positional argument N's runtime dtype is". N can itself be a
    fused predecessor within the SAME cone (an ``ArgRef("member", ...)``), so
    this recurses through the cone's own arg_refs rather than only handling
    the leaf case — the recursion terminates because cone members are a DAG in
    topological order, so a "member" ref always points strictly earlier.
    """
    spec = registry.get_spec(shape.ops[member_idx])
    # A stencil member never declares "argN": its output type is fixed by the
    # morphological op's own ITK semantics, not by what flowed in.
    out_dtype = spec.elementwise.out_dtype if spec.elementwise is not None else spec.stencil.out_dtype
    m = _ARG_DTYPE_RE.match(out_dtype)
    if m is None:
        return np.dtype(out_dtype)

    ref = shape.arg_refs[member_idx][int(m.group(1))]
    if ref.kind == "array_input":
        return arrays[ref.index].dtype
    if ref.kind == "scalar_input":
        return np.dtype(np.float64)  # scalars are always widened to float(), see _compute_cone_numba
    return resolve_out_dtype(shape, ref.index, arrays, scalars, registry)


def _generate_source(shape: ConeShape, registry: "PrimitiveRegistry") -> str:
    """Build the Python source for one per-voxel loop over a cone.

    Two loop shapes, chosen by ``shape.spatial``:

    Flat (the common case, no stencil members). All array inputs and outputs
    are passed as pre-flattened 1D views (the caller reshapes to/from the
    original shape) — this keeps the generated loop nest ndim-agnostic: a 2D,
    3D, or vector-image cone all compile to the exact same *shape of code*,
    differing only in the runtime array sizes numba specializes on.

    Spatial (some member is a stencil). Arrays arrive as 3D ``(z, y, x)``
    views and the nest is explicit, because a neighbour read needs real axes.
    This is strictly less general than the flat path — hence keeping both,
    rather than making everything spatial: the flat form is what the vast
    majority of cones are, it is already tuned, and it works at any ndim.
    """
    if shape.spatial:
        return _generate_spatial_source(shape, registry)
    return _generate_flat_source(shape, registry)


def _generate_flat_source(shape: ConeShape, registry: "PrimitiveRegistry") -> str:
    array_params = [f"arr{i}" for i in range(shape.array_input_count)]
    scalar_params = [f"scalar{i}" for i in range(shape.scalar_input_count)]
    out_params = [f"out{i}" for i in range(len(shape.out_positions))]
    params = array_params + scalar_params + out_params

    lines = [f"def _cone_kernel({', '.join(params)}):"]
    lines.append(f"    n = {array_params[0]}.shape[0]")
    lines.append("    for _i in range(n):")

    out_slot_of_member = {member_idx: slot for slot, member_idx in enumerate(shape.out_positions)}
    for member_idx, (op, refs) in enumerate(zip(shape.ops, shape.arg_refs)):
        placeholders = []
        for ref in refs:
            if ref.kind == "member":
                placeholders.append(f"m{ref.index}")
            elif ref.kind == "array_input":
                placeholders.append(f"arr{ref.index}[_i]")
            else:
                placeholders.append(f"scalar{ref.index}")
        expr = _expr_for(registry, op).format(*placeholders)
        lines.append(f"        m{member_idx} = {expr}")
        if member_idx in out_slot_of_member:
            lines.append(f"        out{out_slot_of_member[member_idx]}[_i] = m{member_idx}")

    return "\n".join(lines) + "\n"


def _generate_spatial_source(shape: ConeShape, registry: "PrimitiveRegistry") -> str:
    """The 3D-indexed variant, for cones containing a stencil member.

    The stencil's reduction is emitted as a fully unrolled sequence of clamped
    reads rather than a triple ``range(-r, r+1)`` loop: at radius 1 that is 27
    reads whose clamped indices are loop-invariant in x for two of the three
    axes, and unrolling lets numba hoist and vectorize them. It also keeps the
    generated source readable, which matters more here than usual — a
    mis-generated stencil is a silent wrong-answer bug, and
    ``_write_source_for_debugging`` dumps this to a real file precisely so it
    can be read back and checked by eye.

    Note this is a DIRECT box reduction, not the separable three-pass form the
    standalone kernel uses. Separability needs whole-array intermediates,
    which is exactly the round trip fusion exists to delete; inside a cone the
    27 reads are already resident in cache. That trade only holds at small
    radius, which is why ``shape_of`` refuses radius > 1.
    """
    array_params = [f"arr{i}" for i in range(shape.array_input_count)]
    scalar_params = [f"scalar{i}" for i in range(shape.scalar_input_count)]
    out_params = [f"out{i}" for i in range(len(shape.out_positions))]
    params = array_params + scalar_params + out_params

    lines = [f"def _cone_kernel({', '.join(params)}):"]
    lines.append(f"    nz, ny, nx = {array_params[0]}.shape")

    # Clamped neighbour indices along z and y are invariant in the inner loop,
    # so they are bound once per z / per y rather than recomputed for every
    # voxel. That is 18 of the 27 index computations lifted out of the hot
    # loop at radius 1. (LLVM can often hoist these itself, but only when it
    # can prove the min/max is loop-invariant through the array indexing;
    # emitting them already hoisted costs nothing and does not rely on it.)
    radii = sorted({s.radius for s in (_stencil_for(registry, op) for op in shape.ops)
                    if s is not None})
    lines.append("    for _z in range(nz):")
    for r in radii:
        for d in range(-r, r + 1):
            if d != 0:
                lines.append(f"        _z{_offset_name(d)} = {_clamped(d, '_z', 'nz')}")
    lines.append("        for _y in range(ny):")
    for r in radii:
        for d in range(-r, r + 1):
            if d != 0:
                lines.append(f"            _y{_offset_name(d)} = {_clamped(d, '_y', 'ny')}")
    lines.append("            for _x in range(nx):")
    body = " " * 16

    out_slot_of_member = {member_idx: slot for slot, member_idx in enumerate(shape.out_positions)}
    for member_idx, (op, refs) in enumerate(zip(shape.ops, shape.arg_refs)):
        stencil = _stencil_for(registry, op)
        if stencil is not None:
            src = f"arr{refs[0].index}"  # shape_of guarantees a single array_input arg
            radius = stencil.radius
            better = ">" if stencil.reduce == "max" else "<"
            centre = stencil.neighbour_expr.format(f"{src}[_z, _y, _x]")
            lines.append(f"{body}_acc{member_idx} = {centre}")
            for dz in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        if dz == 0 and dy == 0 and dx == 0:
                            continue
                        zz = "_z" if dz == 0 else f"_z{_offset_name(dz)}"
                        yy = "_y" if dy == 0 else f"_y{_offset_name(dy)}"
                        xx = _clamped(dx, "_x", "nx")
                        value = stencil.neighbour_expr.format(f"{src}[{zz}, {yy}, {xx}]")
                        lines.append(f"{body}_v = {value}")
                        lines.append(f"{body}if _v {better} _acc{member_idx}:")
                        lines.append(f"{body}    _acc{member_idx} = _v")
            result = stencil.result_expr.format(f"_acc{member_idx}", f"{src}[_z, _y, _x]")
            lines.append(f"{body}m{member_idx} = {result}")
        else:
            placeholders = []
            for ref in refs:
                if ref.kind == "member":
                    placeholders.append(f"m{ref.index}")
                elif ref.kind == "array_input":
                    placeholders.append(f"arr{ref.index}[_z, _y, _x]")
                else:
                    placeholders.append(f"scalar{ref.index}")
            lines.append(f"{body}m{member_idx} = {_expr_for(registry, op).format(*placeholders)}")
        if member_idx in out_slot_of_member:
            slot = out_slot_of_member[member_idx]
            lines.append(f"{body}out{slot}[_z, _y, _x] = m{member_idx}")

    return "\n".join(lines) + "\n"


def _offset_name(delta: int) -> str:
    """A python-identifier-safe suffix for a signed neighbour offset."""
    return f"m{-delta}" if delta < 0 else f"p{delta}"


def _clamped(delta: int, var: str, extent: str) -> str:
    """A neighbour index expression that replicates the edge voxel.

    Clamping, not wrapping and not skipping: it is what
    ``sitk.BinaryDilate``'s boundary handling reduces to for a box structuring
    element, and it matches the standalone kernel exactly (see
    ``kernels._dilate_box3_separable``). Emitted as ``min``/``max`` rather
    than a branch so numba can keep the inner loop branch-free.
    """
    if delta == 0:
        return var
    if delta < 0:
        return f"max({var} - {-delta}, 0)"
    return f"min({var} + {delta}, {extent} - 1)"


def _write_source_for_debugging(source: str) -> str:
    """Persist ``source`` to a real, content-addressed file and return its
    path. Not required for compilation (``cache=False`` — see module
    docstring) — purely so a traceback out of a buggy generated kernel points
    at real, readable source instead of a synthetic ``<string>`` filename."""
    os.makedirs(_SOURCE_CACHE_DIR, exist_ok=True)
    digest = hashlib.sha256(source.encode()).hexdigest()[:32]
    path = os.path.join(_SOURCE_CACHE_DIR, f"cone_{digest}.py")
    if not os.path.exists(path):
        tmp_path = f"{path}.{os.getpid()}.tmp"
        with open(tmp_path, "w") as f:
            f.write(source)
        os.replace(tmp_path, path)  # atomic: concurrent writers never see a partial file
    return path


def compile_shape(shape: ConeShape, registry: "PrimitiveRegistry") -> Callable:
    """Generate and numba-compile one cone shape's flat-loop kernel."""
    import numba

    source = _generate_source(shape, registry)
    path = _write_source_for_debugging(source)
    namespace: dict[str, Any] = {}
    exec(compile(source, path, "exec"), namespace)  # noqa: S102
    raw_fn = namespace["_cone_kernel"]
    return numba.njit(nogil=True, cache=False)(raw_fn)


#: Below this many fused members, Stage B is a measured net LOSS, not just a
#: wash: PolyArray.from_numpy's output has no "sitk" view, so the very next
#: consumer that isn't itself elementwise (a sequence assembly step, a legacy
#: kernel, a goal print) pays a full numpy->sitk copy (Executor._unwrap ->
#: PolyArray.sitk(), see arrays.py) that Stage A's output never pays (its
#: PolyArray is built FROM the real kernel's sitk.Image, "sitk" already
#: cached). That fixed conversion cost only gets amortized once the compiled
#: loop is doing enough work to be worth it. Measured on a 128^3 float32
#: image, straight-line leq_sv+geq_sv+and+not*N chain (bench_numba_fusion.py,
#: cone size = 1 + N since the two comparisons complete as independent
#: dispatches before "and" is ready — see FusionPlanner): cone size 5.2 ->
#: Stage B 0.60x (SLOWER); 9.2 -> 0.90-1.17x (noisy, still marginal); 11.1 ->
#: 1.35x; 21.2 -> 1.92-2.23x. 12 sits solidly past the noisy boundary, in the
#: region where every measured run won.
_MIN_MEMBERS_FOR_STAGE_B = 12


class NumbaFusionBackend:
    """Shape-keyed compile cache with a background, non-blocking compile path."""

    def __init__(self, registry: "PrimitiveRegistry", min_members: int = _MIN_MEMBERS_FOR_STAGE_B):
        self.registry = registry
        self.min_members = min_members
        self._compiled: dict[ConeShape, Callable] = {}
        self._compiling: set[ConeShape] = set()
        self._lock = threading.Lock()  # touched from the event loop AND the compile-pool thread
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxlogica-numba-compile")
        self.compiles_started = 0
        self.compiles_finished = 0
        self.compiles_failed = 0

    def try_get(self, shape: ConeShape) -> Callable | None:
        """READY -> the compiled callable. Otherwise kicks off a background
        compile (once per shape) and returns None — the caller falls back
        to Stage A for this dispatch, and every dispatch of this shape until
        it becomes READY.

        A shape too small to be worth compiling (see ``_MIN_MEMBERS_FOR_STAGE_B``)
        is never even submitted for compilation — it stays on Stage A forever,
        by design, not because it failed.
        """
        if len(shape.ops) < self.min_members:
            return None
        with self._lock:
            fn = self._compiled.get(shape)
            if fn is not None:
                return fn
            if shape in self._compiling:
                return None
            self._compiling.add(shape)
            self.compiles_started += 1
        self._pool.submit(self._compile_and_store, shape)
        return None

    def _compile_and_store(self, shape: ConeShape) -> None:
        try:
            fn = compile_shape(shape, self.registry)
            with self._lock:
                self._compiled[shape] = fn
                self.compiles_finished += 1
        except Exception:  # noqa: BLE001
            # A shape that fails to compile (e.g. an unsupported numba
            # construct) must never take the run down with it — it simply
            # never becomes READY, so every cone of this shape keeps running
            # Stage A forever. Silently degrading to a slower-but-correct
            # path beats crashing a run over an optimization.
            with self._lock:
                self.compiles_failed += 1
        finally:
            with self._lock:
                self._compiling.discard(shape)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)
