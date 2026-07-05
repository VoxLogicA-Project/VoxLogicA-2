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

    #: Cap on resident (live-tier) bytes; admission control holds work back past it.
    max_live_bytes: int
    #: Independent loop bodies scheduled at once; bounds the live frontier.
    loop_window: int
    #: A result is guaranteed-persisted if at least this many consumers share it.
    persist_fanout: int
    #: Push the live-node set to the cache every N completions (amortises an O(n) walk).
    live_refresh_interval: int = 128

    @classmethod
    def from_env(cls, max_concurrency: int, max_live_bytes: int = 0) -> "EngineConfig":
        """Build a config, letting an explicit ``max_live_bytes`` override the env."""
        live = max_live_bytes or _env_gb_as_bytes("VOXLOGICA_MAX_LIVE_GB") or int(_system_ram_bytes() * 0.4)
        return cls(
            max_live_bytes=live,
            loop_window=max(_env_int("VOXLOGICA_LOOP_WINDOW") or max_concurrency, max_concurrency),
            persist_fanout=_env_int("VOXLOGICA_PERSIST_FANOUT") or 8,
        )
