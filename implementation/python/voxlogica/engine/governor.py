"""Closed-loop memory governance: bound RSS, not the engine's own accounting.

THE DEFECT THIS EXISTS TO FIX. The engine bounded ``accounted_bytes`` — the
live tier plus the unwritten persist backlog — against a budget fixed once at
startup as 0.4 x total RAM. That bound was enforced correctly and the process
was still OOM-killed: on a 61 GB host the accounted total sat at its 25.1 GB
ceiling for twenty minutes while RSS ran at 55.5 GB, and the kernel killed the
run (exit 247) with two thirds of the work done and 14.8 GB of computed-but-
unwritten values lost with it.

Accounting is not resident memory, and the gap is not a rounding error: it was
2.2x. It is made of everything the engine does not own a byte count for —
allocator arenas that were freed to the process but never to the kernel, ITK's
own buffers, numpy temporaries inside a kernel, the buffer pool, interpreter
overhead. Modelling those terms one by one is hopeless and unnecessary. RSS is
directly observable, so the fix is to close the loop on it: measure what the
process actually occupies, measure what the engine accounts for, and let the
ratio between them tell the engine how much accounting it may afford.

    budget = ceiling_rss / overhead_ratio

``overhead_ratio`` is measured, not assumed, so a workload whose kernels hold
little outside the table gets nearly the whole ceiling, and one like the BraTS
sweep — where every dt/mask dispatch carries invisible ITK memory — is throttled
in proportion to the invisibility. No disturbance model, no per-term census, no
env var: the same controller handles a term that has not been thought of yet.

DIRECTION IS ASYMMETRIC. The governor may only shrink the configured budget,
never raise it. That is not timidity, it is a measurement: raising the budget
from 25 GB to 47 GB on this exact workload was SLOWER (318.4 s vs 313.8 s) with
MORE recomputes (5,080 vs 4,041), because a bigger live tier retains values
nobody asks for again while the values that are reused are on disk either way
(see ``config._default_live_budget``). Growth buys risk, not throughput.

WHAT THE CEILING IS. Two constraints, whichever binds first:

- what this process may occupy of the box: ``ram * _RSS_SHARE``;
- what the kernel says is still handable out right now: current RSS plus
  ``MemAvailable`` minus a reserve, so a co-tenant that arrives mid-run pushes
  the engine down instead of the two of them racing into the OOM killer.

``MemAvailable`` was already read in ``config.py`` and had no callers; RSS was
already sampled in ``memlog.py`` and only ever reached the log file. Both
numbers were being taken and thrown away. This is the missing wire.

PRESSURE drives the two valves the budget alone cannot provide, and both are
needed — they answer different questions:

- ``sacrifice_ms`` — how expensive a value may be and still be thrown away
  rather than written. Cheap values are sacrificed FIRST because the disk cache
  exists to keep what is worth reusing, and a value that costs a millisecond to
  rebuild is not it. Under real pressure the bar rises: at the ceiling, losing
  a 200 ms recompute beats losing the whole run.
- ``blocking`` — when even that is not enough, admission stops. Sacrifice can
  only free what is cheap; when the resident bytes are EXPENSIVE and unwritten,
  discarding them is destroying real work, and the only honest response is to
  stop taking on more until the writer drains.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import time

from .config import EngineConfig, _available_ram_bytes, _system_ram_bytes
from .memlog import current_rss_bytes

_GB = 1024 ** 3

#: How much of the box this process may ever occupy. Above this the kernel is
#: paging or reaping, and either outcome is worse than a smaller live tier.
_RSS_SHARE = 0.75
#: Held back for the OS, the page cache and anything else on the machine, so
#: the ceiling derived from MemAvailable never claims the last free byte.
_RESERVE = 6 * _GB
#: The budget may never be squeezed below this fraction of its configured
#: value. A controller with no floor can drive itself to zero on a transient
#: reading and then thrash; progress is still guaranteed by admission's
#: wedge-breaker, but at a rate that would never finish.
_FLOOR_FRACTION = 0.25
#: Seconds between readings. RSS via /proc/self/statm is a single short read,
#: but this is called from every worker turn, so it is rate-limited anyway.
_SAMPLE_S = 1.0
#: Smoothing on the overhead ratio. The ratio jumps when a batch of kernels
#: allocates at once; reacting to each spike would make the budget oscillate
#: and evict work that a moment later there was room for.
_EMA_ALPHA = 0.25
#: Fraction of the ceiling at which the sacrifice bar starts to rise.
_PRESSURE_ONSET = 0.85
#: Sacrifice bar at full pressure, in ms of recompute. Chosen as the cost of a
#: mid-weight kernel on this workload: everything below is genuinely cheaper to
#: rebuild than to keep, and nothing above is thrown away without a write.
_SACRIFICE_MAX_MS = 200.0


def _malloc_trim() -> bool:
    """Hand freed glibc arenas back to the kernel; True if the call was made.

    Python-level frees do not necessarily reduce RSS: glibc keeps freed chunks
    in per-thread arenas, and with 16 worker threads cycling multi-hundred-MB
    volumes that retention is a plausible large share of the accounted-vs-RSS
    gap. ``malloc_trim`` is the only way to ask for it back, it is cheap, and
    on a non-glibc platform its absence is simply a no-op.
    """
    try:
        libc_name = ctypes.util.find_library("c")
        if not libc_name:
            return False
        libc = ctypes.CDLL(libc_name)
        trim = getattr(libc, "malloc_trim", None)
        if trim is None:
            return False
        trim(0)
        return True
    except (OSError, AttributeError, ValueError):
        return False


class MemoryGovernor:
    """Tracks RSS against the box and derives the budgets the engine enforces.

    The engine asks this for its soft budget on every reclaim sweep and its
    hard ceiling on every admission decision, so both move with the machine
    instead of with a number chosen before the run began.
    """

    def __init__(self, config: EngineConfig, ram: int = 0,
                 read_rss=current_rss_bytes, read_available=_available_ram_bytes,
                 clock=time.monotonic):
        self._configured = config.max_live_bytes
        self._configured_hard = config.hard_live_bytes
        self._ram = ram or _system_ram_bytes()
        self._read_rss = read_rss
        self._read_available = read_available
        self._clock = clock
        self._floor = max(int(self._configured * _FLOOR_FRACTION), 1 * _GB)
        self._budget = self._configured
        self._hard = self._configured_hard
        self._overhead = 1.0
        self._pressure = 0.0
        self._rss = 0
        self._ceiling = int(self._ram * _RSS_SHARE)
        self._last_sample = float("-inf")
        self._trims = 0
        self._last_trim = float("-inf")

    # ── what the engine reads ────────────────────────────────────────────
    @property
    def budget(self) -> int:
        """Soft budget on accounted bytes: parking and reclaim trigger here."""
        return self._budget

    @property
    def hard(self) -> int:
        """Hard ceiling on accounted bytes: admission refuses here."""
        return self._hard

    @property
    def pressure(self) -> float:
        """0 while RSS is comfortable, rising to 1 as it reaches the ceiling.

        Past 1 the process is over its share of the box; the value is not
        clamped above, so callers can tell "at the limit" from "past it".
        """
        return self._pressure

    @property
    def sacrifice_ms(self) -> float:
        """Recompute cost below which an undurable value may simply be dropped.

        At rest this is the configured ``persist_min_compute_ms`` equivalent
        (1 ms — a value that cheap was never going to be written anyway). It
        rises with pressure because the alternative to sacrificing a 200 ms
        value at the ceiling is not keeping it, it is being killed and losing
        every value at once.
        """
        if self._pressure <= _PRESSURE_ONSET:
            return 1.0
        span = max(1.0 - _PRESSURE_ONSET, 1e-6)
        ramp = min(1.0, (self._pressure - _PRESSURE_ONSET) / span)
        return 1.0 + ramp * (_SACRIFICE_MAX_MS - 1.0)

    @property
    def blocking(self) -> bool:
        """True when new work must not be admitted whatever the queues say.

        The last valve. Sacrifice handles cheap bytes; this handles the case
        where what is resident is expensive and unwritten, and the only way
        out is to let the writer catch up.
        """
        return self._pressure >= 1.0

    # ── the loop ─────────────────────────────────────────────────────────
    def sample(self, accounted: int) -> None:
        """Take a reading and re-derive the budgets. Rate-limited internally.

        Called from the engine's maintenance path, which runs on every worker
        turn; everything past the rate limit is one clock read.
        """
        now = self._clock()
        if now - self._last_sample < _SAMPLE_S:
            return
        self._last_sample = now
        rss = self._read_rss()
        if rss <= 0:                     # unobtainable: leave the budget alone
            return
        self._rss = rss

        # What the process may still grow to. Two constraints, tightest wins:
        # its share of the box, and what the kernel says is actually free.
        share_ceiling = int(self._ram * _RSS_SHARE)
        available = self._read_available()
        if available > 0:
            live_ceiling = rss + available - _RESERVE
            ceiling = min(share_ceiling, live_ceiling)
        else:
            ceiling = share_ceiling
        self._ceiling = max(ceiling, self._floor)

        # Measured overhead: resident bytes per accounted byte. Never below 1
        # (accounting can lag a free, and a ratio under 1 would license a
        # budget above the ceiling).
        if accounted > 0:
            observed = max(1.0, rss / accounted)
            self._overhead += _EMA_ALPHA * (observed - self._overhead)

        target = int(self._ceiling / max(self._overhead, 1.0))
        # ONLY SHRINK: the configured budget is an upper bound, never a target
        # to grow towards (see the module docstring's asymmetry note).
        self._budget = max(self._floor, min(self._configured, target))
        self._hard = max(self._budget, min(self._configured_hard,
                                           int(self._budget * 1.5)))
        self._pressure = rss / self._ceiling if self._ceiling > 0 else 0.0

        # Under real pressure, ask the allocator for the arenas back before
        # concluding the engine itself must shrink further. Rate-limited
        # separately: trimming is cheap but not free, and nothing changes
        # between two calls a second apart.
        if self._pressure >= _PRESSURE_ONSET and now - self._last_trim >= 5.0:
            self._last_trim = now
            if _malloc_trim():
                self._trims += 1

    def describe(self) -> dict[str, float | int]:
        """Governor terms for the memlog, so a run can be read back afterwards."""
        return {
            "gov_budget_mb": round(self._budget / 1024 ** 2, 1),
            "gov_hard_mb": round(self._hard / 1024 ** 2, 1),
            "gov_ceiling_mb": round(self._ceiling / 1024 ** 2, 1),
            "gov_overhead": round(self._overhead, 2),
            "gov_pressure": round(self._pressure, 3),
            "gov_sacrifice_ms": round(self.sacrifice_ms, 1),
            "gov_trims": self._trims,
        }
