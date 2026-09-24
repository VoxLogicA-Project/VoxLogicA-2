"""`dir` must see through symlinked directories.

A reviewer assembles the five-case sample as `ln -s <dataset>/<case> data/`.
Python's rglob does not descend into symlinked directories unless told to, so
`dir` returned an empty list on a directory holding five cases and the program
died indexing it. Pinned here both ways: linked directories are found, and the
order matches what real directories give.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.primitives.default.dir import execute as dir_


def _dataset(tmp_path: Path, n: int = 3) -> Path:
    real = tmp_path / "real"
    for i in range(n):
        case = real / f"case_{i:03d}"
        case.mkdir(parents=True)
        (case / f"case_{i:03d}_flair.nii.gz").write_bytes(b"x")
    return real


@pytest.mark.unit
def test_symlinked_case_directories_are_found(tmp_path: Path) -> None:
    real = _dataset(tmp_path)
    linked = tmp_path / "linked"
    linked.mkdir()
    for case in sorted(real.iterdir()):
        (linked / case.name).symlink_to(case, target_is_directory=True)

    found = dir_(**{"0": str(linked), "1": "*_flair.nii.gz", "2": True, "3": False})
    assert found == [f"case_{i:03d}/case_{i:03d}_flair.nii.gz" for i in range(3)], found


@pytest.mark.unit
def test_a_symlinked_root_gives_the_same_listing_as_the_real_one(tmp_path: Path) -> None:
    real = _dataset(tmp_path)
    root_link = tmp_path / "root_link"
    root_link.symlink_to(real, target_is_directory=True)
    via_real = dir_(**{"0": str(real), "1": "*_flair.nii.gz", "2": True, "3": False})
    via_link = dir_(**{"0": str(root_link), "1": "*_flair.nii.gz", "2": True, "3": False})
    assert via_real == via_link
