"""The disk-derived ceiling must not refuse a budget the disk can honour.

`SQLiteResultsDatabase._effective_max_bytes` caps the configured cache budget by
what the volume can still give. The cap is right; the arithmetic was not. It
reserved a slice of the VOLUME and subtracted it from FREE space, so on a volume
less than half empty the reserve swallowed the headroom whole and the ceiling
came out zero -- and a ceiling of zero makes `_enforce_budget` evict every
payload it has just written. Measured on a 31 GB tmpfs with 15.4 GB free: a
5 MB budget, three thousand times over-served by the disk, enforced as 0.

These tests pin the two properties that failure violated: a budget that fits is
honoured, and the ceiling reaches zero only when the disk really is exhausted.
"""

from __future__ import annotations

import collections
import shutil
from pathlib import Path

import pytest

from voxlogica.storage import SQLiteResultsDatabase, _UNBOUNDED

GB = 1024 ** 3
_Usage = collections.namedtuple("Usage", "total used free")


@pytest.fixture
def store(tmp_path: Path):
    db = SQLiteResultsDatabase(db_path=str(tmp_path / "r.db"), max_bytes=5_000_000)
    try:
        yield db
    finally:
        db.close()


def _budget(db, monkeypatch, *, total: int, free: int, payload: int = 0) -> int:
    """The budget the store would enforce on a volume of this size."""
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Usage(total, total - free, free))
    db._payload_bytes = payload
    db._disk_ceiling = None          # force a fresh probe
    db._disk_ceiling_at = 0.0
    return db._effective_max_bytes()


@pytest.mark.unit
@pytest.mark.parametrize("free_gb", [30, 20, 16, 15.4, 10, 2, 0.5])
def test_a_small_budget_is_honoured_at_every_free_level(store, monkeypatch, free_gb) -> None:
    """5 MB fits in half a gigabyte, so no amount of "reserve" may refuse it.

    The regression: below ~15.5 GB free on a 31 GB volume this returned 0.
    """
    assert _budget(store, monkeypatch, total=31 * GB, free=int(free_gb * GB)) == 5_000_000


@pytest.mark.unit
def test_the_reserve_never_takes_more_than_half_of_what_is_there(store, monkeypatch) -> None:
    """The cache may always grow into half the headroom, however big the reserve.

    With no configured budget the ceiling IS the budget, so this is the number
    that decides whether a run keeps its spilled values.
    """
    store._max_bytes = 0                       # disk is the only limit
    budget = _budget(store, monkeypatch, total=31 * GB, free=10 * GB)
    assert budget == 5 * GB                    # headroom 10 GB, reserve clamped to 5 GB


@pytest.mark.unit
def test_payload_already_held_counts_as_headroom(store, monkeypatch) -> None:
    """A full disk still leaves room to shrink INTO, not a reason to drop everything."""
    store._max_bytes = 0
    budget = _budget(store, monkeypatch, total=31 * GB, free=0, payload=8 * GB)
    assert budget == 4 * GB                    # headroom is the 8 GB we hold


@pytest.mark.unit
def test_zero_only_when_the_disk_is_truly_exhausted(store, monkeypatch) -> None:
    """Nothing free and nothing held: evicting everything is then correct."""
    store._max_bytes = 0
    assert _budget(store, monkeypatch, total=31 * GB, free=0, payload=0) == 0


@pytest.mark.unit
def test_a_failed_probe_does_not_mean_a_budget_of_zero(store, monkeypatch) -> None:
    """statvfs can fail; that says nothing about space, so it must not evict all.

    With no configured budget the old fallback stored `self._max_bytes`, i.e. 0
    -- which `_enforce_budget` reads as "no room" rather than "no limit".
    """
    def _boom(_path):
        raise OSError("probe failed")

    monkeypatch.setattr(shutil, "disk_usage", _boom)
    store._max_bytes = 0
    store._disk_ceiling = None
    store._disk_ceiling_at = 0.0
    assert store._effective_max_bytes() == _UNBOUNDED

    store._max_bytes = 5_000_000
    store._disk_ceiling = None
    store._disk_ceiling_at = 0.0
    assert store._effective_max_bytes() == 5_000_000


@pytest.mark.unit
def test_the_cut_is_logged_once(store, monkeypatch, caplog) -> None:
    """A cache quietly smaller than asked for surfaces only as a slow run."""
    store._max_bytes = 100 * GB
    with caplog.at_level("WARNING", logger="voxlogica.storage"):
        for _ in range(3):
            _budget(store, monkeypatch, total=31 * GB, free=10 * GB)
    cuts = [r for r in caplog.records if "cache budget cut" in r.message]
    assert len(cuts) == 1
