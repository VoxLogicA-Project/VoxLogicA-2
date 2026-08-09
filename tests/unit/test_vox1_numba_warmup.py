"""The numba warm-up must cover every signature production actually uses.

numba's dispatcher is not safe to enter concurrently while it still has to
produce a specialization, and this is a free-threaded build, so nothing else
serializes it. `kernels._warm_numba_dispatchers()` exists to compile everything
on the importing thread, before any worker exists.

The trap it fell into is that a signature includes the array's *read-only*
flag, not just dtype and rank. The production call sites pass `pinned_view()`
results -- read-only views over ITK-owned memory -- while the warm-up used
hand-built writable probe arrays. numba treats those as different signatures,
so six dispatchers, including the one named in the original crash report, were
still being compiled by whichever worker thread reached them first.

This test therefore asserts the property that actually matters: after import,
exercising the real kernels must not produce a single new specialization.
It runs in a fresh interpreter because a dispatcher warmed by an earlier test
in the same process would hide exactly the defect this is guarding against.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROBE = r'''
import json
import numpy as np
import SimpleITK as sitk
from voxlogica.primitives.vox1 import kernels as K

try:
    from numba.core.dispatcher import Dispatcher
except Exception:
    from numba.core.registry import CPUDispatcher as Dispatcher

if not K._HAS_NUMBA:
    print(json.dumps({"skipped": "numba unavailable"}))
    raise SystemExit(0)

dispatchers = {n: o for n in dir(K)
               if isinstance(o := getattr(K, n), Dispatcher)}
before = {n: len(o.signatures) for n, o in dispatchers.items()}

rng = np.random.default_rng(0)
shape = (8, 10, 10)
u8a = (rng.random(shape) < 0.5).astype(np.uint8)
u8b = (rng.random(shape) < 0.5).astype(np.uint8)
f32 = (rng.random(shape) * 100).astype(np.float32)
ia, ib, imf = (sitk.GetImageFromArray(x) for x in (u8a, u8b, f32))

K.near(ia)
K.through(ia, ib)
K.maxvol(ia)
K.mask(imf, ib)
K.mask(ia, ib)
K.percentiles(imf, ib, 0.0)
K.volume(ia)
K.border(ia)

# The parallel-sort branch of `percentiles` only engages above this
# population, so a small image never reaches its kernels at all.
side = int(round((K._PARALLEL_SORT_MIN_POPULATION + 4096) ** (1 / 3))) + 2
big = (side, side, side)
K.percentiles(sitk.GetImageFromArray((rng.random(big) * 100).astype(np.float32)),
              sitk.GetImageFromArray(np.ones(big, np.uint8)), 0.0)

after = {n: len(o.signatures) for n, o in dispatchers.items()}
print(json.dumps({
    "warmup_failures": list(K._WARMUP_FAILURES),
    "count": len(dispatchers),
    "cold": {n: [before[n], after[n]] for n in dispatchers if after[n] > before[n]},
}))
'''


def _run_probe() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in sys.path if p and Path(p).is_dir())
    env["PYTHON_GIL"] = "1"
    done = subprocess.run([sys.executable, "-c", _PROBE], env=env,
                          capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, (
        f"probe failed ({done.returncode}):\n{done.stdout}\n{done.stderr}")
    import json
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def probe() -> dict:
    result = _run_probe()
    if "skipped" in result:
        pytest.skip(result["skipped"])
    return result


def test_warm_up_reports_no_failures(probe: dict) -> None:
    """Each warm-up step is guarded separately, so a failure is recoverable --
    but it still means some dispatcher was left cold, which is the hazard."""
    assert probe["warmup_failures"] == []


def test_warm_up_found_the_dispatchers(probe: dict) -> None:
    """Guards against the probe silently inspecting nothing."""
    assert probe["count"] >= 15


def test_no_kernel_compiles_on_a_worker_thread(probe: dict) -> None:
    """No real kernel call may produce a new specialization after import.

    A new signature here means production would compile that dispatcher on
    whatever thread arrived first, concurrently with fifteen others -- which
    has previously corrupted the interpreter three different ways (SIGSEGV,
    glibc "double free or corruption", and a bad-internal-argument
    SystemError) on the 369-case sweep.
    """
    cold = probe["cold"]
    assert not cold, (
        "these dispatchers were compiled at runtime rather than by the "
        "warm-up (name: [signatures_after_import, signatures_after_calls]): "
        f"{cold}. Usually this means the warm-up passes writable arrays "
        "where the kernel is really called with a read-only pinned_view.")
