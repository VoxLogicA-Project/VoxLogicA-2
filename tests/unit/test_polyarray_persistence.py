"""PolyArray must persist byte-identically to the sitk.Image it replaces.

Phase 0 (voxlogica/arrays.py) makes PolyArray the engine's uniform live-tier
value for images (see engine/executor.py, engine/node_table.py). The on-disk
voxpod/1 format must not change bit for bit, or existing warm caches (and any
run mixing old/new engine versions against the same store) would break.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.arrays import PolyArray
from voxlogica.engine.node_table import NodeTable
from voxlogica.lazy.ir import NodeSpec
from voxlogica.pod_codec import encode_for_storage
from voxlogica.storage import SQLiteResultsDatabase


def _image(shape=(3, 4, 5), value=9) -> sitk.Image:
    arr = np.full(shape, value, dtype=np.uint8)
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing((1.0, 2.0, 3.0))
    img.SetOrigin((0.5, 0.5, 0.5))
    img.SetDirection((1, 0, 0, 0, 1, 0, 0, 0, 1))
    return img


@pytest.mark.unit
def test_encoded_record_identical_for_polyarray_and_sitk_image() -> None:
    image = _image()
    poly = PolyArray.from_sitk(image)

    encoded_sitk = encode_for_storage(image)
    encoded_poly = encode_for_storage(poly)

    assert encoded_sitk.vox_type == encoded_poly.vox_type == "image"
    assert encoded_sitk.payload_json == encoded_poly.payload_json
    assert encoded_sitk.payload_bin == encoded_poly.payload_bin


@pytest.mark.unit
def test_node_table_load_wraps_reloaded_image_in_polyarray(tmp_path) -> None:
    """A value persisted from a PolyArray (or a raw sitk.Image) reloads as a
    PolyArray — the live tier is uniform regardless of how a value arrived."""
    backend = SQLiteResultsDatabase(db_path=str(tmp_path / "results.db"))
    table = NodeTable(backend=backend)
    try:
        table.nodes["n1"] = NodeSpec(kind="primitive", operator="test.image")
        table.begin("n1")
        image = _image(value=42)
        table.complete("n1", PolyArray.from_sitk(image), compute_ms=10.0, persist=True)
        table.flush()
        table.evict("n1")
        assert "n1" not in table.values

        loaded = table.load("n1")
        assert isinstance(loaded, PolyArray)
        assert np.array_equal(loaded.np(), sitk.GetArrayFromImage(image))
        assert loaded.geometry.spacing == pytest.approx(image.GetSpacing())
    finally:
        backend.close()


@pytest.mark.unit
def test_approx_bytes_accounts_polyarray_via_nbytes() -> None:
    from voxlogica.engine.persist import approx_bytes

    poly = PolyArray.from_sitk(_image(shape=(2, 3, 4)))
    assert approx_bytes(poly) == poly.nbytes == 2 * 3 * 4
