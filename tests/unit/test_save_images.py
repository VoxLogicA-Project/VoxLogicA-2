"""`save` must write images, not str(volume).

The simpleitk namespace has published writers for .nii.gz/.mha/.png since it was
written, but nothing ever called get_serializers(): the engine strategy branched
on .json/.pkl and fell through to write_text for everything else. So
`save "mask.nii.gz" seg` produced a text file, named like an image, containing a
repr -- silently, with a zero exit code.
"""

import numpy as np
import pytest

from voxlogica.arrays import PolyArray
from voxlogica.engine.strategy import EngineExecutionStrategy


@pytest.fixture
def strategy():
    return EngineExecutionStrategy()


@pytest.fixture
def volume():
    return PolyArray.from_numpy(np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4))


def test_writes_a_readable_nifti(strategy, volume, tmp_path):
    import SimpleITK as sitk

    target = tmp_path / "mask.nii.gz"
    strategy._save(str(target), volume)

    assert target.exists()
    back = sitk.GetArrayFromImage(sitk.ReadImage(str(target)))
    assert back.shape == (2, 3, 4)
    assert np.allclose(back, volume.np())


def test_compound_extension_beats_the_last_suffix(strategy, volume, tmp_path):
    """".nii.gz" is one format; Path.suffix alone reports ".gz"."""
    target = tmp_path / "mask.nii.gz"
    strategy._save(str(target), volume)
    # A text fallback would have written a repr; a real NIfTI starts with its
    # gzip magic instead.
    assert target.read_bytes()[:2] == b"\x1f\x8b"


def test_json_and_text_paths_are_untouched(strategy, tmp_path):
    import json

    target = tmp_path / "scores.json"
    strategy._save(str(target), {"dice": 0.89})
    assert json.loads(target.read_text()) == {"dice": 0.89}

    plain = tmp_path / "note.txt"
    strategy._save(str(plain), 42)
    assert plain.read_text() == "42"


def test_unknown_extension_still_falls_back_to_text(strategy, volume, tmp_path):
    target = tmp_path / "mask.weird"
    strategy._save(str(target), volume)
    assert target.exists()          # no crash: unknown formats degrade, not fail


def test_a_broken_namespace_cannot_sink_save(strategy, tmp_path, monkeypatch):
    """One namespace raising in get_serializers must not take `save` down."""

    class Exploding:
        def get_serializers(self):
            raise RuntimeError("namespace is broken")

    monkeypatch.setattr(strategy.registry, "_namespace_modules",
                        {"boom": Exploding()}, raising=False)
    strategy._serializer_cache = None
    target = tmp_path / "note.txt"
    strategy._save(str(target), "hello")
    assert target.read_text() == "hello"
