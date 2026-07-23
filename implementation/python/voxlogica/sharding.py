"""Automatic multi-process goal sharding for the ``run`` CLI command.

WHY: a single engine process is GIL-capped at roughly half of a machine's
physical cores — the ready queue is never starved (see engine/core.py's
metrics), so the ceiling is the GIL holding cheap Python kernel dispatch, not
scheduling. Goals in a typical batch program (one `print` per independent
case, e.g. `print "cNNN_B" index(oB,NNN)` for NNN in a runtime loop) are
usually mutually independent, so running P engine processes over disjoint
goal subsets — each with its own GIL — reaches full core utilization with NO
engine changes.

This module is pure orchestration: it forks `python -m voxlogica.main run`
subprocesses (each a completely normal, independent CLI invocation) and never
touches engine/scheduler internals. Correctness rests on two facts that are
already true of the engine, not on anything this module adds:
  1. Goal evaluation is pure and deterministic — the same goal against the
     same source always produces the same value, regardless of what else is
     in the plan or which process computes it.
  2. Reduction is deterministic and content-addressed (Merkle hashing), so
     every subprocess re-reducing the SAME source file gets byte-identical
     goal ids in byte-identical declaration order — no ids need to cross a
     process boundary; each child just re-derives "its slice" from (shard
     index, shard total), a lightweight recipe passed as two integers.

GOAL CLASSIFICATION (why some goals can't be sharded): a goal like
`index(oB, 5)` only needs one element of a runtime-loop sequence — sharding
it is exactly the case above. A goal like `avg(s) = fold(...)  ...; print
"avg" avg(oB)` needs EVERY element of the same sequence — handing it to one
shard would force that shard alone to compute the whole sequence, work every
OTHER shard is redundantly doing too. So goals are split into:
  - SHARDABLE: reduces to `index(<sequence>, <compile-time constant>)` —
    needs exactly one runtime-loop element.
  - AGGREGATE: everything else — needs to run once, after every shard has
    finished, against the now fully-warm shared cache (so it's a cheap
    reload of already-computed+persisted per-element results, not a
    recompute — see `run_sharded_program`'s finalize pass).
This is a conservative, fail-safe heuristic: a goal that doesn't match the
`index(seq, const)` shape is simply treated as aggregate (runs once, single-
process, in the finalize pass) — never silently wrong, at worst not sped up.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass

from voxlogica.lazy.ir import GoalSpec, NodeId, SymbolicPlan

GIL_EFFECTIVE_CORES = 12  # measured: one process saturates ~this many cores under the GIL
MIN_SHARDABLE_GOALS = 8   # below this, forking overhead isn't worth it


def is_indexed_projection(nodes: dict[NodeId, "object"], goal_id: NodeId) -> bool:
    """True if this goal is `index(<sequence>, <constant>)` — one loop element.

    Walks through the trivial case where the goal id *is* the index call
    directly (the common shape: `print "cNNN" index(oB,NNN)`). Does not chase
    through wrapper functions — the reducer inlines those, so a goal's own
    node already reflects the real call after reduction.
    """
    node = nodes.get(goal_id)
    if node is None or node.kind != "primitive":
        return False
    if node.operator not in ("default.index", "index"):
        return False
    if len(node.args) != 2:
        return False
    _seq_id, idx_id = node.args
    idx_node = nodes.get(idx_id)
    return idx_node is not None and idx_node.kind == "constant"


def classify_goals(plan: SymbolicPlan) -> tuple[list[GoalSpec], list[GoalSpec]]:
    """Split a plan's goals into (shardable, aggregate), preserving order."""
    shardable, aggregate = [], []
    for goal in plan.goals:
        (shardable if is_indexed_projection(plan.nodes, goal.id) else aggregate).append(goal)
    return shardable, aggregate


def detect_cpu_count() -> int:
    return os.cpu_count() or 8


def detect_ram_gb() -> float:
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return int(out.stdout.strip()) / (1024 ** 3)
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    import re
                    return int(re.search(r"(\d+)", line).group(1)) / 1024 / 1024
    except Exception:
        pass
    return 16.0  # conservative fallback; only affects the auto shard-count guess


def pick_shard_count(cpu_count: int, shardable_goal_count: int, ram_gb: float,
                     per_process_min_gb: float = 2.0, override: int | None = None) -> int:
    """Auto-pick P: enough to use all cores under the GIL, capped by RAM and goal count."""
    if override is not None:
        return max(1, override)
    if shardable_goal_count < MIN_SHARDABLE_GOALS:
        return 1  # not worth the fork overhead
    by_gil = max(2, cpu_count // GIL_EFFECTIVE_CORES)
    by_ram = max(1, int((ram_gb * 0.7) // per_process_min_gb))
    return max(1, min(by_gil, by_ram, shardable_goal_count))


def split_goals(goals: list[GoalSpec], p: int) -> list[list[GoalSpec]]:
    """Contiguous, size-balanced split — order doesn't matter for correctness."""
    if p <= 1:
        return [goals]
    base, extra = divmod(len(goals), p)
    shards, start = [], 0
    for i in range(p):
        size = base + (1 if i < extra else 0)
        shards.append(goals[start:start + size])
        start += size
    return [s for s in shards if s]  # drop empty shards (more P than goals)


@dataclass
class ShardPlan:
    """What auto-sharding decided to do, for the caller to act on and report."""
    enabled: bool
    reason: str
    shard_count: int = 1
    threads_per_shard: int = 0
    shardable_goal_count: int = 0
    aggregate_goal_count: int = 0


def decide(plan: SymbolicPlan, *, no_cache: bool, explicit_shards: int | None,
          profile_requested: bool, is_child_invocation: bool) -> ShardPlan:
    """The single decision point: should this `run` invocation auto-shard?

    Fails safe toward "no" (ordinary single-process execution, today's
    behavior) whenever sharding wouldn't be correct or wouldn't help:
    already inside a forked child, no persistent cache to share state through,
    profiling was explicitly requested (a precise single-process view is the
    point), or an override of 1 was given.
    """
    if is_child_invocation:
        return ShardPlan(enabled=False, reason="child invocation")
    if explicit_shards == 1:
        return ShardPlan(enabled=False, reason="--shards 1")
    if no_cache:
        return ShardPlan(enabled=False, reason="--no-cache (no persistent store to share across processes)")
    if profile_requested:
        return ShardPlan(enabled=False, reason="--profile requested (single-process view)")

    shardable, aggregate = classify_goals(plan)
    cpu_count = detect_cpu_count()
    ram_gb = detect_ram_gb()
    p = pick_shard_count(cpu_count, len(shardable), ram_gb, override=explicit_shards)
    if p <= 1:
        reason = (f"only {len(shardable)} shardable goals (< {MIN_SHARDABLE_GOALS})"
                  if explicit_shards is None else "computed shard count is 1")
        return ShardPlan(enabled=False, reason=reason,
                         shardable_goal_count=len(shardable), aggregate_goal_count=len(aggregate))
    return ShardPlan(enabled=True, reason=f"{len(shardable)} shardable goals across {cpu_count} cores",
                     shard_count=p, threads_per_shard=max(1, cpu_count // p),
                     shardable_goal_count=len(shardable), aggregate_goal_count=len(aggregate))
