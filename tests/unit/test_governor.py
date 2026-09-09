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


def test_the_unaccounted_gap_comes_off_the_budget():
    """The measured case: 50 GB resident against 25 GB accounted.

    25 GB of that is memory the engine cannot see, so 25 GB less of the
    ceiling is available to hold values.

    The host is sized so the result stays ABOVE the floor, because the identity
    being asserted -- budget == ceiling - gap -- is only observable there. It
    used to be asserted on a 61 GB host, and the 0.75 -> 0.45 share change
    (87195ad, +74% throughput on the real sweep) silently moved that case below
    the floor: the assertion then compared 6.25 GB against 2.45 GB and the test
    became a report of the clamp rather than of the subtraction. The clamp gets
    its own test below.
    """
    # 100 GB at a 0.45 share is a 45 GB ceiling, so 45 - 25 = 20 GB: strictly
    # between the 6.25 GB floor and the 25 GB configured budget, which are the
    # two clamps that would otherwise decide instead of the subtraction.
    machine = _Machine(rss_gb=50.0, available_gb=80.0)
    gov = _governor(machine, ram_gb=100.0)
    for _ in range(40):                 # let the EMA settle on the true gap
        machine.tick()
        gov.sample(accounted=25 * _GB)
    assert gov._gap == pytest.approx(25 * _GB, rel=0.02)
    assert gov.budget < 25 * _GB
    assert gov.budget > gov._floor, "this case must not be floor-clamped"
    assert gov.budget == pytest.approx(gov._ceiling - 25 * _GB, rel=0.02)


def test_the_floor_wins_when_the_gap_would_take_everything():
    """On the reference host the FLOOR decides, not the gap -- deliberately.

    61 GB of RAM at a 0.45 share is a 27.5 GB ceiling; a 25 GB unaccounted gap
    would leave 2.45 GB, which is less than a single ITK working set and would
    park the run into a livelock. So the budget is clamped at
    ``_FLOOR_FRACTION`` of the configured one and the engine keeps going,
    accepting that it may be killed rather than guaranteeing that it stalls.

    This is the configuration the 24-core reference host actually runs, so it
    is the case that must be pinned: the gap subtraction is real but, at this
    share, is not what sets the budget.
    """
    machine = _Machine(rss_gb=50.0, available_gb=8.0)
    gov = _governor(machine)
    for _ in range(40):
        machine.tick()
        gov.sample(accounted=25 * _GB)
    assert gov._gap == pytest.approx(25 * _GB, rel=0.02)
    assert gov._ceiling - gov._gap < gov._floor, (
        "this test is only meaningful while the gap would undercut the floor")
    assert gov.budget == gov._floor


def test_an_idle_process_is_not_throttled_by_its_own_interpreter():
    """A ratio diverges when accounted is small; a difference does not.

    Measured: a run that had merely imported torch reported 0.0 MB accounted
    against 313 MB resident. Under the ratio form that was an overhead of
    37,715 and the budget hit its floor, for a plan of twelve nodes using no
    memory at all.
    """
    machine = _Machine(rss_gb=0.31, available_gb=55.0)
    gov = _governor(machine)
    for _ in range(20):
        machine.tick()
        gov.sample(accounted=0)
    assert gov.budget == 25 * _GB, "nothing is being held; nothing to claw back"


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
    """A pathological gap must not drive the engine to a standstill."""
    machine = _Machine(rss_gb=45.0, available_gb=1.0)
    gov = _governor(machine)
    for _ in range(60):
        machine.tick()
        gov.sample(accounted=1 * _GB)   # 44 GB unaccounted
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
    """Between onset and the ceiling the bar ramps, so the response is graded.

    The sample points are FRACTIONS OF THE CEILING, not absolute gigabytes.
    They were absolute (39/42/44/46 GB on a 61 GB host), chosen when the RAM
    share was 0.75 and the ceiling therefore 45.75 GB; at the measured optimum
    of 0.45 the ceiling is 27.5 GB, so every one of those points sat past it
    and the ramp read as saturated -- the test reported a step where the code
    ramps correctly. A test of a ramp has to sample inside the ramp.
    """
    machine = _Machine(rss_gb=5.0, available_gb=50.0)
    gov = _governor(machine)
    ceiling = gov._ceiling
    seen = []
    for fraction in (0.1, 0.85, 0.92, 0.96, 1.0):
        machine.rss = int(ceiling * fraction)
        machine.tick()
        gov.sample(accounted=10 * _GB)
        seen.append(gov.sacrifice_ms)
    assert seen == sorted(seen)
    assert seen[0] == 1.0
    assert seen[-1] > seen[1], f"the bar did not ramp: {seen}"


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
