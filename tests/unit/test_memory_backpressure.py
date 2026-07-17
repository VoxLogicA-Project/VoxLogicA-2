"""Deterministic guards for the memory-bounding fix (no hardware/timing deps).

The failure these protect against — a large run's RSS climbing past the budget
until the OS OOM-kills it silently — depends on a slow disk and slow kernels to
manifest as a live crash, which a fast dev machine cannot reproduce on demand.
These tests instead pin the two *mechanisms* the fix installs, directly:

1. ``NodeTable.accounted_bytes`` folds the persist backlog into the resident
   total, so a value evicted from the live tier but still queued for writing is
   still counted (the old ``live_bytes`` under-reported it — the invisible-RSS
   gap).
2. ``LoopAdmission._has_room`` enforces a hard ceiling on the progress floor:
   over the ceiling it refuses to admit even when workers would starve, unless
   the run is truly wedged (nothing running, nothing ready). The old rule
   (``starving or under_soft``) had no ceiling and could bypass the budget
   indefinitely whenever the queue was shallow.
"""

from __future__ import annotations

import types

import pytest

from voxlogica.engine.admission import LoopAdmission, _Job
from voxlogica.engine.node_table import NodeTable


@pytest.mark.unit
def test_accounted_bytes_counts_persist_backlog() -> None:
    """A value evicted from the live tier but still in the write backlog stays
    counted in accounted_bytes — the resident total admission must bound."""
    table = NodeTable(backend=None)
    # Attach a stand-in persister exposing only what accounted_bytes reads.
    table._persister = types.SimpleNamespace(pending_bytes=0)

    table.live_bytes = 500
    table._persister.pending_bytes = 0
    assert table.accounted_bytes == 500

    # A large value was handed to the writer and then evicted from the live
    # tier: live_bytes drops, but the object is still resident in the queue.
    table.live_bytes = 0
    table._persister.pending_bytes = 800
    assert table.accounted_bytes == 800, "backlog must count toward resident total"

    # With no persister at all (--no-cache), accounted == live (graceful).
    table._persister = None
    table.live_bytes = 123
    assert table.accounted_bytes == 123


def _admission_with(accounted: int, qsize: int, *, workers: int, soft: int, hard: int,
                    idle: bool) -> tuple[LoopAdmission, _Job]:
    """A LoopAdmission whose _has_room inputs are all stubbed to fixed values."""
    adm = LoopAdmission.__new__(LoopAdmission)  # bypass __init__; set only what _has_room reads
    adm.window = 8
    adm.workers = workers
    adm.max_live_bytes = soft
    adm.hard_live_bytes = hard
    adm.graph = types.SimpleNamespace(table=types.SimpleNamespace(accounted_bytes=accounted))
    adm.ready = types.SimpleNamespace(qsize=lambda: qsize)
    adm._idle = lambda: idle
    return adm, _Job(loop_id="loop", priority=0)


@pytest.mark.unit
def test_progress_floor_capped_by_hard_ceiling() -> None:
    """The starving override admits under the ceiling but never past it —
    except to break a true wedge."""
    soft, hard, workers = 1000, 1500, 4

    # Under the soft cap: always admit, regardless of queue depth.
    adm, job = _admission_with(500, qsize=0, workers=workers, soft=soft, hard=hard, idle=False)
    assert adm._has_room(job) is True

    # Over soft, workers fed (queue deep): hold back.
    adm, job = _admission_with(1200, qsize=workers, workers=workers, soft=soft, hard=hard, idle=False)
    assert adm._has_room(job) is False

    # Over soft, starving, still under the hard ceiling: progress floor admits.
    adm, job = _admission_with(1200, qsize=0, workers=workers, soft=soft, hard=hard, idle=False)
    assert adm._has_room(job) is True

    # Over the HARD ceiling, starving, work still running elsewhere: REFUSE.
    # (This is the case the old code got wrong — it would admit and let RSS climb.)
    adm, job = _admission_with(1600, qsize=0, workers=workers, soft=soft, hard=hard, idle=False)
    assert adm._has_room(job) is False

    # Over the hard ceiling but truly wedged (nothing running/ready): admit one
    # unit so the run can never deadlock.
    adm, job = _admission_with(1600, qsize=0, workers=workers, soft=soft, hard=hard, idle=True)
    assert adm._has_room(job) is True


@pytest.mark.unit
def test_window_is_an_absolute_cap() -> None:
    """No memory state lets a single loop exceed its window of in-flight bodies."""
    adm, job = _admission_with(0, qsize=0, workers=4, soft=1000, hard=1500, idle=True)
    job.in_flight = adm.window
    assert adm._has_room(job) is False, "window bounds concurrency regardless of free memory"
