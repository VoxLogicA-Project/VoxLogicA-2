"""Per-host thread-count calibration: measure, don't guess.

``engine/topology.py``'s ``"balanced"`` default (P-cores + half the
E-cores) is a heuristic fitted to ONE host's DRAM-bandwidth saturation
point on the TACAS'19 BraTS procedure. It is a reasonable default
elsewhere, not a measurement elsewhere. This module lets a specific host
replace the guess with an actual sweep, cheaply and safely:

- **Cached by machine fingerprint** (CPU model, P/E core counts, total RAM,
  kernel release), not by wall-clock time: hardware doesn't drift, so
  there is nothing to gain from periodic re-calibration on the same box.
  Recalibrates automatically only when the fingerprint changes (new
  machine, reimaged host, hot-added/removed cores).
- **Idle-gated**: refuses to calibrate on a loaded machine, where
  contention would bias every candidate downward unevenly. Uses
  ``os.getloadavg()`` (portable POSIX; skipped with a warning where
  unavailable) rather than any Linux-only mechanism.
- **Min-of-N, not mean-of-N, per candidate**: contention or a scheduler
  hiccup can only ever make a run slower, never faster, so the minimum
  over repeated trials approximates the uncontended time and discards
  interference automatically instead of averaging it in.
- **Interleaved, not blocked**: candidates are measured in round-robin
  order across repetitions (A,B,C,A,B,C,... not A,A,A,B,B,B,...) so
  thermal drift or a load spike over the sweep's duration doesn't bias
  later candidates.
- **Lock-protected**: a non-blocking flock so two concurrent calibration
  runs don't contend with and bias each other; the second simply skips.

Deliberately NOT run silently on first use: a calibration sweep costs
tens of seconds, and a user running a 7-second job should never be
surprised by that. ``engine/topology.py`` prints a one-line hint instead
(see ``default_concurrency``) pointing at the ``voxlogica calibrate``
subcommand.

CAUGHT DURING DEVELOPMENT, worth recording: the first version of this
sweep used 6 small (160x160x128) synthetic cases and finished in well
under a second per candidate -- fast, and useless. On fmt-5000 it picked
"20 threads" with 12/16/20/24 all within 0.759-0.775s of each other, i.e.
indistinguishable noise, while the real 40-case/full-size sweep that
grounds ``topology.py``'s heuristic needed multi-second runs before the
bandwidth-saturation curve separated from run-to-run jitter at all. The
defaults here (16 cases, full BraTS-sized 240x240x155 volumes) exist
specifically to put each candidate's run in the multi-second range where
the signal this module measures is actually visible above the noise floor
-- a fast calibration that returns noise is worse than no calibration,
because it looks authoritative.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import sysconfig
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

from voxlogica.engine.topology import _count_cpu_list, _CPU_CORE_LIST_PATH


def _cache_dir() -> Path:
    root = os.environ.get("VOXLOGICA_CACHE_DIR")
    base = Path(root) if root else Path.home() / ".cache" / "voxlogica"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_path() -> Path:
    return _cache_dir() / "thread_calibration.json"


def _lock_path() -> Path:
    return _cache_dir() / "thread_calibration.lock"


# ── Machine fingerprint ───────────────────────────────────────────────────

def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or "unknown"


def _total_ram_bytes() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource
        # Not total RAM, but a stable-enough fallback signal for the
        # fingerprint on platforms without /proc/meminfo (e.g. macOS);
        # actual capacity isn't needed, just something that changes if the
        # host changes.
        return resource.getpagesize() * (os.sysconf("SC_PHYS_PAGES")
                                          if hasattr(os, "sysconf") else 0)
    except (ValueError, AttributeError, OSError):
        return 0


@dataclass(frozen=True)
class MachineFingerprint:
    cpu_model: str
    p_cores: int
    logical_cpus: int
    total_ram_bytes: int
    kernel_release: str
    # NOT hardware, but part of the identity anyway: the optimum thread count
    # is a property of (machine, interpreter), not of the machine alone. Under
    # the GIL, useful concurrency stalls around half the cores regardless of
    # what the memory system could sustain; free-threaded, it does not. Every
    # other field here is identical across the two builds, so without this the
    # cache key is unchanged by the switch and a GIL-era measurement is
    # silently reused on a free-threaded interpreter -- reintroducing the exact
    # ceiling the switch removes. Adding the field also invalidates all
    # pre-cutover cached calibrations, which is intended.
    freethreaded: bool

    @classmethod
    def detect(cls) -> "MachineFingerprint":
        p_cores = _count_cpu_list(_CPU_CORE_LIST_PATH) or 0
        return cls(
            cpu_model=_cpu_model(),
            p_cores=p_cores,
            logical_cpus=os.cpu_count() or 0,
            total_ram_bytes=_total_ram_bytes(),
            kernel_release=platform.release(),
            freethreaded=bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        )

    def key(self) -> str:
        """A short, stable identity for this exact host shape."""
        raw = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Idle gate ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IdleCheckResult:
    is_idle: bool
    reason: str


def check_machine_idle(max_load_fraction: float = 0.3) -> IdleCheckResult:
    """Refuse to calibrate on a loaded machine.

    ``max_load_fraction``: the 1-minute load average must be below this
    fraction of the logical CPU count. Uses ``os.getloadavg()`` (POSIX;
    Linux and macOS both support it) rather than parsing ``/proc/stat``,
    so the same check works on both hosts this project targets. Where
    unavailable (no POSIX loadavg -- effectively never in practice here),
    calibration is allowed to proceed with a caveat rather than refused
    outright: an unmeasurable idle state is not evidence of load.
    """
    cpu_count = os.cpu_count() or 1
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        return IdleCheckResult(True, "os.getloadavg() unavailable on this platform; proceeding without an idle check")
    load1, _, _ = getloadavg()
    threshold = cpu_count * max_load_fraction
    if load1 > threshold:
        return IdleCheckResult(
            False,
            f"1-min load average {load1:.2f} exceeds {threshold:.2f} "
            f"({max_load_fraction:.0%} of {cpu_count} logical CPUs) -- "
            f"machine looks busy, refusing to calibrate (would bias results)",
        )
    return IdleCheckResult(True, f"load average {load1:.2f} / {cpu_count} logical CPUs -- idle enough")


# ── Cache ──────────────────────────────────────────────────────────────────

def load_cached_threads(fingerprint: MachineFingerprint | None = None) -> int | None:
    """Return a previously-calibrated thread count for this exact machine, if any."""
    fp = fingerprint or MachineFingerprint.detect()
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get(fp.key())
    if not entry:
        return None
    return entry.get("threads")


def load_cached_itk_threads(threads: int, fingerprint: MachineFingerprint | None = None
                             ) -> int | None:
    """Return the calibrated ITK thread count for THIS machine at exactly
    ``threads`` engine workers, or None if nothing was calibrated for that
    pairing.

    Deliberately keyed on ``threads`` too, not just the fingerprint: the
    manuscript's Part I sec 5 measured the ITK-thread optimum crossing over
    with worker count (itk=24 best at 8 workers, itk=1 best at 18), so a value
    calibrated at one worker count is not known-good at another. If the caller
    is running at a worker count calibration never swept (e.g. an explicit
    --threads N different from the calibrated winner), returning None leaves
    ITK at its own default -- the measured-safe fallback (Part I sec 4: never
    the worst option across any worker count, just not always the best).
    """
    fp = fingerprint or MachineFingerprint.detect()
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get(fp.key())
    if not entry:
        return None
    itk_by_threads = entry.get("itk_threads_by_workers") or {}
    return itk_by_threads.get(str(threads))


def _save_calibration(fingerprint: MachineFingerprint, threads: int, candidates: dict[int, float],
                       itk_threads_by_workers: dict[int, int] | None = None,
                       itk_candidates_wall_seconds: dict[int, float] | None = None) -> None:
    path = _cache_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    entry = {
        "threads": threads,
        "fingerprint": asdict(fingerprint),
        "candidates_wall_seconds": {str(k): v for k, v in candidates.items()},
        "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if itk_threads_by_workers:
        entry["itk_threads_by_workers"] = {str(k): v for k, v in itk_threads_by_workers.items()}
    if itk_candidates_wall_seconds:
        entry["itk_candidates_wall_seconds"] = {
            str(k): v for k, v in itk_candidates_wall_seconds.items()
        }
    data[fingerprint.key()] = entry
    path.write_text(json.dumps(data, indent=2))


# ── Lock ─────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _calibration_lock():
    """Non-blocking exclusive lock so two calibration runs never overlap
    and bias each other. Yields True if acquired, False if another
    calibration is already in progress (caller should abort, not wait --
    waiting would just mean measuring alongside contention anyway)."""
    try:
        import fcntl
    except ImportError:
        yield True  # no POSIX locking available; proceed uncoordinated
        return
    lock_file = open(_lock_path(), "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield True
    except OSError:
        yield False
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


# ── Synthetic representative workload ──────────────────────────────────────

def _make_blob_volume(shape: tuple[int, int, int], seed: int, n_blobs: int = 5):
    import numpy as np
    rng = np.random.default_rng(seed)
    zz, yy, xx = (a.astype(np.float32) for a in np.meshgrid(
        *[np.arange(s) for s in shape], indexing="ij"))
    field = np.zeros(shape, dtype=np.float32)
    for _ in range(n_blobs):
        cz, cy, cx = (rng.uniform(0, 1, 3) * np.array(shape)).astype(np.float32)
        sigma = float(rng.uniform(0.05, 0.15) * min(shape))
        amp = float(rng.uniform(50, 200))
        d2 = (zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2
        field += amp * np.exp(-d2 / (2 * sigma ** 2))
    field += rng.normal(0, 5, shape).astype(np.float32)
    return field.astype(np.float32)


_CALIBRATION_PROGRAM = """
import "simpleitk"
import "vox1"

dataset_root = "{tmpdir}"
all_paths = dir(dataset_root, "*.nii.gz", true, true)

read_image(path) = ReadImage(path)
to_intensity(img) = intensity(img)

preprocess(flair) =
  let background = touch(leq_sv(0.1, flair), border(flair)) in
  let brain = not(background) in
  percentiles(flair, brain, 0)

segment(pflair) =
  let hyper_intense = smoothen(geq_sv(0.93, pflair), 5.0) in
  let very_intense  = smoothen(geq_sv(0.88, pflair), 2.0) in
  grow(hyper_intense, very_intense)

case_result(g) = volume(segment(preprocess(to_intensity(read_image(index(all_paths, g))))))

all_indices = range(0, {n_cases})
total = fold + 0 (for i in all_indices do case_result(i))
print "total" total
"""


def _build_synthetic_dataset(tmpdir: Path, n_cases: int, shape=(240, 240, 155)) -> None:
    import SimpleITK as sitk
    for i in range(n_cases):
        arr = _make_blob_volume(shape, seed=i)
        sitk.WriteImage(sitk.GetImageFromArray(arr), str(tmpdir / f"case{i}.nii.gz"))


def _candidate_thread_counts(fingerprint: MachineFingerprint) -> list[int]:
    p = fingerprint.p_cores
    logical = fingerprint.logical_cpus
    e = max(0, logical - p)
    if e == 0:
        return sorted({max(1, logical // 2), logical})
    raw = [p, p + e // 4, p + e // 2, p + 3 * e // 4, p + e]
    return sorted({t for t in raw if t > 0})


def _candidate_itk_threads(winner_threads: int, fingerprint: MachineFingerprint) -> list[int]:
    """ITK thread-count candidates to try AT the winning engine worker count.

    Small and bounded on purpose: manuscripts/engine-scaling-2026-07.md Part I
    sec 5 measured that the optimum is not a formula (it crosses over with
    worker count) but IS well-approximated by a small handful of shapes: ITK
    inline (1, so the engine alone supplies parallelism), ITK filling whatever
    the engine leaves idle (logical_cpus // winner_threads, the "one native
    thread per otherwise-idle core" point), and ITK using everything (the
    as-shipped default, included so calibration can confirm "leave it alone"
    is still the winner rather than assuming it never is).
    """
    logical = fingerprint.logical_cpus
    raw = [1, max(1, logical // max(1, winner_threads)), logical]
    return sorted({t for t in raw if t > 0})


def _time_one_run(program_text: str, threads: int, itk_threads: int | None = None) -> float:
    """Run the calibration workload once at a given thread count; return wall seconds.

    ``itk_threads``, if given, is applied via ``engine.itk_threads.apply_itk_threads``
    before the run -- this is how the ITK-thread sub-sweep (see
    ``_candidate_itk_threads``) tests each candidate. Left ``None`` for the
    worker-count sweep, which measures ITK left at its own default.
    """
    from voxlogica.execution import ExecutionEngine
    from voxlogica.parser import parse_program_content
    from voxlogica.reducer import reduce_program
    from voxlogica.storage import NoCacheStorageBackend

    if itk_threads is not None:
        from voxlogica.engine.itk_threads import apply_itk_threads
        apply_itk_threads(itk_threads)

    syntax = parse_program_content(program_text)
    workplan = reduce_program(syntax)
    engine = ExecutionEngine(storage_backend=NoCacheStorageBackend(), no_cache=True,
                              threads=threads)
    t0 = time.perf_counter()
    engine.execute_workplan(workplan)
    return time.perf_counter() - t0


def run_calibration(*, n_cases: int = 16, reps: int = 3, force_ignore_idle: bool = False,
                     progress: Callable[[str], None] | None = None) -> dict:
    """Sweep candidate thread counts on a small representative pipeline and
    cache the winner for this exact machine.

    Returns a dict summary (candidates -> best wall time, chosen thread
    count, whether the result was cached). Raises ``RuntimeError`` if the
    machine looks busy and ``force_ignore_idle`` is False, or if another
    calibration is already in progress.
    """
    def _report(msg: str) -> None:
        if progress is not None:
            progress(msg)

    fingerprint = MachineFingerprint.detect()
    if fingerprint.p_cores == 0:
        raise RuntimeError(
            "No hybrid P/E core split detected on this host "
            "(/sys/devices/cpu_core/cpus not found) -- calibration targets "
            "exactly the P/E tradeoff this module exists for, and has "
            "nothing to measure here. Use --threads N directly instead."
        )

    idle = check_machine_idle()
    if not idle.is_idle and not force_ignore_idle:
        raise RuntimeError(f"Refusing to calibrate: {idle.reason}. Pass --force to override.")
    _report(f"idle check: {idle.reason}")

    with _calibration_lock() as acquired:
        if not acquired:
            raise RuntimeError(
                "Another calibration is already running on this machine "
                "(lock held) -- refusing to run concurrently, which would "
                "bias both measurements. Try again once it finishes."
            )

        candidates = _candidate_thread_counts(fingerprint)
        _report(f"machine: {fingerprint.p_cores} P-cores, {fingerprint.logical_cpus} logical CPUs")
        _report(f"candidates: {candidates}  (reps={reps}, interleaved, min-of-N)")

        with tempfile.TemporaryDirectory(prefix="voxlogica-calibrate-") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            _report(f"generating {n_cases} synthetic volumes...")
            _build_synthetic_dataset(tmpdir, n_cases)
            program_text = _CALIBRATION_PROGRAM.format(tmpdir=str(tmpdir), n_cases=n_cases)

            best: dict[int, float] = {c: float("inf") for c in candidates}
            for rep in range(reps):
                for c in candidates:
                    wall = _time_one_run(program_text, c)
                    best[c] = min(best[c], wall)
                    _report(f"  rep {rep + 1}/{reps}  threads={c:3d}  wall={wall:.2f}s "
                            f"(best so far: {best[c]:.2f}s)")

            winner = min(best, key=best.get)
            _report(f"chosen: {winner} threads (best wall time {best[winner]:.2f}s)")

            # Second, small sweep: AT the winning worker count, is there an ITK
            # thread count that beats leaving ITK at its own default? Bounded to
            # a handful of candidates (_candidate_itk_threads) and run only at
            # the winner, not the full cross product -- see that function's
            # docstring and manuscripts/engine-scaling-2026-07.md Part I sec 5
            # for why a fixed formula was tried, reverted, and replaced with
            # this measurement instead.
            itk_candidates = _candidate_itk_threads(winner, fingerprint)
            _report(f"itk-thread candidates at {winner} workers: {itk_candidates} "
                     f"(reps={reps}, interleaved, min-of-N)")
            itk_best: dict[int, float] = {c: float("inf") for c in itk_candidates}
            for rep in range(reps):
                for c in itk_candidates:
                    wall = _time_one_run(program_text, winner, itk_threads=c)
                    itk_best[c] = min(itk_best[c], wall)
                    _report(f"  rep {rep + 1}/{reps}  itk_threads={c:3d}  wall={wall:.2f}s "
                             f"(best so far: {itk_best[c]:.2f}s)")
            itk_winner = min(itk_best, key=itk_best.get)
            _report(f"chosen: itk_threads={itk_winner} at {winner} workers "
                     f"(best wall time {itk_best[itk_winner]:.2f}s)")

        _save_calibration(fingerprint, winner, best,
                           itk_threads_by_workers={winner: itk_winner},
                           itk_candidates_wall_seconds=itk_best)
        return {
            "fingerprint": asdict(fingerprint),
            "candidates_wall_seconds": best,
            "chosen_threads": winner,
            "itk_candidates_wall_seconds": itk_best,
            "chosen_itk_threads": itk_winner,
        }
