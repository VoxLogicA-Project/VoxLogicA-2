"""The per-entry retry path in AsyncPersister._write_batch.

This path had no coverage, and that is exactly how it came to be broken: it
unpacked four fields from entries that carry five, so the *first* time a batch
write failed for a real reason (a full filesystem, on fmt-5000) every entry hit
a ValueError instead of being retried, and the log reported an unpack error
rather than ENOSPC. A recovery path that is never exercised is not a recovery
path.

The tests drive ``_write_batch`` directly rather than going through ``submit``.
That is deliberate: batching in ``_run`` is opportunistic (drain-what-is-there,
across several writer threads), so a submit-based test cannot guarantee that a
multi-entry batch is ever formed, and would pass by accident on a machine where
each value happened to land in its own transaction. Constructing the batch here
pins the exact condition the fix is about.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.engine.persist import AsyncPersister
from voxlogica.storage import SQLiteResultsDatabase


class _RejectsBatches(SQLiteResultsDatabase):
    """Fails any multi-entry write, and any write of one poisoned node.

    This is the production failure shape: a transaction aborts wholesale (out
    of space, in the case that prompted the fix), and the retry has to sort the
    one genuinely bad value from the good ones that were merely along for the
    ride.
    """

    def __init__(self, *args, poison: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.poison = poison
        self.batch_sizes: list[int] = []

    def put_success_batch(self, entries):
        self.batch_sizes.append(len(entries))
        if len(entries) > 1:
            raise OSError(28, "No space left on device")
        if self.poison is not None and entries[0][0] == self.poison:
            raise OSError(28, "No space left on device")
        return super().put_success_batch(entries)


def _batch(items):
    """Build the 7-tuples ``_write_batch`` consumes: id, value, meta, size, ms, leases, snapshot."""
    return [(node_id, value, {"source": "test"}, 0, 0.0, (), None)
            for node_id, value in items]


@pytest.fixture
def persister_for(tmp_path: Path):
    made = []

    def _make(poison: str | None = None):
        db = _RejectsBatches(db_path=tmp_path / "results.db", poison=poison)
        persister = AsyncPersister(backend=db, max_pending_bytes=1 << 30)
        made.append((persister, db))
        return persister, db

    yield _make
    for persister, db in made:
        persister.close()
        db.close()


@pytest.mark.unit
def test_batch_failure_falls_back_to_one_at_a_time(persister_for) -> None:
    persister, db = persister_for()
    persister._write_batch(_batch([(f"node-{i}", f"value-{i}") for i in range(6)]))

    for i in range(6):
        assert db.has(f"node-{i}") is True, f"node-{i} was lost by the fallback path"


@pytest.mark.unit
def test_one_bad_value_does_not_sink_its_neighbours(persister_for) -> None:
    persister, db = persister_for(poison="node-3")
    persister._write_batch(_batch([(f"node-{i}", f"value-{i}") for i in range(6)]))

    assert db.has("node-3") is False
    for i in (0, 1, 2, 4, 5):
        assert db.has(f"node-{i}") is True, f"node-{i} sank with its poisoned neighbour"


@pytest.mark.unit
def test_fallback_preserves_the_value(persister_for) -> None:
    """The retry must not drop tuple fields on the way through.

    Entries carry a fifth field, the payload snapshot, that ``put_success``'s
    signature cannot express. A fix that "solves" the ValueError by discarding
    it would still satisfy ``has()`` while writing a value whose payload was
    captured from memory ITK may already have freed. Reading the values back is
    what catches that.
    """
    persister, db = persister_for()
    persister._write_batch(_batch([("node-a", [1, 2, 3]), ("node-b", {"k": "v"})]))

    assert db.get_record("node-a").value == [1, 2, 3]
    assert db.get_record("node-b").value == {"k": "v"}


@pytest.mark.unit
def test_the_fallback_actually_ran(persister_for) -> None:
    """Pin that the batch was tried first and the retry went one at a time.

    Without this, a change that quietly stopped batching altogether would leave
    the other tests green while the code they exist to protect no longer runs.
    """
    persister, db = persister_for()
    persister._write_batch(_batch([(f"node-{i}", f"value-{i}") for i in range(6)]))

    assert db.batch_sizes[0] == 6, "the multi-entry batch was not attempted first"
    assert db.batch_sizes[1:] == [1] * 6, "the retry did not go one entry at a time"


@pytest.mark.unit
def test_a_healthy_backend_writes_in_one_transaction(tmp_path: Path) -> None:
    """The fallback is a fallback: no per-entry retries when the batch succeeds.

    Batching exists for throughput (one WAL commit instead of N); a regression
    that always fell through to singletons would be correct and slow, which is
    the kind of thing a correctness-only suite lets through.
    """
    calls: list[int] = []
    db = SQLiteResultsDatabase(db_path=tmp_path / "healthy.db")
    original = db.put_success_batch

    def counting(entries):
        calls.append(len(entries))
        return original(entries)

    db.put_success_batch = counting  # type: ignore[method-assign]
    persister = AsyncPersister(backend=db, max_pending_bytes=1 << 30)
    try:
        persister._write_batch(_batch([(f"node-{i}", f"value-{i}") for i in range(6)]))
        assert calls == [6], f"expected one transaction of six, saw {calls}"
        for i in range(6):
            assert db.has(f"node-{i}") is True
    finally:
        persister.close()
        db.close()
