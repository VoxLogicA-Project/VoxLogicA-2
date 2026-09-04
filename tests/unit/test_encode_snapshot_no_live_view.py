"""The writer thread must never build a SimpleITK array view.

Regression test for a SIGSEGV in the persister: encode_for_storage described
the image by calling as_array(), which builds a zero-copy SimpleITK view over
a buffer ITK is free to release at any moment. On the persister thread that is
a use-after-free, and it killed 369-case sweeps hours in:

    SimpleITK.py MakeUnique <- extra.py GetArrayViewFromImage
    <- arrays.py pinned_view <- value_model.as_array <- describe
    <- pod_codec.encode_for_storage <- storage.put_success_batch
    <- persist._write_batch                       (Fatal Python error: SIGSEGV)

The payload snapshot exists to keep that thread away from ITK memory, but it
carried only bytes -- dtype/shape/size were still read off the live value. The
pin below is the important one: with a snapshot in hand, as_array() must not be
reached at all, so a future edit that quietly reintroduces the touch fails here
rather than on a machine at 3am.
"""

import numpy as np
import pytest

from voxlogica.arrays import PolyArray
from voxlogica.engine.persist import _payload_snapshot
from voxlogica.pod_codec import encode_for_storage
from voxlogica.value_model import PayloadSnapshot, VoxImageValue


@pytest.fixture
def volume():
    return PolyArray.from_numpy(np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4))


def test_snapshot_carries_the_shape_facts(volume):
    snap = _payload_snapshot(volume)
    assert isinstance(snap, PayloadSnapshot)
    assert snap.dtype == "float32"
    assert snap.shape == (2, 3, 4)
    assert snap.size == 24
    assert len(snap.data) == 24 * 4  # float32


def test_encoding_with_a_snapshot_never_touches_the_live_array(volume, monkeypatch):
    snap = _payload_snapshot(volume)

    def explode(self):
        raise AssertionError("as_array() on the writer thread is the segfault")

    monkeypatch.setattr(VoxImageValue, "as_array", explode)
    encoded = encode_for_storage(volume, payload_snapshot=snap)

    assert encoded.vox_type == "image"
    assert encoded.payload_json["dtype"] == "float32"
    assert encoded.payload_json["shape"] == [2, 3, 4]
    assert encoded.descriptor["summary"]["dtype"] == "float32"
    assert encoded.descriptor["summary"]["shape"] == [2, 3, 4]
    assert encoded.descriptor["summary"]["size"] == 24


def test_snapshot_and_live_paths_agree(volume):
    """The snapshot must not change what gets written, only where it is read."""
    with_snap = encode_for_storage(volume, payload_snapshot=_payload_snapshot(volume))
    without = encode_for_storage(volume)

    assert with_snap.payload_json == without.payload_json
    assert with_snap.descriptor == without.descriptor
    assert with_snap.payload_bin == without.payload_bin
