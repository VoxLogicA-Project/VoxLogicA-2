"""GIL-releasing large-payload kernel for memory-bounding benchmarks.

``blob(seed, mb, rounds)`` reuses test.spin's proven technique — ``rounds``
sha256 passes over a 1 MiB buffer, which hashlib genuinely runs off-GIL, so
concurrent calls occupy distinct cores cleanly (unlike numpy, whose own
internal BLAS/Accelerate thread pool contends with itself under high Python
thread concurrency and would confound a scheduler benchmark) — then returns a
freshly allocated ``mb``-megabyte bytes payload, so the live tier and persist
backlog carry realistic bytes. Deterministic in ``seed``/``rounds``, not in
wall-clock timing. Used by tests/perf/bench_scheduler.py's large-payload mode.
"""

import hashlib

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory

_BUFFER = b"\xa5" * (1 << 20)


def execute(**kwargs):
    seed = int(kwargs["0"])
    mb = float(kwargs["1"])
    rounds = int(kwargs["2"])
    h = hashlib.sha256()
    for _ in range(max(0, rounds)):
        h.update(_BUFFER)
    size = max(1, int(mb * 1024 * 1024))
    payload = bytearray(size)
    marker = h.digest()[:4]
    payload[:4] = marker
    payload[4:8] = (seed & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(payload)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="blob",
    namespace="test",
    kind="scalar",
    arity=AritySpec.fixed(3),
    attrs_schema={},
    planner=default_planner_factory("test.blob", kind="scalar"),
    kernel_name="test.blob",
    description="GIL-releasing CPU burn (spin technique) + mb-MB payload: blob(seed, mb, rounds) -> bytes",
)
