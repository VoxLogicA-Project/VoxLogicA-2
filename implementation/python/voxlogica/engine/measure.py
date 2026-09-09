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
  3. **Per-sample cost is bounded and constant.** Exactly two ``/proc`` reads and
     one ``getrusage``, regardless of how many threads the process has. ITK's
     pools push the thread count past 130; an instrument whose cost grows with
     that is measuring itself.
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

#: Sampling period. Two /proc reads per tick, so 0.25 s costs microseconds of
#: CPU per second while resolving stalls an order of magnitude shorter than the
#: ten-second one this instrument was written to explain.
DEFAULT_PERIOD_S = 0.25


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


@dataclass
class _Sample:
    """One reading. Stored, not aggregated: aggregation is the reader's job."""

    t_ns: int
    proc_ticks: int
    loop_ticks: int
    rss_bytes: int
    minflt: int
    majflt: int
    nvcsw: int
    nivcsw: int
    engine: dict[str, Any] = field(default_factory=dict)


class Measurement:
    """Samples this process while it runs, and writes one self-describing file.

    Created only by ``--measure``. ``register_loop_thread`` is called by the
    engine from inside its event loop, which is the only place that can know the
    loop's native thread id for certain.
    """

    __slots__ = ("_path", "_period", "_snapshot", "_samples", "_thread", "_stop",
                 "_loop_tid", "_started_ns", "_start_rusage", "_meta",
                 "_sampler_cpu_s", "_program", "_flags", "_outcome")

    def __init__(self, path: str | Path, snapshot: Callable[[], dict[str, Any]],
                 *, program: str = "", flags: dict[str, Any] | None = None,
                 period_s: float = DEFAULT_PERIOD_S) -> None:
        self._path = Path(path)
        self._period = max(0.01, float(period_s))
        self._snapshot = snapshot
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
        tick = 0
        base = time.perf_counter()
        while not self._stop.is_set():
            t_ns = time.perf_counter_ns()
            proc = _cpu_ticks(proc_stat)
            loop = _cpu_ticks(loop_stat) if loop_stat else None
            usage = resource.getrusage(resource.RUSAGE_SELF)
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
        columns = ["t_ns", "proc_ticks", "loop_ticks", "rss_bytes",
                   "minflt", "majflt", "nvcsw", "nivcsw", *engine_keys]

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as out:
            out.write("# voxlogica measurement v1\n")
            for line in json.dumps(header, indent=2, default=str).splitlines():
                out.write(f"# {line}\n")
            out.write("\t".join(columns) + "\n")
            for sample in self._samples:
                row = [sample.t_ns, sample.proc_ticks, sample.loop_ticks,
                       sample.rss_bytes, sample.minflt, sample.majflt,
                       sample.nvcsw, sample.nivcsw]
                row.extend(sample.engine.get(key, "") for key in engine_keys)
                out.write("\t".join(str(value) for value in row) + "\n")
        mean = header["authoritative"]["mean_cpu_percent"]
        verdict = ("" if not self._outcome.get("recorded")
                   else " COMPLETE" if self._outcome.get("complete")
                   else f" INCOMPLETE ({self._outcome.get('goals_resolved')}"
                        f"/{self._outcome.get('goals_total')} goals)")
        print(f"[measure]{verdict} {self._path}: {wall_s:.1f} s wall, {mean}% mean CPU "
              f"of {os.cpu_count() * 100}% available, {len(self._samples)} samples, "
              f"instrument cost "
              f"{f'{self._sampler_cpu_s:.2f} CPU-s' if self._sampler_cpu_s >= 0 else 'unavailable'}",
              file=sys.stderr, flush=True)
