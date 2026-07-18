"""Runtime-tunable knobs for the computation engine and its cache.

Resolved once from the environment with documented defaults, so the scheduler
and the persistence layer never scatter ``os.environ`` reads through their logic.
Every field is a plain int; construct via :meth:`EngineConfig.from_env`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_GB = 1024 ** 3


def _system_ram_bytes() -> int:
    """Total physical RAM, or a conservative 16 GB fallback."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 16 * _GB


def _env_gb_as_bytes(name: str) -> int:
    """Read an env var holding a GB float, in bytes; 0 if unset/invalid."""
    raw = os.environ.get(name)
    if not raw:
        return 0
    try:
        return max(1, int(float(raw) * _GB))
    except ValueError:
        return 0


def _env_int(name: str) -> int:
    """Read an env var holding a non-negative int; 0 if unset/invalid."""
    raw = os.environ.get(name)
    if raw and raw.isdigit():
        return int(raw)
    return 0


@dataclass(frozen=True)
class EngineConfig:
    """Tunables governing memory bounds, loop unrolling, and cache admission."""

    #: Soft budget on resident bytes (live tier + unwritten persist backlog;
    #: "accounted" bytes — see NodeTable.accounted_bytes). Past it, ready work
    #: is parked and proactive reclaim starts evicting durably-persisted
    #: values (ComputationEngine._reclaim_memory). Loop admission does NOT
    #: consult it — admission is demand-driven (ready-queue depth) and reads
    #: only the hard ceiling below.
    max_live_bytes: int
    #: Hard ceiling on accounted bytes — past it, loop admission refuses even
    #: when workers would starve, letting memory drain first. The only
    #: exception is a true wedge (nothing running, nothing ready), where one
    #: unit is admitted to guarantee progress. This is what actually bounds
    #: peak RSS under sustained pressure.
    hard_live_bytes: int
    #: Independent loop bodies scheduled at once; bounds the live frontier.
    loop_window: int
    #: A result is guaranteed-persisted if at least this many consumers share it.
    persist_fanout: int
    #: Loop elements reduced per off-loop expansion step (pipelines DAG build with compute).
    expansion_chunk: int = 0  # 0 = follow loop_window
    #: Skip best-effort persistence of values cheaper to recompute than to store.
    #: Serialization is pure-Python (GIL-holding): writing a sub-millisecond
    #: scalar steals more interpreter time from dispatch than recomputing it
    #: ever would, and GreedyDual-Size would evict it first anyway. Critical
    #: values (the warm-run reuse cut) are always persisted regardless.
    persist_min_compute_ms: float = 1.0
    #: Schedule-time kernel fusion (engine/fusion.py, Stage A). Off is a pure
    #: no-op — the planner is never consulted and every node dispatches
    #: exactly as before Phase 1. See doc/specs/semantic-queueing-fusion.md.
    fusion_enabled: bool = True
    #: Max nodes absorbed into one fusion cone (a hard cap on planner growth,
    #: independent of the loop/admission window).
    fusion_cap: int = 64

    @classmethod
    def from_env(cls, max_concurrency: int, max_live_bytes: int = 0) -> "EngineConfig":
        """Build a config, letting an explicit ``max_live_bytes`` override the env."""
        ram = _system_ram_bytes()
        live = max_live_bytes or _env_gb_as_bytes("VOXLOGICA_MAX_LIVE_GB") or int(ram * 0.4)
        # Hard ceiling: room above the soft cap for the anti-wedge floor to breathe,
        # but clamped well below total RAM so the OS OOM killer is never reached.
        hard = _env_gb_as_bytes("VOXLOGICA_HARD_LIVE_GB") or min(int(live * 1.5), int(ram * 0.7))
        hard = max(hard, live)  # never below the soft cap
        window = max(_env_int("VOXLOGICA_LOOP_WINDOW") or max_concurrency, max_concurrency)
        raw_min = os.environ.get("VOXLOGICA_PERSIST_MIN_MS")
        try:
            persist_min = float(raw_min) if raw_min else 1.0
        except ValueError:
            persist_min = 1.0
        fusion_raw = os.environ.get("VOXLOGICA_FUSION")
        fusion_enabled = fusion_raw != "0" if fusion_raw is not None else True
        return cls(
            max_live_bytes=live,
            hard_live_bytes=hard,
            loop_window=window,
            persist_fanout=_env_int("VOXLOGICA_PERSIST_FANOUT") or 8,
            expansion_chunk=_env_int("VOXLOGICA_EXPANSION_CHUNK") or window,
            persist_min_compute_ms=persist_min,
            fusion_enabled=fusion_enabled,
            fusion_cap=_env_int("VOXLOGICA_FUSION_CAP") or 64,
        )
