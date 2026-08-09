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


def _available_ram_bytes() -> int:
    """RAM actually available right now (Linux MemAvailable), else 0."""
    try:
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _default_live_budget(ram: int) -> int:
    """MEASURED AND REJECTED — kept as a record, deliberately not wired in.

    The idea was that 0.4 x RAM is arbitrary and costs recomputes. It does not
    pay: on a 61 GB host, raising the budget to 47 GB made the same one-case
    BraTS sweep marginally SLOWER (318.4 s vs 313.8 s) with MORE recomputes
    (5,080 vs 4,041) and 47 GB peak RSS instead of 25 GB. A bigger live tier
    keeps more values that are never asked for again while the values that do
    get reused are on disk either way, so it buys risk, not throughput. Any
    future attempt to retune this must beat 313.8 s on that workload.

    Original rationale follows.

    A fixed 0.4 x RAM is arbitrary, and on a big host it is expensive: measured
    on a 61 GB box, a one-case BraTS sweep pinned itself at the 25 GB cap and
    paid 1,416 evictions and 4,041 recomputes to stay there, while peak RSS
    never passed 29 GB — the engine was rebuilding values it had room to keep.
    Recomputes are pure waste in a bandwidth-bound workload (the same run with
    the disk tier off, and therefore 3x the recomputes, was 16% SLOWER).

    So: take what is genuinely free, minus a reserve for the OS, the page cache
    and everything else on the machine, and never claim more than three quarters
    of the box. The reserve is the larger of 8 GB and a tenth of RAM, so this
    stays conservative on small machines and still hands a big one real headroom.
    """
    reserve = max(8 * _GB, int(ram * 0.10))
    available = _available_ram_bytes() or int(ram * 0.5)
    return max(int(ram * 0.4), min(int(ram * 0.75), available - reserve))


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
    #: Stage B (engine/numba_fusion.py): compile fusion cones into flat
    #: per-voxel loops. Off is a pure no-op on top of Stage A — cones still
    #: batch-dispatch their real kernels, they just never try the compiled
    #: path. Independent of fusion_enabled's own default so either can be
    #: toggled without the other.
    numba_fusion_enabled: bool = True
    #: Minimum fused-member count before Stage B is even attempted (see
    #: numba_fusion.py's ``_MIN_MEMBERS_FOR_STAGE_B``) — below this, the
    #: mandatory numpy->sitk conversion at the cone's exit costs more than
    #: the compiled loop saves; measured net LOSS, not just a wash.
    # Minimum cone size for the numba backend. Was 12, which this workload's
    # cones never reach (measured mean 3.24; cones_numba stayed 0 on every
    # run). Swept at 12/3/2 on the 1-case reproducer, bit-exact results:
    # 249.1 s / 237.1 s / 231.3 s, with minor faults down 25% at 2 -- fused
    # loops skip the intermediate volumes entirely. Compile-storm risk is why
    # the gate exists; the sweep showed none (compiles are per-shape and this
    # workload is single-shaped).
    numba_min_members: int = 2

    @classmethod
    def from_env(cls, max_concurrency: int, max_live_bytes: int = 0) -> "EngineConfig":
        """Build a config, letting an explicit ``max_live_bytes`` override the env."""
        ram = _system_ram_bytes()
        live = max_live_bytes or _env_gb_as_bytes("VOXLOGICA_MAX_LIVE_GB") or int(ram * 0.4)
        # Hard ceiling: room above the soft cap for the anti-wedge floor to breathe,
        # but clamped well below total RAM so the OS OOM killer is never reached.
        hard = _env_gb_as_bytes("VOXLOGICA_HARD_LIVE_GB") or min(int(live * 1.5), int(ram * 0.7))
        hard = max(hard, live)  # never below the soft cap
        # An EXPLICIT VOXLOGICA_LOOP_WINDOW is authoritative, including below
        # max_concurrency; only the default is floored at the worker count.
        # Rationale: the window bounds how many loop bodies are open at once,
        # so flooring it at the worker count ties the live working set to the
        # thread count -- measured on the TACAS19 BraTS benchmark, peak RSS
        # goes 1.96/3.12/4.68 GB at 8/16/24 threads against a 36MB L3, and
        # wall-clock turns UP past 16 threads even though the machine has 24
        # cores. Decoupling the two is the only way to test whether that
        # working-set growth (rather than the E-cores themselves) is what
        # caps useful concurrency. A window below the worker count can
        # under-feed workers if the per-body DAG exposes too little internal
        # parallelism -- it cannot deadlock (admission's _idle() wedge-breaker
        # still guarantees progress), it just idles cores, so this is a
        # measure-it-yourself knob, not a default.
        explicit_window = _env_int("VOXLOGICA_LOOP_WINDOW")
        window = explicit_window if explicit_window else max_concurrency
        raw_min = os.environ.get("VOXLOGICA_PERSIST_MIN_MS")
        try:
            persist_min = float(raw_min) if raw_min else 1.0
        except ValueError:
            persist_min = 1.0
        fusion_raw = os.environ.get("VOXLOGICA_FUSION")
        fusion_enabled = fusion_raw != "0" if fusion_raw is not None else True
        numba_raw = os.environ.get("VOXLOGICA_NUMBA_FUSION")
        numba_fusion_enabled = numba_raw != "0" if numba_raw is not None else True
        return cls(
            max_live_bytes=live,
            hard_live_bytes=hard,
            loop_window=window,
            persist_fanout=_env_int("VOXLOGICA_PERSIST_FANOUT") or 8,
            expansion_chunk=_env_int("VOXLOGICA_EXPANSION_CHUNK") or window,
            persist_min_compute_ms=persist_min,
            fusion_enabled=fusion_enabled,
            fusion_cap=_env_int("VOXLOGICA_FUSION_CAP") or 64,
            numba_fusion_enabled=numba_fusion_enabled,
            numba_min_members=_env_int("VOXLOGICA_NUMBA_MIN_MEMBERS") or 2,
        )
