"""The memory governor: does the budget actually follow RSS?

Every case here is the arithmetic of the OOM this exists to prevent — a 61 GB
host, a 25 GB accounted budget correctly enforced, and 55.5 GB resident.
"""

import pytest

from voxlogica.engine.config import EngineConfig
from voxlogica.engine.governor import MemoryGovernor

_GB = 1024 ** 3


def _config(live_gb: float = 25.0, hard_gb: float = 37.5) -> EngineConfig:
    return EngineConfig(max_live_bytes=int(live_gb * _GB),
                        hard_live_bytes=int(hard_gb * _GB),
                        loop_window=16, persist_fanout=8)


class _Machine:
    """A fake host: RSS and MemAvailable the test drives directly."""

    def __init__(self, rss_gb=1.0, available_gb=40.0):
        self.rss = rss_gb * _GB
        self.available = available_gb * _GB
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def tick(self, seconds: float = 2.0) -> None:
        self.now += seconds


def _governor(machine: _Machine, config: EngineConfig | None = None,
              ram_gb: float = 61.0) -> MemoryGovernor:
    return MemoryGovernor(config or _config(), ram=int(ram_gb * _GB),
                          read_rss=lambda: int(machine.rss),
                          read_available=lambda: int(machine.available),
                          clock=machine.clock)


def test_starts_at_the_configured_budget():
    """Before any reading, nothing is known and nothing is changed."""
    gov = _governor(_Machine())
    assert gov.budget == 25 * _GB
    assert gov.pressure == 0.0


def test_a_two_to_one_overhead_halves_the_budget():
    """The measured case: 55.5 GB resident against 25 GB accounted.

    The engine may then afford only half the ceiling in accounting, because
    every accounted byte is costing it two.
    """
    machine = _Machine(rss_gb=50.0, available_gb=8.0)
    gov = _governor(machine)
    for _ in range(40):                 # let the EMA settle on the true ratio
        machine.tick()
        gov.sample(accounted=25 * _GB)
    assert gov._overhead == pytest.approx(2.0, abs=0.05)
    # ceiling is the tighter of 0.75*61 GB and rss + available - reserve
    assert gov.budget < 25 * _GB
    assert gov.budget == pytest.approx(gov._ceiling / 2.0, rel=0.02)


def test_never_grows_past_the_configured_budget():
    """A comfortable machine does not license a bigger live tier.

    Measured and rejected: 47 GB instead of 25 GB was slower with more
    recomputes (see config._default_live_budget).
    """
    machine = _Machine(rss_gb=2.0, available_gb=55.0)
    gov = _governor(machine)
    for _ in range(20):
        machine.tick()
        gov.sample(accounted=2 * _GB)
    assert gov.budget == 25 * _GB


def test_a_co_tenant_pushes_the_budget_down():
    """MemAvailable collapsing mid-run must tighten the ceiling."""
    machine = _Machine(rss_gb=20.0, available_gb=35.0)
    gov = _governor(machine)
    for _ in range(20):
        machine.tick()
        gov.sample(accounted=20 * _GB)
    roomy = gov.budget
    machine.available = 2 * _GB          # something else took the machine
    for _ in range(20):
        machine.tick()
        gov.sample(accounted=20 * _GB)
    assert gov.budget < roomy


def test_the_budget_has_a_floor():
    """A pathological overhead must not drive the engine to a standstill."""
    machine = _Machine(rss_gb=45.0, available_gb=1.0)
    gov = _governor(machine)
    for _ in range(60):
        machine.tick()
        gov.sample(accounted=1 * _GB)   # 45:1 overhead
    assert gov.budget >= int(25 * _GB * 0.25)


def test_pressure_and_the_two_valves_track_the_ceiling():
    machine = _Machine(rss_gb=5.0, available_gb=50.0)
    gov = _governor(machine)
    machine.tick()
    gov.sample(accounted=5 * _GB)
    assert gov.pressure < 0.85
    assert gov.sacrifice_ms == 1.0      # at rest, only free values are dropped
    assert not gov.blocking

    machine.rss = 46 * _GB              # 0.75 * 61 GB = 45.75 GB ceiling
    machine.available = 2 * _GB
    machine.tick()
    gov.sample(accounted=20 * _GB)
    assert gov.pressure >= 1.0
    assert gov.sacrifice_ms > 100.0     # sacrifice real work rather than die
    assert gov.blocking                 # and stop admitting on top of it


def test_sacrifice_rises_gradually_not_as_a_step():
    """Between onset and the ceiling the bar ramps, so the response is graded."""
    machine = _Machine(rss_gb=5.0, available_gb=50.0)
    gov = _governor(machine)
    seen = []
    for rss_gb in (5, 39, 42, 44, 46):
        machine.rss = rss_gb * _GB
        machine.tick()
        gov.sample(accounted=10 * _GB)
        seen.append(gov.sacrifice_ms)
    assert seen == sorted(seen)
    assert seen[0] == 1.0
    assert seen[-1] > seen[1]


def test_readings_are_rate_limited():
    """This is called from every worker turn; it must not read /proc each time."""
    machine = _Machine(rss_gb=10.0)
    reads = []

    gov = MemoryGovernor(_config(), ram=61 * _GB,
                         read_rss=lambda: reads.append(1) or int(machine.rss),
                         read_available=lambda: int(machine.available),
                         clock=machine.clock)
    for _ in range(100):
        gov.sample(accounted=10 * _GB)  # no clock movement at all
    assert len(reads) == 1


def test_an_unreadable_rss_leaves_the_budget_alone():
    """No reading is not a reading of zero — the budget must not move."""
    machine = _Machine()
    gov = MemoryGovernor(_config(), ram=61 * _GB, read_rss=lambda: 0,
                         read_available=lambda: 0, clock=machine.clock)
    machine.tick()
    gov.sample(accounted=20 * _GB)
    assert gov.budget == 25 * _GB
    assert gov.pressure == 0.0


def test_hard_ceiling_follows_the_budget_down():
    """Admission's backstop must not stay where the soft budget no longer is."""
    machine = _Machine(rss_gb=44.0, available_gb=2.0)
    gov = _governor(machine)
    for _ in range(30):
        machine.tick()
        gov.sample(accounted=12 * _GB)
    assert gov.budget <= gov.hard <= 37.5 * _GB
    assert gov.hard < 37.5 * _GB
