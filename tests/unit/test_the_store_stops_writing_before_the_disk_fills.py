"""The payload tier must refuse to write once the disk reserve is reached.

WHY THIS TEST EXISTS. The store had eviction and no admission. `_enforce_budget`
trims the tier when it is over budget, but it cannot trim what is live, and it
says so itself before giving up -- "the tier will keep growing while that is
true". So the budget was advisory in exactly the case it was written for.

Measured 2026-09-15: a BraTS double sweep wrote **759 GB against
`--cache-max-gb 700`**, took a shared 3.6 TB volume from 805 GB free to 33 GB,
and died at 6,399,700 completions with nothing in any log -- no traceback, no
`oom-kill` record, a zero-byte stderr file. Recorded as §37k of
HANDOVER-2026-09-11-engine-stability.md.

The fix is admission: before a payload is written, ask whether writing it would
eat into the reserve, and shed it if so. A shed costs a recompute -- every value
in this tier is regenerable from its lineage -- while a full disk costs the run
and everyone else's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from voxlogica.lazy.hash import hash_node
from voxlogica.lazy.ir import NodeSpec
from voxlogica.storage import SQLiteResultsDatabase, spec_row_for

_GB = 1024 ** 3


def _volume(free_bytes: int, total_bytes: int = 100 * _GB):
    """A `shutil.disk_usage` stand-in reporting a volume of a chosen fullness.

    IN BYTES, and the reason is the rule it has to exercise. The reserve is
    `min(max(50 GB, 5% of total), headroom // 2)` where headroom is what the
    tier holds plus what is free -- so on an empty tier the clamp always wins
    and the reserve is half of free, whatever the volume's size. A write is
    refused only when it would not fit in the other half. Expressing that needs
    free space comparable to one payload, which is bytes, not gigabytes.
    """
    return lambda _path: _Usage(total_bytes, total_bytes - free_bytes, free_bytes)


class _Usage(tuple):
    """Minimal stand-in for `os.statvfs`-backed `shutil._ntuple_diskusage`."""

    def __new__(cls, total: int, used: int, free: int):
        return super().__new__(cls, (total, used, free))

    total = property(lambda self: self[0])
    used = property(lambda self: self[1])
    free = property(lambda self: self[2])


def _entry(index: int) -> tuple:
    """One storable result whose value is big enough to need a payload file."""
    numpy = pytest.importorskip("numpy")
    spec = NodeSpec(kind="primitive", operator="test.block",
                    args=(f"{index:064x}",), output_kind="overlay")
    node_id = hash_node(spec)
    value = numpy.zeros((64, 64), dtype=numpy.float32)
    return (node_id, value, {}, 1.0, None, spec_row_for(node_id, spec))


@pytest.mark.unit
def test_a_roomy_disk_still_gets_written(tmp_path: Path, monkeypatch) -> None:
    """The control: with space available nothing changes."""
    monkeypatch.setattr(shutil, "disk_usage", _volume(free_bytes=90 * _GB))
    store = SQLiteResultsDatabase(db_path=str(tmp_path / "roomy.db"), max_bytes=0)
    try:
        store.put_success_batch([_entry(1)])
        stats = store.stats()
    finally:
        store.close()
    assert stats["shed_disk_full"] == 0
    assert stats["payload_entries"] == 1
    assert list(store.payload_dir.glob("*.bin")), "the payload was not written"


@pytest.mark.unit
def test_the_reserve_stops_the_write(tmp_path: Path, monkeypatch) -> None:
    """A volume inside its reserve takes no new payloads at all."""
    monkeypatch.setattr(shutil, "disk_usage", _volume(free_bytes=_PAYLOAD // 2))
    store = SQLiteResultsDatabase(db_path=str(tmp_path / "full.db"), max_bytes=0)
    try:
        store.put_success_batch([_entry(2)])
        stats = store.stats()
    finally:
        store.close()
    assert stats["shed_disk_full"] == 1, "the write was admitted onto a full disk"
    assert stats["payload_entries"] == 0
    assert not list(store.payload_dir.glob("*.bin")), (
        "a payload file was written despite the reserve")


@pytest.mark.unit
def test_a_shed_value_keeps_its_recipe(tmp_path: Path, monkeypatch) -> None:
    """Shedding the value must not shed the spec.

    A spec is a few hundred bytes in SQLite with no payload file. Dropping it
    with the value would trade nothing for the ability to ever name the node
    again -- which is the defect §37 spent a day on from the other direction.
    """
    monkeypatch.setattr(shutil, "disk_usage", _volume(free_bytes=_PAYLOAD // 2))
    store = SQLiteResultsDatabase(db_path=str(tmp_path / "recipe.db"), max_bytes=0)
    node_id = _entry(3)[0]
    try:
        store.put_success_batch([_entry(3)])
        definition = store.get_definition(node_id)
    finally:
        store.close()
    assert definition is not None, "the shed value took its recipe with it"


@pytest.mark.unit
def test_a_write_that_fails_anyway_is_shed_not_raised(tmp_path: Path, monkeypatch) -> None:
    """ENOSPC can still arrive: the probe is cached and the volume is shared.

    It must degrade to a shed. It used to propagate out of a persister thread,
    mid-run.
    """
    monkeypatch.setattr(shutil, "disk_usage", _volume(free_bytes=90 * _GB))
    store = SQLiteResultsDatabase(db_path=str(tmp_path / "enospc.db"), max_bytes=0)

    def _no_space(self, payload_file, payload_bin):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", lambda self, data: _no_space(None, None, None))
    try:
        store.put_success_batch([_entry(4)])       # must not raise
        stats = store.stats()
    finally:
        monkeypatch.undo()
        store.close()
    assert stats["shed_disk_full"] == 1
    assert stats["payload_entries"] == 0
