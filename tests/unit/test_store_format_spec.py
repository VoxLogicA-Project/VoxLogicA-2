from __future__ import annotations

import pytest

from voxlogica.pod_codec import encode_for_storage


@pytest.mark.unit
def test_scalar_envelope_matches_voxpod_v1_contract() -> None:
    encoded = encode_for_storage(42)
    assert encoded.format_version == "voxpod/1"
    assert encoded.vox_type == "integer"
    assert encoded.descriptor["vox_type"] == "integer"
    assert encoded.descriptor["format_version"] == "voxpod/1"
    assert isinstance(encoded.descriptor["summary"], dict)
    assert isinstance(encoded.descriptor["navigation"], dict)
    assert encoded.payload_json["encoding"] == "scalar-json-v1"
    assert encoded.payload_json["value"] == 42


@pytest.mark.unit
def test_ndarray_binary_payload_has_consistent_shape_and_size() -> None:
    np = pytest.importorskip("numpy")
    arr = np.arange(60, dtype=np.float32).reshape(3, 4, 5)
    encoded = encode_for_storage(arr)
    assert encoded.vox_type == "ndarray"
    assert encoded.payload_json["encoding"] == "ndarray-binary-v1"
    assert encoded.payload_json["shape"] == [3, 4, 5]
    assert encoded.payload_json["dtype"] == "float32"
    assert encoded.payload_bin is not None
    assert len(encoded.payload_bin) == arr.size * arr.dtype.itemsize

