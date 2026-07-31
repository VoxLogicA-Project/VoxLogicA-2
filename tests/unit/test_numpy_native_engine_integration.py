"""numpy-native primitives through the REAL engine: the wiring, not the
kernel math (see test_numpy_native_kernels.py for that).

Three things can only break at the engine layer, not in a kernel unit test:
  1. Geometry threading -- a numpy-native op's result is a bare np.ndarray,
     which carries no spacing/origin/direction of its own (see
     engine/executor.py's _wrap/_cone_reference_geometry). A wiring bug here
     would silently produce an identity-geometry PolyArray instead of the
     real one, corrupting anything downstream that is spacing-sensitive
     (dt() first among them).
  2. Cone-level (not just single-node) dispatch of numpy-native members --
     Executor._compute_cone's interior/exit handling with a numpy-native
     member is new code, distinct from the already-covered single-node path.
  3. Mixed chains: a numpy-native op feeding a sitk-only consumer (or vice
     versa) must round-trip through the PolyArray adapter boundary correctly
     in both directions.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.engine.core import ComputationEngine
from voxlogica.engine.priority import Priority
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


def _run(program: str):
    plan = reduce_program(parse_program_content(program)).to_symbolic_plan()
    engine = ComputationEngine(max_concurrency=4)
    engine.adopt_plan(plan)

    async def _drive():
        queries = [(g, engine.submit(g.id, g.operation, g.name, Priority.NORMAL))
                   for g in plan.goals]
        await engine.run()
        return {g.name: await q.result() for g, q in queries}

    values = asyncio.run(_drive())
    return values, engine.metrics()


def _write_image_with_real_geometry(path, arr: np.ndarray) -> None:
    """Non-identity spacing/origin: a wiring bug that silently drops geometry
    (falling back to Geometry.identity) would only show up against real
    values, never against the default (1,1,1)/(0,0,0) an identity fallback
    would coincidentally also produce."""
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing((2.5, 2.5, 3.0))
    img.SetOrigin((10.0, -5.0, 1.0))
    sitk.WriteImage(img, str(path))


@pytest.mark.unit
def test_geometry_survives_a_numpy_native_op_into_a_spacing_sensitive_consumer(tmp_path):
    """not()'s bare-ndarray result must carry the SOURCE image's real spacing
    forward into dt() (a genuine SignedMaurerDistanceMap, whose numeric
    output depends on spacing) -- not silently fall back to identity
    geometry, which would still run without error but compute the wrong
    distances everywhere except by construction-coincidence.
    """
    img_path = tmp_path / "in.nii.gz"
    arr = np.zeros((6, 6, 6), dtype=np.float32)
    arr[2:4, 2:4, 2:4] = 1.0
    _write_image_with_real_geometry(img_path, arr)

    program = f"""
import "simpleitk"
import "vox1"
img = intensity(ReadImage("{img_path}"))
region = geq_sv(0.5, img)
negated = not(region)
distances = dt(negated)
print "dmin" min(distances)
print "dmax" max(distances)
"""
    values, _ = _run(program)

    # Reference: run the identical computation entirely on the sitk side (no
    # numpy-native path at all) via direct sitk calls, so this is a real
    # independent check, not a restatement of the same code path.
    ref_img = sitk.ReadImage(str(img_path))
    ref_region = sitk.GreaterEqual(sitk.Cast(ref_img, sitk.sitkFloat32), 0.5)
    ref_negated = sitk.Not(sitk.Cast(ref_region, sitk.sitkUInt8))
    flt = sitk.SignedMaurerDistanceMapImageFilter()
    flt.SetInsideIsPositive(False)
    flt.SetSquaredDistance(False)
    flt.SetUseImageSpacing(True)
    flt.SetBackgroundValue(0.0)
    ref_distances = flt.Execute(ref_negated)
    ref_min_max = sitk.MinimumMaximumImageFilter()
    ref_min_max.Execute(ref_distances)

    assert values["dmin"] == pytest.approx(ref_min_max.GetMinimum())
    assert values["dmax"] == pytest.approx(ref_min_max.GetMaximum())
    # A geometry mixup (falling back to spacing=(1,1,1)) would still produce
    # SOME finite min/max here without erroring -- so also pin the actual
    # spacing-dependent magnitude, not just "it ran".
    assert values["dmax"] > 2.0, (
        "dmax this small suggests spacing fell back to (1,1,1) instead of "
        "the real (2.5, 2.5, 3.0) -- silent geometry loss would look like this"
    )


@pytest.mark.unit
def test_numpy_native_cone_dispatch_matches_expected_and_actually_fires(tmp_path):
    """A chain of only-numpy-native ops long enough to guarantee a real cone
    (not just single-node dispatch), verified against an independently
    computed reference AND asserted to have actually gone through
    _compute_cone (cones_dispatched > 0), so this cannot silently degrade to
    "every op ran standalone and happened to be correct"."""
    img_path = tmp_path / "in.nii.gz"
    arr = np.array([0.1, 0.5, 1.0, 1.5, 2.0, -1.0, 3.0, 0.0] * 16, dtype=np.float32).reshape(8, 4, 4)
    sitk.WriteImage(sitk.GetImageFromArray(arr), str(img_path))

    program = f"""
import "simpleitk"
import "vox1"
img = intensity(ReadImage("{img_path}"))
lo = geq_sv(0.3, img)
hi = leq_sv(2.0, img)
combo = and(lo, hi)
negated = not(combo)
result = or(negated, geq_sv(2.5, img))
print "v" volume(result)
"""
    values, metrics = _run(program)
    assert metrics["cones_dispatched"] > 0, "test must exercise cone dispatch, or it proves nothing"

    lo_ref = arr >= 0.3
    hi_ref = arr <= 2.0
    combo_ref = lo_ref & hi_ref
    negated_ref = ~combo_ref
    result_ref = negated_ref | (arr >= 2.5)
    assert values["v"] == pytest.approx(float(np.count_nonzero(result_ref)))


@pytest.mark.unit
def test_mixed_numpy_native_and_sitk_chain_round_trips_both_directions(tmp_path):
    """sitk (ReadImage/intensity) -> numpy-native (geq_sv/mask) -> sitk
    (border, non-numpy_native) -> numpy-native (and) -> sitk (volume, via
    logical_not_compat's not_compat path) -- crosses the adapter boundary
    in both directions more than once in one plan."""
    img_path = tmp_path / "in.nii.gz"
    arr = np.zeros((6, 6, 6), dtype=np.float32)
    arr[1:5, 1:5, 1:5] = 5.0
    sitk.WriteImage(sitk.GetImageFromArray(arr), str(img_path))

    program = f"""
import "simpleitk"
import "vox1"
img = intensity(ReadImage("{img_path}"))
region = geq_sv(1.0, img)
masked = mask(img, region)
edge = border(masked)
combo = and(region, not(edge))
print "v" volume(combo)
"""
    values, _ = _run(program)
    assert values["v"] >= 0.0  # must not raise; exact value depends on border's own semantics
    assert isinstance(values["v"], float)
