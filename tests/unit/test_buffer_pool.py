"""Exclusive buffer reuse across SimpleITK and NumPy/Numba representations."""

from __future__ import annotations

import threading

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.arrays import PolyArray, allocate_writable_like
from voxlogica.buffer_pool import (
    acquire_numpy,
    buffer_states,
    pool_stats,
    release_states,
    reset_pool_for_tests,
    retain_states,
)
from voxlogica.engine.node_table import NodeTable
from voxlogica.engine.persist import AsyncPersister


@pytest.fixture(autouse=True)
def _clean_pool(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOXLOGICA_BUFFER_POOL_MB", "64")
    monkeypatch.setenv("VOXLOGICA_BUFFER_POOL_PER_KEY", "4")
    reset_pool_for_tests()
    yield
    reset_pool_for_tests()


def test_numpy_buffer_reused_only_after_last_lease() -> None:
    first = acquire_numpy((4, 5, 6), np.float32)
    first.fill(17.0)
    states = retain_states(buffer_states(first))
    persistence_lease = retain_states(states)

    release_states(states)
    assert pool_stats()["returns"] == 0
    release_states(persistence_lease)
    assert pool_stats()["returns"] == 1

    second = acquire_numpy((4, 5, 6), np.float32)
    assert second is first
    assert pool_stats()["numpy_reuses"] == 1
    second.fill(0.0)


def test_sitk_buffer_reused_with_geometry_refreshed() -> None:
    reference = sitk.GetImageFromArray(np.zeros((3, 4, 5), dtype=np.uint8))
    reference.SetSpacing((0.7, 1.3, 2.1))
    first, first_view = allocate_writable_like(reference, sitk.sitkUInt8)
    first_view.fill(23)
    del first_view
    release_states(retain_states(buffer_states(first)))

    changed_geometry = sitk.Image(reference)
    changed_geometry.SetOrigin((4.0, 5.0, 6.0))
    second, second_view = allocate_writable_like(changed_geometry, sitk.sitkUInt8)

    assert second is first
    assert second.GetOrigin() == changed_geometry.GetOrigin()
    assert np.all(second_view == 23), "pooled contents are unspecified until overwritten"
    second_view.fill(0)
    assert pool_stats()["sitk_reuses"] == 1


def test_node_table_tracks_aliases_and_nested_images_before_reuse() -> None:
    reference = sitk.GetImageFromArray(np.zeros((3, 4), dtype=np.uint8))
    image, output = allocate_writable_like(reference, sitk.sitkUInt8)
    output.fill(1)
    wrapped = PolyArray.from_sitk(image)
    table = NodeTable(backend=None)

    table.set_value("image", wrapped)
    table.set_value("sequence", [image])
    table.evict("image")
    assert pool_stats()["returns"] == 0

    table.evict("sequence")
    assert pool_stats()["returns"] == 1


def test_pool_key_separates_shape_and_dtype() -> None:
    first = acquire_numpy((8, 8), np.uint8)
    release_states(retain_states(buffer_states(first)))

    assert acquire_numpy((8, 8), np.float32) is not first
    assert acquire_numpy((4, 16), np.uint8) is not first


def test_async_persistence_holds_lease_until_transfer_finishes() -> None:
    started = threading.Event()
    finish = threading.Event()

    class BlockingBackend:
        def has(self, _node_id):
            return False

        def put_success_batch(self, _entries):
            started.set()
            assert finish.wait(5.0)

    array = acquire_numpy((32, 32), np.float32)
    value = PolyArray.from_numpy(array)
    live_lease = retain_states(buffer_states(value))
    persister = AsyncPersister(BlockingBackend(), 64 * 1024 * 1024)
    try:
        persister.submit("node", value, {})
        release_states(live_lease)
        assert started.wait(5.0)
        assert pool_stats()["returns"] == 0

        finish.set()
        persister.flush(5.0)
        assert pool_stats()["returns"] == 1
    finally:
        finish.set()
        persister.close()
