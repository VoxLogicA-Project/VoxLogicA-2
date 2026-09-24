"""A loop node offered to the workers twice starts ONE expansion job.

The ready queue is a suggestion, not property (see a313172): the same loop id
can be popped by two workers, and neither `completed` nor `is_running` tells them
apart, because a node handed to admission never goes through `table.begin`. A
second `_run_job` for the same id used to overwrite the first's `_jobs` entry;
whichever finished first popped the key, and the others slept on a wake nobody
could send. Measured: run the same three-line program twice against one store
and the second run delivered nothing -- six replays, six stalls.

The guard must see the first start BEFORE the job's task has run its first step,
which is why `start` registers the job itself instead of leaving that to the
coroutine. Both calls below happen in one turn, with no await in between: that
is exactly the window the coroutine-side registration left open.
"""

from __future__ import annotations

import asyncio

import pytest

from voxlogica.engine.core import ComputationEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

PROGRAM = """
let xs = for i in range(0, 4) do +(i, 1)
print "first" index(xs, 0)
"""


@pytest.mark.unit
def test_a_loop_offered_twice_starts_one_job() -> None:
    engine = ComputationEngine(backend=None)
    engine.adopt_plan(reduce_program(parse_program_content(PROGRAM)))
    loop = next(nid for nid, node in engine.table.nodes.items()
                if node.operator in ("default.for_loop", "for_loop"))
    node = engine.table.nodes[loop]

    async def scenario() -> None:
        before = engine.ready.outstanding
        engine.admission.start(loop, node, 0)
        engine.admission.start(loop, node, 0)   # the duplicate offer, same turn
        assert engine.admission.active_jobs == 1, "one job per loop id, registered at once"
        assert engine.ready.outstanding == before + 1, "and exactly one unit taken for it"
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

    asyncio.run(scenario())
