"""What each worker thread is executing right now, for the post-mortem.

A native crash inside a kernel used to leave almost nothing to reason about.
`faulthandler` prints the faulting thread's Python stack, but on a
free-threaded interpreter it refuses the rest:

    <Cannot show all threads while the GIL is disabled>

and the one fact that matters for a data race -- what the OTHER threads were
doing inside the same object -- is exactly the fact it cannot show. (Found the
hard way on `terminate called after throwing an instance of 'c10::Error' /
invalid device pointer`, where two workers were inside one nnU-Net predictor
and only inference could say so.)

So the engine records it itself. Entering a kernel writes (node, operator) for
this thread, leaving clears it, and the memory-forensics logger samples the
table into every line it writes -- flushed, so the last line of a run that died
by SIGKILL or SIGABRT still names what each thread was in.

Cost is two dict operations per node on a thread-local-ish path; at the
130 nodes/s of a real sweep that is not measurable.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

# thread ident -> (node_id, operator). Plain dict: writes are per-thread keys,
# and readers tolerate a torn view (a forensic sample never needs to be exact).
_EXECUTING: dict[int, tuple[str, str]] = {}


@contextmanager
def executing(node_id: str, operator: str) -> Iterator[None]:
    """Record that this thread is inside ``operator`` for ``node_id``."""
    ident = threading.get_ident()
    _EXECUTING[ident] = (node_id, operator)
    try:
        yield
    finally:
        _EXECUTING.pop(ident, None)


def snapshot() -> list[tuple[int, str, str]]:
    """(thread ident, node id, operator) for every kernel currently running."""
    return [(ident, nid, op) for ident, (nid, op) in list(_EXECUTING.items())]


def render() -> str:
    """One compact field for a log line: ``op@node#thread`` per running kernel."""
    return ",".join(f"{op}@{nid[:8]}#{ident}" for ident, nid, op in snapshot()) or "-"
