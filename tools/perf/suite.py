#!/usr/bin/env python3
"""General performance measurement suite for VoxLogicA 2.

Runs one ImgQL program under a set of experiments, repeated and interleaved,
and writes one record per run. It measures from OUTSIDE the engine: wall clock,
processor time and peak resident memory come from the operating system
(``os.wait4``), so a bookkeeping error in the engine cannot flatter its own
measurement. The engine's own counters (kernels executed, fusion cones,
saturation, ...) are read from the JSON summary that ``voxlogica run`` prints
at exit, which every branch prints; nothing here depends on a flag or an
environment variable that exists on one branch only.

EXPERIMENTS (``--experiments``, comma-separated)

  scaling   The program at each width in ``--threads-list``, fresh store each.
  fusion    The program at ``--threads`` with element-wise fusion enabled and
            disabled (``VOXLOGICA_FUSION=0``), fresh store each.
  cache     A cold run on a fresh store, then ``--warm-runs`` runs against the
            store it left.
  resume    An uninterrupted run on a fresh store; then, on another fresh
            store, a run interrupted with SIGINT -- what a user's Ctrl-C sends
            -- after ``--resume-fraction`` of the uninterrupted wall time, and a
            run started again on the store the interrupted one left.

PRECAUTIONS, each of which has invalidated a measurement before

  * Repetitions are interleaved (rep-major), so thermal drift and other slow
    trends spread over every configuration instead of loading the last ones.
  * Before every run the suite waits until other users' CPU stays below
    ``--quiet-pct`` for ``--hold`` seconds, and while the run lasts it samples
    other users' CPU; both are recorded, so a run taken under load is visible.
  * Stores and work files must not live on tmpfs: a store there consumes the
    RAM the engine sizes its memory budget from.
  * The engine's per-machine calibration cache is isolated in a fresh directory
    (``VOXLOGICA_CACHE_DIR``), so a calibration left by an earlier session
    cannot silently change ITK's thread count between two measurements.
  * One untimed warm-up run fills the OS page cache with the dataset and
    compiles the Numba kernels before anything is timed.
  * Every run's printed goals are hashed; runs of the same program that
    disagree are flagged by the report. A fast wrong answer is not a result.

OUTPUT (``--out DIR``)

  meta.json     machine, interpreter, packages, engine commit, program, flags
  runs.jsonl    one record per run, appended as soon as the run ends
  logs/         stdout and stderr of every run
  program.imgql the program actually run (after ``--set`` overrides)

Summarise with ``tools/perf/report.py DIR``.

USAGE
  python3 tools/perf/suite.py --program P.imgql --out DIR --work-dir DIR2 \\
      [--set case_count=10] [--experiments scaling,fusion,cache,resume] \\
      [--threads-list 1,2,4,8,16,24] [--threads 24] [--reps 3]

The suite itself needs only the standard library and any Python 3.10+; the
engine runs under the checkout's own ``.venv`` (``--engine-python``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

EXPERIMENTS = ("scaling", "fusion", "cache", "resume")

#: Counters copied from the engine's exit summary when present. Read by name,
#: so a counter one branch lacks is recorded as absent rather than as zero.
ENGINE_COUNTERS = (
    "saturation", "mean_concurrency", "peak_concurrency", "max_concurrency",
    "peak_live_mb", "live_budget_mb", "kernels_executed", "recomputes",
    "cones_dispatched", "ops_fused", "cones_numba", "bytes_moved_gb",
    "completed", "memo_hits", "memo_misses",
)

#: `name=value`, as VoxLogicA 2 prints a goal; VoxLogicA 1 prefixes it with a
#: timestamp and `[user]`.
GOAL_LINE = re.compile(r"^(?:\[[^\]]*\] \[user\] )?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


# ------------------------------------------------------------ machine probes

def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def cpu_model() -> str:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    if sys.platform == "darwin":
        out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                             capture_output=True, text=True)
        return out.stdout.strip()
    return platform.processor()


def mem_total_gb() -> float | None:
    m = re.search(r"MemTotal:\s+(\d+) kB", _read("/proc/meminfo"))
    return round(int(m.group(1)) / 1024 ** 2, 1) if m else None


def fs_type(path: Path) -> str:
    """Filesystem type of the mount that holds ``path`` (Linux; '?' elsewhere)."""
    best, kind = "", "?"
    target = str(path.resolve())
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1]
        if (target == mount or target.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
            best, kind = mount, parts[2]
    return kind


_HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _total_busy_ticks() -> int | None:
    line = _read("/proc/stat").splitlines()[:1]
    if not line:
        return None
    f = [int(x) for x in line[0].split()[1:]]
    idle = f[3] + (f[4] if len(f) > 4 else 0)
    return sum(f[:8]) - idle


def _process_ticks(pid: int) -> int:
    """utime+stime of a process, threads included; 0 once it has exited."""
    raw = _read(f"/proc/{pid}/stat")
    if not raw:
        return 0
    fields = raw.rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])


def _own_ticks() -> int:
    """CPU of this suite and everything it started (finished children included)."""
    t = os.times()
    return int((t.user + t.system + t.children_user + t.children_system) * _HZ)


class ForeignLoad:
    """Samples the CPU used by everything except the suite and the run.

    Units are percent of one core, as ``top`` shows them, so 100 means one
    core's worth of somebody else's work. Linux only; elsewhere it reports
    nothing rather than a guess.
    """

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.samples: list[float] = []
        self._pid: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def available() -> bool:
        return _total_busy_ticks() is not None

    def _snapshot(self) -> tuple[float, int, int]:
        child = _process_ticks(self._pid) if self._pid else 0
        return time.monotonic(), _total_busy_ticks() or 0, _own_ticks() + child

    def start(self, pid: int | None) -> None:
        self._pid = pid
        self.samples = []
        if not self.available():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        prev = self._snapshot()
        while not self._stop.wait(self.interval):
            cur = self._snapshot()
            dt = cur[0] - prev[0]
            if dt > 0:
                foreign = (cur[1] - prev[1]) - (cur[2] - prev[2])
                self.samples.append(max(0.0, foreign / _HZ / dt * 100))
            prev = cur

    def stop(self) -> dict:
        if self._thread:
            self._stop.set()
            self._thread.join()
        if not self.samples:
            return {"foreign_cpu_mean_pct": None, "foreign_cpu_max_pct": None}
        return {"foreign_cpu_mean_pct": round(sum(self.samples) / len(self.samples), 1),
                "foreign_cpu_max_pct": round(max(self.samples), 1)}


def wait_until_quiet(quiet_pct: float, hold: float, log) -> float:
    """Block until other users' CPU stays under ``quiet_pct`` for ``hold`` s."""
    if hold <= 0 or not ForeignLoad.available():
        return 0.0
    probe = ForeignLoad(interval=1.0)
    started = time.monotonic()
    quiet_since = None
    last_note = started
    probe.start(None)
    try:
        while True:
            time.sleep(1.0)
            if not probe.samples:
                continue
            now = time.monotonic()
            if probe.samples[-1] < quiet_pct:
                quiet_since = quiet_since or now
                if now - quiet_since >= hold:
                    return round(now - started - hold, 1)
            else:
                quiet_since = None
            if now - last_note > 300:
                log(f"waiting for the machine: {probe.samples[-1]:.0f}% foreign CPU, "
                    f"need <{quiet_pct:.0f}% for {hold:.0f}s")
                last_note = now
    finally:
        probe.stop()


# ------------------------------------------------------------- engine probes

def engine_info(engine: Path, python: Path) -> dict:
    def git(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(engine), *args], capture_output=True, text=True)
        return out.stdout.strip()

    probe = (
        "import json, sys, sysconfig, importlib.metadata as m\n"
        "pk = {}\n"
        "for n in ('SimpleITK','numpy','numba','llvmlite','lark','nnunetv2','torch'):\n"
        "    try: pk[n] = m.version(n)\n"
        "    except Exception: pass\n"
        "print(json.dumps({'python': sys.version.split()[0],\n"
        "  'free_threaded_build': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),\n"
        "  'packages': pk}))\n"
    )
    out = subprocess.run([str(python), "-c", probe], capture_output=True, text=True)
    try:
        interp = json.loads(out.stdout)
    except json.JSONDecodeError:
        interp = {"error": out.stderr[-400:]}
    return {
        "path": str(engine),
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "describe": git("log", "-1", "--format=%h %cd %s", "--date=short"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "interpreter": interp,
    }


# --------------------------------------------------------------- the program

def apply_overrides(text: str, overrides: list[str]) -> str:
    """Replace the value of top-level constants: ``--set case_count=10``.

    Only a line of the form ``name = value`` at column 0 is touched, keeping
    any trailing ``//`` comment; a name that is not found is an error, because
    a silent no-op would measure the wrong program.
    """
    for item in overrides:
        name, _, value = item.partition("=")
        name = name.strip()
        pattern = re.compile(rf"^({re.escape(name)}\s*=\s*)([^/\n]*?)(\s*(//.*)?)$", re.M)
        text, count = pattern.subn(lambda m: m.group(1) + value.strip() + m.group(3), text, count=1)
        if count != 1:
            raise SystemExit(f"--set {item}: no top-level definition of {name!r} in the program")
    return text


def parse_output(stdout: str) -> tuple[dict, dict]:
    """The printed goals, and the engine's JSON exit summary."""
    goals: dict[str, str] = {}
    summary: dict = {}
    lines = stdout.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == "{":
            start = i
        elif line == "}" and start is not None:
            try:
                summary = json.loads("\n".join(lines[start:i + 1]))
            except json.JSONDecodeError:
                pass
            start = None
        elif start is None:
            m = GOAL_LINE.match(line)
            if m:
                goals[m.group(1)] = m.group(2)
    return goals, summary


# ------------------------------------------------------------------- the run

def _default_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)


class Suite:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out: Path = args.out
        self.logs = self.out / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.work: Path = args.work_dir
        self.work.mkdir(parents=True, exist_ok=True)
        self.calibration_dir = self.work / "calibration-cache"
        self.calibration_dir.mkdir(exist_ok=True)
        self.program = self.out / "program.imgql"
        self.runs_file = self.out / "runs.jsonl"
        self.counter = 0

    def log(self, message: str) -> None:
        line = f"[suite {time.strftime('%H:%M:%S')}] {message}"
        print(line, file=sys.stderr, flush=True)
        with open(self.out / "suite.log", "a") as f:
            f.write(line + "\n")

    def store(self, label: str) -> Path:
        path = self.work / "stores" / label / "results.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def discard(self, store: Path) -> None:
        if not self.args.keep_stores:
            shutil.rmtree(store.parent, ignore_errors=True)

    def command(self, threads: int, store: Path) -> list[str]:
        cmd = [str(self.args.engine_python), "-u", "-m", "voxlogica.main", "run", "--no-serve",
               "--store-db", str(store)]
        if threads:
            cmd += ["--threads", str(threads)]
        return cmd + list(self.args.engine_arg) + [str(self.program)]

    def environment(self, extra: dict[str, str]) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.args.engine / "implementation" / "python")
        env["PYTHON_GIL"] = "0"
        env["VOXLOGICA_NO_UI"] = "1"
        env["VOXLOGICA_CACHE_DIR"] = str(self.calibration_dir)
        # A switch inherited from the operator's shell would change what is
        # measured without appearing anywhere in the record.
        for key in [k for k in env if k.startswith("VOXLOGICA_") and k not in (
                "VOXLOGICA_NO_UI", "VOXLOGICA_CACHE_DIR")]:
            env.pop(key)
        if self.args.itk_threads:
            env["ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"] = str(self.args.itk_threads)
        else:
            env.pop("ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS", None)
        env.update(extra)
        return env

    def run(self, experiment: str, config: str, rep: int, threads: int, store: Path, *,
            env: dict[str, str] | None = None, interrupt_after: float | None = None,
            command: list[str] | None = None, cwd: Path | None = None) -> dict:
        """One measured run; returns and appends its record.

        ``command`` and ``cwd`` replace the VoxLogicA 2 invocation, so that
        another tool (VoxLogicA 1) is measured by exactly the same means.
        """
        self.counter += 1
        run_id = f"{self.counter:03d}-{experiment}-{config}-r{rep}"
        waited = wait_until_quiet(self.args.quiet_pct, self.args.hold, self.log)
        extra = env or {}
        if cwd is None:
            cwd = self.work / "cwd"
            cwd.mkdir(exist_ok=True)
        out_path, err_path = self.logs / f"{run_id}.out", self.logs / f"{run_id}.err"
        load = ForeignLoad()
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        t0 = time.monotonic()
        with open(out_path, "w") as out_f, open(err_path, "w") as err_f:
            # A shell starting the suite in the background (`cmd &`, nohup)
            # leaves SIGINT ignored, and an ignored disposition is inherited:
            # the engine would then never see the interrupt the resume
            # experiment sends. Restore the default in the child.
            proc = subprocess.Popen(command or self.command(threads, store), cwd=cwd, stdout=out_f,
                                    stderr=err_f, env=self.environment(extra),
                                    preexec_fn=_default_sigint)
            load.start(proc.pid)
            ended_by = "exit"
            timer = None
            if interrupt_after is not None:
                def _interrupt() -> None:
                    nonlocal ended_by
                    if proc.poll() is None:
                        ended_by = "sigint"
                        proc.send_signal(signal.SIGINT)
                timer = threading.Timer(interrupt_after, _interrupt)
                timer.start()
            killer = threading.Timer(self.args.timeout, proc.kill)
            killer.start()
            _, status, rusage = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            killer.cancel()
            if timer:
                timer.cancel()
        wall = time.monotonic() - t0
        foreign = load.stop()
        stdout = out_path.read_text(errors="replace")
        goals, summary = parse_output(stdout)
        counters = (summary.get("execution") or {}).get("cache_summary") or {}
        execution = summary.get("execution") or {}
        maxrss = rusage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        cpu = rusage.ru_utime + rusage.ru_stime
        digest = hashlib.sha256(json.dumps(goals, sort_keys=True).encode()).hexdigest()[:16]
        (self.logs / f"{run_id}.goals.json").write_text(json.dumps(goals, indent=1))
        record = {
            "run_id": run_id, "experiment": experiment, "config": config, "rep": rep,
            "threads": threads, "env": extra, "started_at": started_at,
            "waited_for_quiet_s": waited, "exit_code": proc.returncode, "ended_by": ended_by,
            "interrupt_after_s": round(interrupt_after, 2) if interrupt_after else None,
            "wall_s": round(wall, 3), "cpu_s": round(cpu, 3),
            "cpu_per_wall": round(cpu / wall, 2) if wall > 0 else None,
            "max_rss_mb": round(maxrss / 1024 ** 2, 1),
            "engine_execution_s": execution.get("execution_time"),
            "completed_operations": execution.get("completed_operations"),
            "total_operations": execution.get("total_operations"),
            "goals": len(goals), "goals_digest": digest if goals else None,
            **{k: counters.get(k) for k in ENGINE_COUNTERS},
            **foreign,
        }
        with open(self.runs_file, "a") as f:
            f.write(json.dumps(record) + "\n")
        self.log(f"{run_id}: exit={proc.returncode} ({ended_by}) wall={wall:.1f}s "
                 f"cpu/wall={record['cpu_per_wall']} rss={record['max_rss_mb']}MB "
                 f"kernels={record['kernels_executed']} goals={len(goals)} "
                 f"foreign_max={foreign['foreign_cpu_max_pct']}%")
        return record

    # ------------------------------------------------------------ experiments

    def fresh(self, experiment: str, config: str, rep: int, threads: int, **kw) -> dict:
        store = self.store(f"{experiment}-{config}-r{rep}")
        record = self.run(experiment, config, rep, threads, store, **kw)
        self.discard(store)
        return record

    def scaling(self, rep: int) -> None:
        widths = list(self.args.threads_list)
        if rep % 2 == 0:  # alternate direction, so no width always runs first
            widths.reverse()
        for t in widths:
            self.fresh("scaling", f"t{t}", rep, t)

    def fusion(self, rep: int) -> None:
        configs = [("on", {}), ("off", {"VOXLOGICA_FUSION": "0"})]
        if rep % 2 == 0:
            configs.reverse()
        for label, env in configs:
            self.fresh("fusion", label, rep, self.args.threads, env=env)

    def cache(self, rep: int) -> None:
        store = self.store(f"cache-r{rep}")
        self.run("cache", "cold", rep, self.args.threads, store)
        for k in range(1, self.args.warm_runs + 1):
            self.run("cache", f"warm{k}", rep, self.args.threads, store)
        self.discard(store)

    def resume(self, rep: int) -> None:
        reference = self.fresh("resume", "uninterrupted", rep, self.args.threads)
        store = self.store(f"resume-r{rep}")
        after = max(1.0, reference["wall_s"] * self.args.resume_fraction)
        self.run("resume", "interrupted", rep, self.args.threads, store, interrupt_after=after)
        self.run("resume", "resumed", rep, self.args.threads, store)
        self.discard(store)

    # ------------------------------------------------------------------- main

    def main(self) -> int:
        a = self.args
        text = apply_overrides(a.program.read_text(), a.set)
        self.program.write_text(text)
        meta = {
            "suite": "tools/perf/suite.py",
            "argv": sys.argv,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            "host": socket.gethostname(),
            "machine": {
                "cpu_model": cpu_model(), "logical_cpus": os.cpu_count(),
                "mem_total_gb": mem_total_gb(), "platform": platform.platform(),
                "loadavg_at_start": _read("/proc/loadavg").strip() or None,
            },
            "work_dir": str(self.work), "work_dir_fs": fs_type(self.work),
            "engine": engine_info(a.engine, a.engine_python),
            "program": {"source": str(a.program), "overrides": a.set,
                        "sha256": hashlib.sha256(text.encode()).hexdigest()},
            "settings": {"experiments": a.experiments, "reps": a.reps, "threads": a.threads,
                         "threads_list": a.threads_list, "warmup": a.warmup,
                         "warm_runs": a.warm_runs, "resume_fraction": a.resume_fraction,
                         "itk_threads": a.itk_threads or "ITK default",
                         "quiet_pct": a.quiet_pct, "hold_s": a.hold,
                         "engine_args": a.engine_arg},
        }
        (self.out / "meta.json").write_text(json.dumps(meta, indent=2))
        self.log(f"engine {meta['engine']['branch']} {meta['engine']['describe']}"
                 f"{' (DIRTY)' if meta['engine']['dirty'] else ''}; "
                 f"program sha256 {meta['program']['sha256'][:12]}")
        for w in range(a.warmup):
            self.fresh("warmup", "untimed", w + 1, a.threads)
        for rep in range(1, a.reps + 1):
            for experiment in a.experiments:
                getattr(self, experiment)(rep)
        meta["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
        (self.out / "meta.json").write_text(json.dumps(meta, indent=2))
        self.log(f"done: {self.counter} runs in {self.runs_file}")
        return 0


def _ints(text: str) -> list[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n", 2)[2])
    p.add_argument("--program", type=Path, required=True)
    p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                   help="override a top-level constant of the program (repeatable)")
    p.add_argument("--out", type=Path, required=True, help="results directory (must not exist)")
    p.add_argument("--work-dir", type=Path, required=True,
                   help="stores and scratch; refused on tmpfs unless --allow-tmpfs")
    p.add_argument("--engine", type=Path, default=REPO, help="engine checkout (default: this one)")
    p.add_argument("--engine-python", type=Path, default=None,
                   help="interpreter (default: <engine>/.venv/bin/python)")
    p.add_argument("--engine-arg", action="append", default=[],
                   help="extra argument for `voxlogica run` (repeatable)")
    p.add_argument("--experiments", default=",".join(EXPERIMENTS))
    p.add_argument("--threads", type=int, default=os.cpu_count() or 8,
                   help="width for fusion, cache and resume (default: all CPUs)")
    p.add_argument("--threads-list", type=_ints, default=None,
                   help="widths for scaling (default: powers of two up to --threads, and it)")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--warm-runs", type=int, default=2)
    p.add_argument("--resume-fraction", type=float, default=0.5)
    p.add_argument("--itk-threads", type=int, default=0,
                   help="pin ITK's global thread count (default: ITK's own default)")
    p.add_argument("--quiet-pct", type=float, default=100.0,
                   help="other users' CPU, in percent of one core, below which a run may start")
    p.add_argument("--hold", type=float, default=60.0,
                   help="seconds the machine must stay quiet before a run (0: do not wait)")
    p.add_argument("--timeout", type=float, default=4 * 3600)
    p.add_argument("--keep-stores", action="store_true")
    p.add_argument("--allow-tmpfs", action="store_true")
    a = p.parse_args(argv[1:])

    a.engine = a.engine.resolve()
    a.engine_python = a.engine_python or a.engine / ".venv" / "bin" / "python"
    a.experiments = [e for e in a.experiments.split(",") if e]
    unknown = set(a.experiments) - set(EXPERIMENTS)
    if unknown:
        p.error(f"unknown experiments: {sorted(unknown)}")
    if a.threads_list is None:
        a.threads_list = [t for t in (1, 2, 4, 8, 16, 32) if t < a.threads] + [a.threads]
    if a.out.exists():
        p.error(f"{a.out} exists; results are never overwritten")
    for path in (a.out.parent, a.work_dir.parent if not a.work_dir.exists() else a.work_dir):
        kind = fs_type(path)
        if kind in ("tmpfs", "ramfs") and not a.allow_tmpfs:
            p.error(f"{path} is on {kind}: a store there takes the RAM the engine budgets from")
    a.out.mkdir(parents=True)
    return Suite(a).main()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
