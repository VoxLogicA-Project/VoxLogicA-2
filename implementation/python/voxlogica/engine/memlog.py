"""Periodic memory-forensics logger — so an OOM kill is never silent.

A SIGKILL (what the OS OOM killer sends) cannot be caught, so a dying process
writes no traceback: the engine simply vanishes mid-run. This daemon appends a
snapshot line every few seconds to a file, flushing each write, so after such a
death the last line shows exactly how memory was trending — was the engine's
*accounted* memory (live tier + persist backlog) under budget while the OS RSS
climbed far past it? That divergence is the signature of an accounting gap; a
flat accounted total near the OS RSS with both under budget rules memory out.

Dep-free (no psutil): current RSS from /proc/self/statm (Linux) or `ps` (macOS),
falling back to peak RSS via ``resource``. Best-effort throughout — the logger
must never be able to crash or slow the run it is observing.
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable


def _page_size() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 4096


def current_rss_bytes() -> int:
    """Best-effort current resident set size in bytes (0 if unobtainable)."""
    try:  # Linux: field 2 of statm is resident pages
        with open("/proc/self/statm", "r") as handle:
            return int(handle.read().split()[1]) * _page_size()
    except (OSError, ValueError, IndexError):
        pass
    try:  # macOS/BSD: ps reports RSS in KiB
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                             capture_output=True, text=True, timeout=2.0)
        return int(out.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    # Peak fallback: ru_maxrss is bytes on macOS, KiB on Linux.
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maxrss if sys.platform == "darwin" else maxrss * 1024


class MemoryLogger:
    """Background snapshot thread; start()/stop() bracket a run. Best-effort."""

    def __init__(self, snapshot: Callable[[], dict], path: str | None = None,
                 interval_s: float = 5.0):
        self._snapshot = snapshot
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_rss = 0
        env = os.environ.get("VOXLOGICA_MEMLOG")
        if path is not None:
            self.path = path
        elif env:
            self.path = env
        else:  # temp dir, not cwd, so a run never litters the repo
            self.path = os.path.join(tempfile.gettempdir(), f"voxlogica-memlog-{os.getpid()}.tsv")

    def start(self) -> None:
        try:
            self._file = open(self.path, "w", buffering=1)  # line-buffered
            self._file.write("elapsed_s\tcompleted\tlive_mb\tbacklog_mb\taccounted_mb"
                             "\tbudget_mb\thard_mb\trss_mb\tin_flight\tready\tparked"
                             "\tevicted_early\tevict_cand\n")
        except OSError:
            self._file = None
            return
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="voxlogica-memlog", daemon=True)
        self._thread.start()
        print(f"[voxlogica] memory log: {self.path}", file=sys.stderr)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._write_line()

    def _write_line(self) -> None:
        if self._file is None:
            return
        try:
            rss = current_rss_bytes()
            self.peak_rss = max(self.peak_rss, rss)
            s = self._snapshot()
            mb = 1024 * 1024
            self._file.write(
                f"{time.monotonic() - self._t0:.1f}\t{s.get('completed', 0)}\t"
                f"{s.get('live_bytes', 0) / mb:.1f}\t{s.get('backlog_bytes', 0) / mb:.1f}\t"
                f"{s.get('accounted_bytes', 0) / mb:.1f}\t{s.get('budget_bytes', 0) / mb:.1f}\t"
                f"{s.get('hard_bytes', 0) / mb:.1f}\t{rss / mb:.1f}\t"
                f"{s.get('in_flight', 0)}\t{s.get('ready', 0)}\t{s.get('parked', 0)}\t"
                f"{s.get('evicted_early', 0)}\t{s.get('evict_candidates', 0)}\n")
        except Exception:  # noqa: BLE001 — observ. must never break the run
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._write_line()  # final sample
        if getattr(self, "_file", None) is not None:
            try:
                self._file.close()
            except OSError:
                pass
        print(f"[voxlogica] peak RSS {self.peak_rss / 1024 / 1024:.0f} MB "
              f"(memory log: {self.path})", file=sys.stderr)
