"""After a native crash, the log must still say what every thread was running.

`faulthandler` prints only the faulting thread on a free-threaded interpreter
("<Cannot show all threads while the GIL is disabled>"), which is precisely the
wrong half for diagnosing a data race between workers. The engine therefore
records what each thread is inside and the memory logger samples it into every
flushed line.
"""

from __future__ import annotations

import threading

from voxlogica.engine import inflight
from voxlogica.engine.memlog import MemoryLogger


def test_nothing_running_renders_as_a_placeholder():
    assert inflight.render() == "-"


def test_a_running_kernel_names_itself_and_leaves_no_trace_after():
    with inflight.executing("abcdef1234567890", "nnunet.predict"):
        rendered = inflight.render()
        assert "nnunet.predict@abcdef12#" in rendered
        assert len(inflight.snapshot()) == 1

    assert inflight.snapshot() == []


def test_every_thread_appears_at_once():
    started, release = threading.Barrier(4), threading.Event()

    def worker(index: int):
        with inflight.executing(f"{index:040x}", f"op{index}"):
            started.wait(timeout=5)
            release.wait(timeout=5)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    started.wait(timeout=5)
    try:
        rendered = inflight.render()
        assert len(inflight.snapshot()) == 3
        for index in range(3):
            assert f"op{index}@" in rendered
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)


def test_the_memory_log_carries_the_column(tmp_path):
    path = tmp_path / "memlog.tsv"
    logger = MemoryLogger(lambda: {"completed": 1, "executing": "nnunet.predict@abcdef12#7"},
                          path=str(path), interval_s=60.0)
    logger.start()
    logger.stop()

    lines = path.read_text().splitlines()
    assert lines[0].split("\t")[-1] == "executing"
    assert lines[-1].split("\t")[-1] == "nnunet.predict@abcdef12#7"
