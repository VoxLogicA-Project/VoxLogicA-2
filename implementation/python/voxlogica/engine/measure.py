"""Performance measurement that can be defended in a paper.

WHY THIS EXISTS, AND WHY THE PREVIOUS METHOD DID NOT WORK

Every CPU-utilisation number this project produced before this module was
measured from OUTSIDE the process, by a shell loop reading ``/proc``. Two
measurements of the same sixty-case sweep, taken an hour apart, reported 2059%
and 1094% of an available 2400%. Both were wrong, for opposite reasons, and both
were quoted as findings:

  * the first divided tick deltas by an ASSUMED one-second period, while each
    iteration actually took 1.389 s because the sampler was reading 135
    ``/proc/<pid>/task/*/stat`` files per tick -- a 39% overstatement, entirely
    manufactured by the instrument;
  * the second timestamped correctly but its per-tick cost both stole CPU from
    the run and starved the sampler itself (observed intervals up to 2.5 s), so
    its lower number is depressed by an unknown amount.

An instrument whose error is unknown and whose perturbation is unmeasured cannot
support a claim. So this module is built on four rules, and each one exists
because breaking it produced a wrong number:

  1. **The process measures itself.** No external sampler, so there is no second
     process competing for the cores under measurement.
  2. **Every sample carries its own timestamp.** Rates are computed from the
     interval that actually elapsed, never from the interval that was requested.
     The achieved interval statistics are part of the report, so a reader can
     see the sampler's own jitter.
  3. **Per-sample cost is bounded and constant.** A fixed five ``/proc`` reads
     and one ``getrusage`` on the common tick -- ``self/stat``, the loop
     thread's ``stat``, ``self/statm``, ``self/io`` and ``diskstats`` -- no
     matter whether the process has eight threads or a hundred and thirty. ITK's
     pools push the count past 130; an instrument whose cost grows with that is
     measuring itself. The one reading that is unavoidably O(threads), the
     R/S/D census, therefore runs on every Nth tick only (about once a second)
     and leaves its columns empty on the rest, so a reader can never mistake an
     interpolation for a sample.
  4. **The instrument measures its own footprint** with ``RUSAGE_THREAD`` and
     reports it. "The measurement does not perturb the run" is then a number in
     the output rather than a claim in a docstring.

WHAT IT REPORTS, AND WHY EACH FIELD

The authority for total CPU is ``getrusage(RUSAGE_SELF)`` at exit divided by wall
time: the kernel accumulates it, it costs nothing, and it has no sampling error
at all. The per-sample series exists only to show the SHAPE -- where the run is
saturated and where it is not -- and the report says so explicitly, so nobody
quotes a sampled mean when an exact one is right there.

The one decomposition that has ever identified a real defect here is **the event
loop against everything else**. The engine's dispatch, completion and eviction
bookkeeping all run on one asyncio thread; when that thread is at 100% of a core
and the whole process is at 200%, the workers are idle waiting for it, and no
amount of thread tuning or kernel fusion changes that. So the loop thread is
sampled separately, by the native thread id the engine registers from inside the
loop -- known, not guessed.

**Why I/O and thread state are in here too.** A sweep on the 24-core reference
host held a mean of 1675-1934% out of 2400% and stood at >=90% of all cores in
fewer than half the sampled intervals. Scheduler starvation was ruled out by
measurement (31-32 kernels in flight with 30-40 more ready), the GIL by the
re-exec under ``-X gil=0`` on /proc/self/cmdline, and memory bandwidth by a
~5 GB/s demand against a measured ~63 GB/s ceiling. The loop signature above
explained one ten-second window and no more. **The disk was never instrumented
at all**, while ``htop`` showed several threads in D state -- uninterruptible,
i.e. blocked in the kernel on I/O -- at 13-33 MB/s of writes each. A gap that
big cannot be closed by a hypothesis, so the three readings that would settle it
are now part of every run:

  * ``/proc/self/io`` -- what this process asked the kernel for (``rchar``,
    ``wchar``) against what actually reached storage (``read_bytes``,
    ``write_bytes``, ``cancelled_write_bytes``). The pair is the check on
    whether the run writes what we believe it writes: a store mode that claims
    to write nothing must show ``write_bytes`` flat, and ``--no-write-cache``
    was added precisely so that claim could be tested rather than argued.
  * ``/proc/diskstats`` fields 10 and 11 for the device backing the store:
    ``io_ticks`` (ms during which the device had at least one request in
    flight) and the weighted ms (queue depth integrated over time).
    ``io_ticks`` per unit of WALL time is device utilisation, and it is the
    only honest way to say "the disk is saturated" -- throughput is not, since
    a device can be pinned at 100% by a trickle of small synchronous writes.
  * the R/S/D census, because "the disk is busy" and "this process is waiting
    for the disk" are different claims, and only the count of our own threads
    in D state supports the second one.

OFF BY DEFAULT, AND FREE WHEN OFF

Nothing here is constructed unless ``--measure`` is given. There is no branch on
any dispatch path: the engine holds ``None`` and never calls in.
"""

from __future__ import annotations

import json
import os
import platform
import resource
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

#: Clock ticks per second, for /proc/<pid>/stat's utime and stime fields.
_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

#: Sampling period. Five /proc reads per tick, so 0.25 s costs microseconds of
#: CPU per second while resolving stalls an order of magnitude shorter than the
#: ten-second one this instrument was written to explain.
DEFAULT_PERIOD_S = 0.25

#: How often the O(threads) R/S/D census fires, in seconds of wall time. One
#: second is chosen against the phenomenon: the loop stall that motivated this
#: instrument lasted ten seconds, so a per-second census resolves it ten times
#: over while costing one directory scan per second instead of four.
DEFAULT_CENSUS_PERIOD_S = 1.0

#: Linux reports /proc/diskstats sector counts in 512-byte units regardless of
#: the device's own logical block size. Not a tunable: a kernel constant.
_SECTOR_BYTES = 512

#: The /proc/self/io counters this instrument records, in the order they appear
#: in the sample row. rchar/wchar are what the process asked for; read_bytes /
#: write_bytes are what reached the block layer; cancelled_write_bytes is what
#: was written to page cache and then truncated away before it ever got there,
#: which is how a store that deletes its own temporary files can show a large
#: wchar and almost no device traffic.
_IO_KEYS = ("rchar", "wchar", "read_bytes", "write_bytes", "cancelled_write_bytes")


def _cpu_ticks(path: str) -> int | None:
    """utime + stime from a /proc stat file, in clock ticks.

    Parsed by splitting at the LAST ``)``: field 2 is the executable name and
    may itself contain spaces and parentheses, so a plain ``split()`` on the
    whole line silently misaligns every field after it. After the comm, the
    remaining fields start at ``state`` (field 3), which puts utime (field 14)
    at offset 11 and stime (field 15) at offset 12.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    cut = raw.rfind(b")")
    if cut < 0:
        return None
    fields = raw[cut + 2:].split()
    if len(fields) < 13:
        return None
    try:
        return int(fields[11]) + int(fields[12])
    except (ValueError, IndexError):
        return None


def _rss_bytes() -> int:
    """Current RSS. /proc when it exists, else the peak from getrusage."""
    try:
        with open("/proc/self/statm", "rb") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak * 1024 if sys.platform != "darwin" else peak


def _proc_io(path: str = "/proc/self/io") -> tuple[int, ...] | None:
    """The five counters of ``_IO_KEYS``, in that order, from one file read.

    Returns ``None`` rather than zeros when the file is absent (macOS, or a
    kernel built without CONFIG_TASK_IO_ACCOUNTING): a missing reading and a
    reading of zero are opposite conclusions about whether the run writes.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    found: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, value = line.partition(b":")
        name = key.decode("ascii", "replace")
        if name in _IO_KEYS:
            try:
                found[name] = int(value)
            except ValueError:
                return None
    if len(found) != len(_IO_KEYS):
        return None
    return tuple(found[name] for name in _IO_KEYS)


def _resolve_block_device(store_path: str | Path,
                          sys_root: str = "/sys") -> str | None:
    """The /proc/diskstats name of the whole device backing ``store_path``.

    Resolved ONCE, at start, because it cannot change during a run and because
    walking /sys on every tick would be exactly the O(n) cost rule 3 forbids.

    A partition is deliberately mapped to its parent (``sda1`` -> ``sda``):
    ``io_ticks`` on a partition counts only the requests issued through that
    partition, and "the device is saturated" is a statement about the device.
    ``st_dev`` with major 0 is a synthetic filesystem (tmpfs, overlay, btrfs's
    own device numbers) with no single backing device, and returns ``None`` --
    which is the honest answer, not an error.
    """
    try:
        st = os.stat(store_path)
    except OSError:
        return None
    try:
        major, minor = os.major(st.st_dev), os.minor(st.st_dev)
    except (AttributeError, OSError):
        return None
    if major == 0:
        return None
    try:
        real = Path(f"{sys_root}/dev/block/{major}:{minor}").resolve(strict=True)
    except (OSError, RuntimeError):
        real = None
    if real is not None:
        if (real / "partition").is_file() and (real.parent / "stat").is_file():
            return real.parent.name
        if (real / "stat").is_file():
            return real.name
    # Fallback for hosts without /sys/dev/block: match the major:minor in each
    # /sys/class/block/*/dev. Flat scan, no recursion.
    try:
        names = sorted(os.listdir(f"{sys_root}/class/block"))
    except OSError:
        return None
    want = f"{major}:{minor}"
    for name in names:
        base = Path(f"{sys_root}/class/block/{name}")
        try:
            if base.joinpath("dev").read_text(encoding="ascii").strip() != want:
                continue
        except OSError:
            continue
        if (base / "partition").is_file():
            parent = base.resolve().parent
            if (parent / "stat").is_file():
                return parent.name
        return name
    return None


def _diskstats(device: str, path: str = "/proc/diskstats") -> tuple[int, ...] | None:
    """``(sectors_read, sectors_written, io_ticks_ms, weighted_io_ms)``.

    One file read, and the device line found by name. Field numbering follows
    Documentation/admin-guide/iostats.rst: after major/minor/name come reads
    completed, reads merged, sectors read, ms reading, writes completed, writes
    merged, sectors written, ms writing, ios in flight, io_ticks, weighted ms.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    needle = device.encode("ascii", "replace")
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 14 or fields[2] != needle:
            continue
        try:
            return (int(fields[5]), int(fields[9]), int(fields[12]), int(fields[13]))
        except ValueError:
            return None
    return None


def _thread_census(root: str = "/proc/self/task") -> tuple[int, ...] | None:
    """``(threads, running, sleeping, disk_wait)`` from every thread's state.

    THE ONE O(threads) READING, hence never on the common tick. D means
    uninterruptible sleep -- in practice blocked in the kernel on I/O -- and a
    non-zero D count is the only direct evidence that THIS process, rather than
    some other tenant of the device, is the one waiting for the disk.

    A thread that exits between the listdir and the open is skipped rather than
    counted, so the total is of threads actually read, never of names seen.
    """
    try:
        tids = os.listdir(root)
    except OSError:
        return None
    total = running = sleeping = disk = 0
    for tid in tids:
        try:
            with open(f"{root}/{tid}/stat", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        cut = raw.rfind(b")")
        if cut < 0:
            continue
        total += 1
        state = raw[cut + 2:cut + 3]
        if state == b"R":
            running += 1
        elif state == b"S":
            sleeping += 1
        elif state == b"D":
            disk += 1
    if total == 0:
        return None
    return (total, running, sleeping, disk)


@dataclass
class _Sample:
    """One reading. Stored, not aggregated: aggregation is the reader's job.

    Every optional field is ``None`` when its source was unavailable and is
    written as an EMPTY cell, never as a zero: the census fires on one tick in
    four, and a zero there would read as "no threads in D" instead of "not
    asked".
    """

    t_ns: int
    proc_ticks: int
    loop_ticks: int
    rss_bytes: int
    minflt: int
    majflt: int
    nvcsw: int
    nivcsw: int
    io: tuple[int, ...] | None = None
    disk: tuple[int, ...] | None = None
    census: tuple[int, ...] | None = None
    engine: dict[str, Any] = field(default_factory=dict)


class Measurement:
    """Samples this process while it runs, and writes one self-describing file.

    Created only by ``--measure``. ``register_loop_thread`` is called by the
    engine from inside its event loop, which is the only place that can know the
    loop's native thread id for certain.
    """

    __slots__ = ("_path", "_period", "_snapshot", "_samples", "_thread", "_stop",
                 "_loop_tid", "_started_ns", "_start_rusage", "_meta",
                 "_sampler_cpu_s", "_program", "_flags", "_outcome",
                 "_store_path", "_census_every", "_device", "_device_from")

    def __init__(self, path: str | Path, snapshot: Callable[[], dict[str, Any]],
                 *, program: str = "", flags: dict[str, Any] | None = None,
                 period_s: float = DEFAULT_PERIOD_S,
                 store_path: str | Path | None = None,
                 census_every: int | None = None) -> None:
        self._path = Path(path)
        self._period = max(0.01, float(period_s))
        self._snapshot = snapshot
        self._store_path = store_path
        # N as a parameter, defaulted from the period rather than hard-coded,
        # so changing --measure's resolution does not silently change how often
        # the expensive census fires.
        self._census_every = (max(1, int(census_every)) if census_every
                              else max(1, round(DEFAULT_CENSUS_PERIOD_S / self._period)))
        self._device: str | None = None
        self._device_from: str | None = None
        self._samples: list[_Sample] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop_tid: int | None = None
        self._started_ns = 0
        self._start_rusage: Any = None
        self._sampler_cpu_s = 0.0
        self._program = program
        self._flags = dict(flags or {})
        self._meta: dict[str, Any] = {}
        self._outcome: dict[str, Any] = {"recorded": False}

    # ── the engine's side of the contract ────────────────────────────────────

    def register_loop_thread(self) -> None:
        """Called FROM the event loop: records which thread it is.

        The loop's identity is the one thing an outside observer cannot know.
        Everything today assumes it is the main thread, which is true only
        because ``asyncio.run`` happens to be called there; recording it from
        inside removes the assumption.
        """
        try:
            self._loop_tid = threading.get_native_id()
        except AttributeError:            # pre-3.8 or an exotic platform
            self._loop_tid = None

    def set_outcome(self, *, goals_total: int, goals_resolved: int,
                    error: BaseException | None) -> None:
        """What the run actually achieved. Called before ``stop``.

        A MEASUREMENT OF A FAILED RUN MUST SAY SO. A sweep that aborts early
        looks fast: a run of a broken build once finished in 26.3 s against 30 s
        for the working one and was reported as the fastest of three, because
        nothing in the numbers said it had produced 13 of 16 goals instead of
        16. So the outcome sits in the same file as the timings, and the report
        prints it in the same row.
        """
        self._outcome = {
            "recorded": True,
            "goals_total": int(goals_total),
            "goals_resolved": int(goals_resolved),
            "complete": goals_resolved >= goals_total and error is None,
            "error": None if error is None else f"{type(error).__name__}: {error}",
        }

    def start(self) -> None:
        self._started_ns = time.perf_counter_ns()
        self._start_rusage = resource.getrusage(resource.RUSAGE_SELF)
        # --no-cache installs a backend with no store path at all, and that is
        # precisely the configuration whose device traffic must be comparable
        # with the others', so fall back to the directory the measurement
        # itself is written to. Which one was used is in the header, because a
        # device number is only evidence if the reader knows what it is of.
        for candidate in (self._store_path, self._path.parent):
            if candidate is None:
                continue
            self._device = _resolve_block_device(candidate)
            if self._device is not None:
                self._device_from = str(candidate)
                break
        self._meta = self._environment()
        self._thread = threading.Thread(target=self._run, name="voxlogica-measure",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and write the file. Never raises into the run."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self._write()
        except Exception as exc:                            # noqa: BLE001
            print(f"[measure] could not write {self._path}: {exc}",
                  file=sys.stderr, flush=True)

    # ── sampling ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        """Sample on ABSOLUTE deadlines, so late ticks do not accumulate drift.

        A ``sleep(period)`` loop adds its own work to every period and the error
        compounds -- which is precisely how the 39% overstatement happened. Here
        the next wake-up is computed from the start time, so a slow tick is
        followed by a short sleep rather than shifting every tick after it.
        """
        proc_stat = "/proc/self/stat"
        loop_stat = (f"/proc/self/task/{self._loop_tid}/stat"
                     if self._loop_tid is not None else None)
        device = self._device
        census_every = self._census_every
        tick = 0
        base = time.perf_counter()
        while not self._stop.is_set():
            t_ns = time.perf_counter_ns()
            proc = _cpu_ticks(proc_stat)
            loop = _cpu_ticks(loop_stat) if loop_stat else None
            usage = resource.getrusage(resource.RUSAGE_SELF)
            io = _proc_io()
            disk = _diskstats(device) if device else None
            # Tick 0 carries a census so that even a run shorter than the
            # census period reports a thread state instead of nothing.
            census = _thread_census() if tick % census_every == 0 else None
            try:
                engine = self._snapshot()
            except Exception:                               # noqa: BLE001
                engine = {}
            self._samples.append(_Sample(
                t_ns=t_ns,
                proc_ticks=proc if proc is not None else -1,
                loop_ticks=loop if loop is not None else -1,
                rss_bytes=_rss_bytes(),
                minflt=usage.ru_minflt, majflt=usage.ru_majflt,
                nvcsw=usage.ru_nvcsw, nivcsw=usage.ru_nivcsw,
                io=io, disk=disk, census=census,
                engine=engine,
            ))
            tick += 1
            self._stop.wait(max(0.0, base + tick * self._period - time.perf_counter()))
        try:                        # what the instrument itself cost, measured
            own = resource.getrusage(resource.RUSAGE_THREAD)
            self._sampler_cpu_s = own.ru_utime + own.ru_stime
        except (AttributeError, ValueError):
            # RUSAGE_THREAD is Linux-only. Reported as unavailable rather than
            # as a number, because an instrument that cannot state its own cost
            # must say so instead of implying zero.
            self._sampler_cpu_s = -1.0

    # ── the report ───────────────────────────────────────────────────────────

    def _environment(self) -> dict[str, Any]:
        """Everything a reader needs to know that we did not choose to tell them."""
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                    text=True, timeout=5,
                                    cwd=Path(__file__).resolve().parent).stdout.strip()
            dirty = bool(subprocess.run(["git", "status", "--porcelain"],
                                        capture_output=True, text=True, timeout=5,
                                        cwd=Path(__file__).resolve().parent).stdout.strip())
        except Exception:                                   # noqa: BLE001
            commit, dirty = "", False
        model = ""
        try:
            for line in open("/proc/cpuinfo", encoding="utf-8", errors="replace"):
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        return {
            # The command line is the best provenance there is: it names the
            # program, every flag, and the interpreter, and it cannot drift out
            # of step with what actually ran the way a plumbed-through argument
            # can. `program`/`flags` stay as the engine's own view of the run.
            "argv": [sys.executable, *sys.argv],
            "program": self._program,
            "flags": self._flags,
            "commit": commit,
            "working_tree_dirty": dirty,
            "host": platform.node(),
            "cpu_model": model,
            "cpu_count": os.cpu_count(),
            "python": sys.version,
            "free_threaded_build": bool(__import__("sysconfig").get_config_var("Py_GIL_DISABLED")),
            "gil_enabled": (sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True),
            "loop_thread_id": self._loop_tid,
            "per_thread_sampling": (self._loop_tid is not None
                                    and Path(f"/proc/self/task/{self._loop_tid}/stat").exists()),
            "period_requested_s": self._period,
            "ticks_per_second": _TICKS,
            "sector_bytes": _SECTOR_BYTES,
            "store_path": str(self._store_path) if self._store_path else None,
            "device_resolved_from": self._device_from,
            # Named, not guessed: every device rate in the report is about THIS
            # device, and a null here means the columns are empty because the
            # device could not be resolved (tmpfs, macOS) rather than because
            # the device was idle.
            "block_device": self._device,
            "device_stats_available": bool(self._device
                                           and _diskstats(self._device) is not None),
            "process_io_available": _proc_io() is not None,
            "census_every_n_ticks": self._census_every,
            "census_period_s": round(self._census_every * self._period, 6),
            "started_unix": time.time(),
        }

    def _write(self) -> None:
        end_ns = time.perf_counter_ns()
        end = resource.getrusage(resource.RUSAGE_SELF)
        start = self._start_rusage
        wall_s = (end_ns - self._started_ns) / 1e9
        cpu_s = ((end.ru_utime - start.ru_utime) + (end.ru_stime - start.ru_stime))

        intervals = [(b.t_ns - a.t_ns) / 1e9
                     for a, b in zip(self._samples, self._samples[1:])]
        header = dict(self._meta)
        header["outcome"] = self._outcome
        header["authoritative"] = {
            # THE number. getrusage is accumulated by the kernel and read once,
            # so it has no sampling error; every claim about total CPU should
            # come from here and not from the series below.
            "wall_s": round(wall_s, 6),
            "cpu_s": round(cpu_s, 6),
            "mean_cpu_percent": round(100.0 * cpu_s / wall_s, 1) if wall_s > 0 else None,
            "cores_available": os.cpu_count(),
            "user_s": round(end.ru_utime - start.ru_utime, 6),
            "sys_s": round(end.ru_stime - start.ru_stime, 6),
            "peak_rss_bytes": end.ru_maxrss * (1024 if sys.platform != "darwin" else 1),
            "minor_faults": end.ru_minflt - start.ru_minflt,
            "major_faults": end.ru_majflt - start.ru_majflt,
            "voluntary_switches": end.ru_nvcsw - start.ru_nvcsw,
            "involuntary_switches": end.ru_nivcsw - start.ru_nivcsw,
            "source": "getrusage(RUSAGE_SELF), read once at start and once at exit",
        }
        header["instrument"] = {
            # Non-perturbation as a measurement, not an assertion.
            "samples": len(self._samples),
            "period_achieved_mean_s": round(sum(intervals) / len(intervals), 6) if intervals else None,
            "period_achieved_min_s": round(min(intervals), 6) if intervals else None,
            "period_achieved_max_s": round(max(intervals), 6) if intervals else None,
            "sampler_cpu_s": (round(self._sampler_cpu_s, 6)
                              if self._sampler_cpu_s >= 0 else "unavailable (needs RUSAGE_THREAD, Linux)"),
            "sampler_share_of_run_cpu": (round(self._sampler_cpu_s / cpu_s, 6)
                                         if cpu_s > 0 and self._sampler_cpu_s >= 0 else None),
            "note": ("Rates in the series MUST be computed from consecutive t_ns, "
                     "never from period_requested_s: dividing by the nominal period "
                     "overstated CPU by 39% in the measurement this instrument replaced."),
        }

        engine_keys: list[str] = []
        for sample in self._samples:
            for key in sample.engine:
                if key not in engine_keys and not isinstance(sample.engine[key], (dict, list)):
                    engine_keys.append(key)
        io_columns = [f"io_{name}" for name in _IO_KEYS]
        disk_columns = ["dev_sectors_read", "dev_sectors_written",
                        "dev_io_ticks_ms", "dev_weighted_io_ms"]
        census_columns = ["thr_total", "thr_running", "thr_sleeping", "thr_disk"]
        columns = ["t_ns", "proc_ticks", "loop_ticks", "rss_bytes",
                   "minflt", "majflt", "nvcsw", "nivcsw",
                   *io_columns, *disk_columns, *census_columns, *engine_keys]
        widths = (len(io_columns), len(disk_columns), len(census_columns))

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as out:
            out.write("# voxlogica measurement v1\n")
            for line in json.dumps(header, indent=2, default=str).splitlines():
                out.write(f"# {line}\n")
            out.write("\t".join(columns) + "\n")
            for sample in self._samples:
                row: list[Any] = [sample.t_ns, sample.proc_ticks, sample.loop_ticks,
                                  sample.rss_bytes, sample.minflt, sample.majflt,
                                  sample.nvcsw, sample.nivcsw]
                for group, width in zip((sample.io, sample.disk, sample.census), widths):
                    # Empty cells, not zeros: a census that did not fire and a
                    # census that found no thread in D are different readings.
                    row.extend(group if group is not None else ("",) * width)
                row.extend(sample.engine.get(key, "") for key in engine_keys)
                out.write("\t".join(str(value) for value in row) + "\n")
        mean = header["authoritative"]["mean_cpu_percent"]
        disk = self._device_summary()
        verdict = ("" if not self._outcome.get("recorded")
                   else " COMPLETE" if self._outcome.get("complete")
                   else f" INCOMPLETE ({self._outcome.get('goals_resolved')}"
                        f"/{self._outcome.get('goals_total')} goals)")
        print(f"[measure]{verdict} {self._path}: {wall_s:.1f} s wall, {mean}% mean CPU "
              f"of {os.cpu_count() * 100}% available, {len(self._samples)} samples, "
              f"{disk}, instrument cost "
              f"{f'{self._sampler_cpu_s:.2f} CPU-s' if self._sampler_cpu_s >= 0 else 'unavailable'}",
              file=sys.stderr, flush=True)

    def _device_summary(self) -> str:
        """The disk line of the one-liner, over the first and last samples.

        Endpoints, not a mean of per-interval rates: both are the same quotient
        of a counter delta by an ELAPSED t_ns delta, and the endpoints need no
        weighting to stay right when the sampler's own interval jitters.
        """
        if self._device is None:
            return "device not resolved"
        bounds = [s for s in self._samples if s.disk is not None]
        if len(bounds) < 2:
            return f"{self._device}: too few samples"
        first, last = bounds[0], bounds[-1]
        span_s = (last.t_ns - first.t_ns) / 1e9
        if span_s <= 0:
            return f"{self._device}: no elapsed time"
        util = (last.disk[2] - first.disk[2]) / 10.0 / span_s
        written_mb = (last.disk[1] - first.disk[1]) * _SECTOR_BYTES / 1e6
        return (f"{self._device} {util:.0f}% utilised, "
                f"{written_mb / span_s:.1f} MB/s written")


class LoopClock:
    """Where the event loop's own CPU goes, phase by phase.

    The loop is the measured ceiling: at loop occupancy below 25% the process
    ran 2250-2369% of 2400%, and at 90% or above it collapsed to 791-1086%,
    monotonically across twelve runs. But "the loop is busy" is not a defect
    anyone can fix -- the defect is whichever phase inside it is expensive, and
    that had never been attributed. Guessing produced three wrong answers in one
    day (ITK oversubscription, page faults, memory bandwidth), so this measures
    instead.

    Deliberately not a context manager: creating one object per phase per node
    would cost more than the phases it is timing at the dispatch rates involved.
    The caller takes ``perf_counter_ns()`` itself and hands over the delta.

    Held as ``None`` when nothing is measuring, so the cost when off is one
    ``is not None`` test at each phase boundary -- against phases that are
    themselves microseconds of dictionary and list work at minimum.
    """

    __slots__ = ("ns", "hits")

    def __init__(self) -> None:
        self.ns: dict[str, int] = {}
        self.hits: dict[str, int] = {}

    def add(self, phase: str, delta_ns: int) -> None:
        self.ns[phase] = self.ns.get(phase, 0) + delta_ns
        self.hits[phase] = self.hits.get(phase, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        """Phase totals in milliseconds, plus the count of times each ran.

        Milliseconds because the interesting quantity is "how much of a
        thirty-second run", and counts because a phase that is cheap per call
        and ruinous in aggregate looks identical to the reverse without them.
        """
        out: dict[str, Any] = {}
        for phase, total in self.ns.items():
            out[f"loop_{phase}_ms"] = round(total / 1e6, 3)
            out[f"loop_{phase}_n"] = self.hits.get(phase, 0)
        return out
