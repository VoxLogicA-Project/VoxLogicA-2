"""engine/numba_fusion.py::resolve_out_dtype -- dynamic ('argN') output dtype
resolution for ElementwiseSpec entries whose result type tracks an operand's
(currently just `mask`; see ElementwiseSpec.out_dtype's docstring for why a
single fixed string is wrong for it).

Direct/constructed-ConeShape tests, not through the full engine: cone
FORMATION is scheduler-dependent (see test_numba_fusion.py's _NOT_DEPTH
comment on why cone membership can vary run to run under concurrency), so
whether a hand-written program actually nests two `mask` calls in the SAME
cone -- the case that exercises the recursive "member" branch below -- is not
guaranteed by construction. Building the ConeShape directly makes the
recursive path deterministically testable instead of hoping the scheduler
cooperates.
"""

from __future__ import annotations

import numpy as np
import pytest

from voxlogica.engine.numba_fusion import ArgRef, ConeShape, resolve_out_dtype
from voxlogica.primitives.registry import PrimitiveRegistry


@pytest.fixture(scope="module")
def registry() -> PrimitiveRegistry:
    return PrimitiveRegistry()


@pytest.mark.unit
def test_literal_out_dtype_is_returned_directly(registry):
    # "vox1.and" declares a plain literal out_dtype ("uint8"), no argN.
    shape = ConeShape(
        ops=("vox1.and",),
        arg_refs=((ArgRef("array_input", 0), ArgRef("array_input", 1)),),
        array_input_count=2, scalar_input_count=0, out_positions=(0,),
    )
    arrays = [np.zeros(4, dtype=np.uint8), np.zeros(4, dtype=np.uint8)]
    assert resolve_out_dtype(shape, 0, arrays, [], registry) == np.dtype("uint8")


@pytest.mark.unit
def test_arg0_resolves_to_the_bound_array_inputs_dtype(registry):
    """The real usage this was built for: pdt(x) = mask(dt(x), dt(x) > 0) --
    mask's arg0 (the image) is dt(x), a float32 array_input external to the
    cone."""
    shape = ConeShape(
        ops=("vox1.mask",),
        arg_refs=((ArgRef("array_input", 0), ArgRef("array_input", 1)),),
        array_input_count=2, scalar_input_count=0, out_positions=(0,),
    )
    arrays = [np.zeros(4, dtype=np.float32), np.zeros(4, dtype=np.uint8)]
    assert resolve_out_dtype(shape, 0, arrays, [], registry) == np.dtype("float32")


@pytest.mark.unit
def test_arg0_resolves_through_a_different_array_dtype(registry):
    """Same shape, different bound dtype: proves the resolution is genuinely
    per-dispatch, not baked into anything cached at compile time (ConeShape
    deliberately excludes dtype -- see its own docstring)."""
    shape = ConeShape(
        ops=("vox1.mask",),
        arg_refs=((ArgRef("array_input", 0), ArgRef("array_input", 1)),),
        array_input_count=2, scalar_input_count=0, out_positions=(0,),
    )
    arrays = [np.zeros(4, dtype=np.uint8), np.zeros(4, dtype=np.uint8)]
    assert resolve_out_dtype(shape, 0, arrays, [], registry) == np.dtype("uint8")


@pytest.mark.unit
def test_scalar_arg_widens_to_float64(registry):
    shape = ConeShape(
        ops=("vox1.mask",),
        arg_refs=((ArgRef("scalar_input", 0), ArgRef("array_input", 0)),),
        array_input_count=1, scalar_input_count=1, out_positions=(0,),
    )
    arrays = [np.zeros(4, dtype=np.uint8)]
    scalars = [1.0]  # matches _compute_cone_numba: scalars are always float()
    assert resolve_out_dtype(shape, 0, arrays, scalars, registry) == np.dtype("float64")


@pytest.mark.unit
def test_member_ref_recurses_through_a_nested_mask(registry):
    """mask(mask(dtimg, cond1), cond2): the outer mask's arg0 is the INNER
    mask's result, a "member" ref within the same cone, not a leaf. This is
    the recursive branch resolve_out_dtype exists for -- must resolve all the
    way back to dtimg's actual array dtype, not stop at the intermediate
    "argN" spec."""
    shape = ConeShape(
        ops=("vox1.mask", "vox1.mask"),
        arg_refs=(
            (ArgRef("array_input", 0), ArgRef("array_input", 1)),   # inner: mask(dtimg, cond1)
            (ArgRef("member", 0), ArgRef("array_input", 2)),         # outer: mask(inner, cond2)
        ),
        array_input_count=3, scalar_input_count=0, out_positions=(1,),
    )
    arrays = [
        np.zeros(4, dtype=np.float32),  # dtimg
        np.zeros(4, dtype=np.uint8),    # cond1
        np.zeros(4, dtype=np.uint8),    # cond2
    ]
    assert resolve_out_dtype(shape, 1, arrays, [], registry) == np.dtype("float32")


@pytest.mark.unit
def test_member_ref_recurses_through_three_levels(registry):
    """Guards against an off-by-one that only happens to work for depth 1:
    inner (leaf) -> middle ("member" of inner) -> outer ("member" of middle)."""
    shape = ConeShape(
        ops=("vox1.mask", "vox1.mask", "vox1.mask"),
        arg_refs=(
            (ArgRef("array_input", 0), ArgRef("array_input", 1)),
            (ArgRef("member", 0), ArgRef("array_input", 2)),
            (ArgRef("member", 1), ArgRef("array_input", 3)),
        ),
        array_input_count=4, scalar_input_count=0, out_positions=(2,),
    )
    arrays = [np.zeros(4, dtype=np.float32)] + [np.zeros(4, dtype=np.uint8)] * 3
    assert resolve_out_dtype(shape, 2, arrays, [], registry) == np.dtype("float32")
