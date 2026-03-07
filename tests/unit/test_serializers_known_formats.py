from __future__ import annotations

import pytest

from voxlogica.pod_codec import decode_runtime_value, encode_for_storage
from voxlogica.value_model import OverlayValue


@pytest.mark.unit
def test_bytes_roundtrip_binary_codec() -> None:
    payload = b"\x00\x01voxpod\x02"
    encoded = encode_for_storage(payload)
    decoded = decode_runtime_value(encoded.vox_type, encoded.payload_json, encoded.payload_bin)
    assert decoded == payload


@pytest.mark.unit
def test_ndarray_roundtrip_binary_codec() -> None:
    np = pytest.importorskip("numpy")
    arr = np.arange(24, dtype=np.int16).reshape(2, 3, 4)
    encoded = encode_for_storage(arr)
    decoded = decode_runtime_value(encoded.vox_type, encoded.payload_json, encoded.payload_bin)
    assert isinstance(decoded, np.ndarray)
    assert decoded.dtype == arr.dtype
    assert decoded.shape == arr.shape
    assert np.array_equal(decoded, arr)


@pytest.mark.unit
def test_simpleitk_image_roundtrip_binary_codec() -> None:
    np = pytest.importorskip("numpy")
    sitk = pytest.importorskip("SimpleITK")
    arr = np.arange(64, dtype=np.uint8).reshape(4, 4, 4)
    image = sitk.GetImageFromArray(arr)
    image.SetSpacing((1.25, 1.0, 0.75))
    encoded = encode_for_storage(image)
    decoded = decode_runtime_value(encoded.vox_type, encoded.payload_json, encoded.payload_bin)
    decoded_arr = sitk.GetArrayFromImage(decoded)
    assert decoded_arr.shape == arr.shape
    assert np.array_equal(decoded_arr, arr)


@pytest.mark.unit
def test_overlay_roundtrip_binary_codec() -> None:
    np = pytest.importorskip("numpy")
    overlay = OverlayValue.from_layers(
        [
            np.zeros((3, 3, 3), dtype=np.float32),
            np.ones((3, 3, 3), dtype=np.float32),
        ],
        metadata={"case": "overlay-demo"},
    )
    encoded = encode_for_storage(overlay)
    decoded = decode_runtime_value(encoded.vox_type, encoded.payload_json, encoded.payload_bin)
    assert isinstance(decoded, OverlayValue)
    assert decoded.metadata == {"case": "overlay-demo"}
    assert len(decoded.layers) == 2
    assert decoded.layers[0].label == "Base"
    assert decoded.layers[0].colormap == "gray"
    assert decoded.layers[1].label == "Overlay 1"
    assert np.array_equal(decoded.layers[0].value, np.zeros((3, 3, 3), dtype=np.float32))
    assert np.array_equal(decoded.layers[1].value, np.ones((3, 3, 3), dtype=np.float32))
