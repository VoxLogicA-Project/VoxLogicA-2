"""Executor-level PolyArray adapter: the real seam, exercised with real kernels.

``Executor._compute`` (engine/executor.py) is the sole place PolyArray is
unwrapped for a kernel call and a kernel's sitk.Image result is wrapped back.
This drives it with actual vox1 kernels (not mocks) end to end: a PolyArray
input must reach the kernel as a plain sitk.Image, and a fresh sitk.Image
result must come back out of ``_compute`` as a PolyArray.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.arrays import PolyArray
from voxlogica.engine.executor import Executor
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeSpec
from voxlogica.primitives.registry import PrimitiveRegistry


def _bool_image(shape, value: bool) -> sitk.Image:
    arr = np.full(shape, 1 if value else 0, dtype=np.uint8)
    return sitk.GetImageFromArray(arr)


@pytest.fixture
def registry() -> PrimitiveRegistry:
    reg = PrimitiveRegistry()
    reg.import_namespace("vox1")
    return reg


@pytest.mark.unit
def test_compute_unwraps_polyarray_input_and_wraps_sitk_output(registry) -> None:
    """vox1.not on a PolyArray-wrapped input must: (a) hand the kernel a plain
    sitk.Image (existing kernels are untouched), (b) return a PolyArray."""
    table = NodeTable(backend=None)
    src = _bool_image((3, 4, 5), value=True)
    src_id = "src"
    table.nodes[src_id] = NodeSpec(kind="primitive", operator="constant")
    table.set_value(src_id, PolyArray.from_sitk(src))

    node_id = "not_node"
    table.nodes[node_id] = NodeSpec(kind="primitive", operator="vox1.not", args=(src_id,))

    executor = Executor(registry, max_workers=1)
    try:
        result = executor._compute(table, node_id)
    finally:
        executor.shutdown()

    assert isinstance(result, PolyArray), "a fresh sitk.Image kernel result must be wrapped"
    expected = sitk.GetArrayFromImage(sitk.Not(src))
    assert np.array_equal(result.np(), expected)


@pytest.mark.unit
def test_compute_chains_two_polyarray_producing_kernels(registry) -> None:
    """and(not(a), b) — the output of one wrapped kernel call feeds the next,
    exactly the shape a real plan produces (image -> image -> image)."""
    table = NodeTable(backend=None)
    a = _bool_image((2, 3, 4), value=True)
    b = _bool_image((2, 3, 4), value=True)
    table.nodes["a"] = NodeSpec(kind="primitive", operator="constant")
    table.set_value("a", PolyArray.from_sitk(a))
    table.nodes["b"] = NodeSpec(kind="primitive", operator="constant")
    table.set_value("b", PolyArray.from_sitk(b))
    table.nodes["not_a"] = NodeSpec(kind="primitive", operator="vox1.not", args=("a",))
    table.nodes["and_node"] = NodeSpec(kind="primitive", operator="vox1.and", args=("not_a", "b"))

    executor = Executor(registry, max_workers=1)
    try:
        not_a = executor._compute(table, "not_a")
        assert isinstance(not_a, PolyArray)
        table.set_value("not_a", not_a)
        result = executor._compute(table, "and_node")
    finally:
        executor.shutdown()

    assert isinstance(result, PolyArray)
    expected = sitk.GetArrayFromImage(sitk.And(sitk.Not(a), b))
    assert np.array_equal(result.np(), expected)
    # Not(True) & True == False everywhere.
    assert not expected.any()


@pytest.mark.unit
def test_compute_leaves_scalar_kernel_results_unwrapped(registry) -> None:
    """vox1.avg takes two PolyArray-wrapped image inputs (both correctly
    unwrapped to sitk for the kernel) but returns a plain float — the
    adapter must not wrap a non-image result."""
    table = NodeTable(backend=None)
    arr = np.array([[[1.0, 2.0], [3.0, 4.0]]], dtype=np.float32)
    image = sitk.GetImageFromArray(arr)
    mask = _bool_image(arr.shape, value=True)
    table.nodes["img"] = NodeSpec(kind="primitive", operator="constant")
    table.set_value("img", PolyArray.from_sitk(image))
    table.nodes["mask"] = NodeSpec(kind="primitive", operator="constant")
    table.set_value("mask", PolyArray.from_sitk(mask))
    table.nodes["avg_node"] = NodeSpec(kind="primitive", operator="vox1.avg", args=("img", "mask"))

    executor = Executor(registry, max_workers=1)
    try:
        result = executor._compute(table, "avg_node")
    finally:
        executor.shutdown()

    assert isinstance(result, float)
    assert result == pytest.approx(2.5)
