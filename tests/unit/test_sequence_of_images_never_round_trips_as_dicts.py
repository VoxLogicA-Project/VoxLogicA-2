"""A container holding images must never come back from the store as descriptors.

This is the root cause of the "a sequence delivers the wrong element" hunt.
`VoxImageValue.to_json_native` returned the image's DESCRIPTOR, and the only
callers of that method are the sequence and mapping encoders. So a value like a
training case ``[id, [flair], mask]`` was accepted for persistence and written
as JSON with ``{"vox_type": "image", "navigation": {...}}`` where each volume
belonged; `decode_runtime_value` then returned that list of dicts *as the
value*. nnU-Net saw a dict where a volume was expected.

Two properties are pinned here:

  1. such a container is reported as NOT serializable, so it stays in the
     in-memory tier instead of being written lossily; and
  2. if it ever is written again, what comes back is not a descriptor dict.

The failure this prevents is worse than the crash that exposed it: between two
floats the same round-trip returns a plausible wrong number and nothing fails.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from voxlogica.pod_codec import can_serialize_value, decode_runtime_value
from voxlogica.value_model import UnsupportedVoxValueError, adapt_runtime_value


def an_image():
    image = sitk.GetImageFromArray(np.arange(24, dtype=np.float32).reshape(2, 3, 4))
    image.SetSpacing((1.0, 1.0, 2.0))
    return image


def test_an_image_has_no_json_native_form():
    with pytest.raises(UnsupportedVoxValueError):
        adapt_runtime_value(an_image()).to_json_native()


def test_a_sequence_holding_an_image_is_not_serializable():
    ok, reason, record = can_serialize_value(["c000", [an_image()], an_image()])

    assert ok is False
    assert record is None
    assert reason  # the refusal has to say something


def test_a_mapping_holding_an_image_is_not_serializable():
    ok, _reason, record = can_serialize_value({"flair": an_image()})

    assert ok is False
    assert record is None


def test_a_sequence_of_plain_values_still_persists():
    ok, _reason, record = can_serialize_value(["c000", 1.5, 2, True, None])

    assert ok is True
    assert record is not None
    assert decode_runtime_value(record.vox_type, record.payload_json, record.payload_bin) == [
        "c000", 1.5, 2, True, None
    ]


def test_a_bare_image_still_persists_losslessly():
    ok, _reason, record = can_serialize_value(an_image())

    assert ok is True
    assert record is not None
    restored = decode_runtime_value(record.vox_type, record.payload_json, record.payload_bin)
    assert np.allclose(sitk.GetArrayFromImage(restored), sitk.GetArrayFromImage(an_image()))
    assert restored.GetSpacing() == pytest.approx((1.0, 1.0, 2.0))
