"""Apply an ITK thread-pool size someone else decided on.

A previous version of this module CHOSE the value (`cores // workers`) and was
reverted: measured on fmt-5000 it was up to 3.1x slower than leaving ITK alone,
because the right value is not a formula -- it interacts with worker count with
a crossover (itk=24 wins at 8 workers, itk=1 wins at 18) and, per
manuscripts/engine-scaling-2026-07.md Part II sec 10-11, depends on how
memory-bound the specific workload and core mix are, which only measurement can
tell. See engine/calibration.py, which now sweeps this value the same way it
already swept worker count.

So this module does no picking. It only applies a value engine/calibration.py
measured and cached for THIS exact (machine, worker count), or a value a
calibration sweep is testing candidate-by-candidate. If nothing has been
calibrated, callers should not call this at all -- leaving ITK at its own
default is the safe, measured-not-harmful baseline (Part I sec 4: 68-76s
across every worker count, never the worst option, just not always the best).
"""

from __future__ import annotations

import os
import sys

# ITK reads this once at library init; setting it before SimpleITK is first
# imported is sufficient. After that only the API call below still works.
ITK_THREADS_ENV = "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"


def apply_itk_threads(n: int) -> None:
    """Set ITK's global default thread count to exactly ``n``.

    Sets the env var (effective before SimpleITK's first import) and calls the
    API directly if SimpleITK is already loaded (the env var alone is too late
    by then). Does not consult the environment for an existing override or
    apply any formula -- the caller (calibration's sweep, or the engine
    applying a cached result) is the sole source of the value.
    """
    os.environ[ITK_THREADS_ENV] = str(n)
    sitk = sys.modules.get("SimpleITK")
    if sitk is not None:
        try:
            sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(n)
        except Exception as e:  # pragma: no cover - older SimpleITK without the setter
            print(f"WARNING: could not set ITK thread count: {e}", file=sys.stderr)
