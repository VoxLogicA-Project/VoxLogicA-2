"""Keep ITK's own per-filter thread pool from oversubscribing the engine's.

There are TWO independent parallelism layers in a run, and by default neither
knows about the other:

- the engine dispatches up to ``max_concurrency`` kernels concurrently;
- *each* SimpleITK filter call internally spreads itself over ITK's global
  default thread count, which is every logical CPU unless told otherwise.

Uncapped, a run with W engine workers on a C-core box can therefore have
W x C native threads live, all contending for C cores and synchronizing
through locks neither layer coordinates. Measured cost of that on this
codebase: at 12 engine workers on an 18-core Mac under free-threaded CPython,
the benchmark took 318.7s and consumed only 488 CPU-seconds -- about 1.5 cores
busy out of 18. Wall time went UP 4.3x versus the GIL build while CPU went
DOWN, the signature of threads blocked on locks rather than computing.

Why this is a free-threading *prerequisite* and not a tuning nicety: under the
GIL this damage was accidentally contained, because the GIL serialized
Python-level dispatch and so throttled how many ITK pools were live at once.
Removing the GIL removes that accidental throttle, and the two layers thrash
freely. doc/dev/free-threaded-handover.md sec 5a saw the same effect on Linux
(kernel/futex wait+wake 14-36% of all cycles, cut ~3.5-4x by capping) but
read it as a ~9% optimization; on a hybrid-core Mac it is the difference
between free-threading being a win and being a severe regression.

On the arithmetic here, which deserves a note because ``engine/topology.py``
exists precisely to *refute* per-core arithmetic as a way to choose thread
counts: that refutation is about picking W, the engine's worker count, whose
optimum depends on memory bandwidth and must be measured (see
``engine/calibration.py``). It does not apply to this cap. Here W is already
fixed -- calibrated, or given with --threads -- and the only job left is to
stop the second layer from multiplying it. ``C // W`` is the invariant
"total native threads ~= cores", not a guess about where bandwidth saturates.
It also reproduces the one point that WAS measured independently: at 24
workers on 24 cores it yields 1, which is the value the handover's A/B found
best.
"""

from __future__ import annotations

import os
import sys

# ITK reads this at library initialization, so setting it before SimpleITK is
# first imported is enough; after that only the API call takes effect.
_ITK_ENV = "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"
_OVERRIDE_ENV = "VOXLOGICA_ITK_THREADS"


def itk_threads_for(engine_workers: int, cpu_count: int | None = None) -> int:
    """Threads to allow each ITK filter, given the engine's worker count."""
    cores = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(1, cores // max(1, engine_workers))


def configure_itk_threads(engine_workers: int) -> int | None:
    """Cap ITK's global default thread count to match ``engine_workers``.

    Returns the value applied, or None if an explicit external setting was
    left alone. Deliberately does NOT import SimpleITK: on a run that never
    touches a vox1 primitive, importing it would cost seconds for nothing.
    Setting the environment variable is sufficient whenever ITK has not
    initialized yet, which is the normal case (primitive namespaces load
    lazily, after the engine is constructed); if SimpleITK *is* already
    imported, the env var would be ignored, so the API is called too.
    """
    # An explicit ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS is a deliberate choice
    # by whoever launched the process (run_iter.sh sets it, and so does any
    # A/B measuring this very knob) -- never silently override it.
    if os.environ.get(_ITK_ENV, "").strip():
        return None

    override = os.environ.get(_OVERRIDE_ENV, "").strip()
    if override:
        try:
            threads = max(1, int(override))
        except ValueError:
            print(
                f"WARNING: ignoring non-integer {_OVERRIDE_ENV}={override!r}",
                file=sys.stderr,
            )
            threads = itk_threads_for(engine_workers)
    else:
        threads = itk_threads_for(engine_workers)

    os.environ[_ITK_ENV] = str(threads)

    sitk = sys.modules.get("SimpleITK")
    if sitk is not None:  # already initialized: the env var alone is too late
        try:
            sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(threads)
        except Exception as e:  # pragma: no cover - older SimpleITK
            print(f"WARNING: could not cap ITK thread count: {e}", file=sys.stderr)

    return threads
