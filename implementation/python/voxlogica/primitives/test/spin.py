"""GIL-releasing benchmark kernel.

``spin(x, rounds)`` hashes a 1 MiB buffer ``rounds`` times and returns a float
derived deterministically from ``x`` (not from the digest timing), so results
are reproducible while the CPU cost is real. ``hashlib`` releases the GIL for
updates larger than 2047 bytes, so concurrent spins genuinely occupy multiple
cores — exactly like the imaging kernels the scheduler must keep fed. Used by
the scheduler throughput benchmark (tests/perf/bench_scheduler.py).
"""

import hashlib

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory

_BUFFER = b"\xa5" * (1 << 20)  # 1 MiB: large enough that sha256 drops the GIL


def execute(**kwargs):
    """Burn ``rounds`` sha256 passes over 1 MiB off-GIL; return f(x) deterministically."""
    x = float(kwargs["0"])
    rounds = int(kwargs["1"])
    h = hashlib.sha256()
    for _ in range(max(0, rounds)):
        h.update(_BUFFER)
    # Fold the digest into the result *without* making the value depend on it:
    # (digest % 1) of an integer is 0 — keeps the data dependency on x only.
    return x + (int.from_bytes(h.digest()[:4], "big") & 0)


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="spin",
    namespace="test",
    kind="scalar",
    arity=AritySpec.fixed(2),
    attrs_schema={},
    planner=default_planner_factory("test.spin", kind="scalar"),
    kernel_name="test.spin",
    description="GIL-releasing CPU burner for scheduler benchmarks: spin(x, rounds)",
)
