"""A sequence must deliver its elements BY POSITION, whatever the finish order.

This is the invariant behind a failure that is far worse than a crash. An
nnU-Net training case is `[case_id, [volume], label]`, unpacked positionally,
and a run died with `expected 2D image data, got shape ()` inside
`write_training_dataset`. Shape `()` is what `np.asarray` returns for a STRING:
the kernel had been handed the case_id where the volume belongs. The sequence
delivered the wrong element, and it did so intermittently -- three occurrences
against a dozen clean runs of the same program.

That it surfaced at all was luck: the slots had different types. In a sweep,
where every element is a float, the same defect returns a wrong NUMBER with no
error anywhere. So these tests assert positional identity directly, under the
conditions that make finish order diverge from index order, and they repeat --
a defect that appears a quarter of the time is indistinguishable from correct
behaviour in a single run, which is exactly how a first diagnosis of this bug
went wrong.
"""

from __future__ import annotations

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

#: Repetitions for the flake hunters. A defect with probability p survives this
#: many independent trials with chance (1-p)^N: at p=0.25 and N=25 that is
#: 0.08%, so a clean pass is evidence rather than luck.
_TRIALS = 25


def _run(program: str, capsys) -> dict[str, str]:
    """Run one program with no store and return its printed bindings."""
    result = ExecutionEngine(storage_backend=None, use_engine=True).execute_workplan(
        reduce_program(parse_program_content(program))
    )
    assert result.success is True, f"run failed: {result}"
    out: dict[str, str] = {}
    for line in capsys.readouterr().out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


# Cost falls as the index rises, so the last element finishes first and finish
# order is close to the reverse of index order. `tagged` carries the index as
# its VALUE while depending on that cost, which is what lets the assertion see
# a permutation rather than merely a delay.
_ORDER_PROGRAM = """
sizes = [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0]
cost(i) = test.timewaste(index(sizes, i), 60)
tagged(i) = i + (cost(i) * 0.0)
print "seq" for i in range(0, 8) do tagged(i)
"""


@pytest.mark.unit
def test_elements_come_back_in_index_order_not_finish_order(capsys):
    out = _run(_ORDER_PROGRAM, capsys)
    seq = [float(v) for v in out["seq"].strip("[]").split(",")]
    assert seq == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


@pytest.mark.unit
def test_index_order_holds_under_repetition(capsys):
    """One clean run proves nothing about an intermittent defect."""
    expected = [float(i) for i in range(8)]
    for trial in range(_TRIALS):
        out = _run(_ORDER_PROGRAM, capsys)
        seq = [float(v) for v in out["seq"].strip("[]").split(",")]
        assert seq == expected, f"permuted on trial {trial}: {seq}"


# The nnU-Net case shape, reduced to its essentials: a heterogeneous triple
# whose slots are told apart by TYPE, so a swap cannot pass unnoticed the way
# it can among floats.
_HETERO_PROGRAM = """
sizes = [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0]
cost(i) = test.timewaste(index(sizes, i), 60)
case(i) = [i + (cost(i) * 0.0), [100.0 + i], 200.0 + i]
cases = for i in range(0, 6) do case(i)
print "ids"     for c in cases do index(c, 0)
print "volumes" for c in cases do index(index(c, 1), 0)
print "labels"  for c in cases do index(c, 2)
"""


@pytest.mark.unit
def test_a_heterogeneous_triple_keeps_each_slot_in_its_place(capsys):
    """`[id, [volume], label]`: the exact shape that mis-delivered."""
    for trial in range(_TRIALS):
        out = _run(_HETERO_PROGRAM, capsys)
        ids = [float(v) for v in out["ids"].strip("[]").split(",")]
        volumes = [float(v) for v in out["volumes"].strip("[]").split(",")]
        labels = [float(v) for v in out["labels"].strip("[]").split(",")]
        assert ids == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], f"trial {trial}: {ids}"
        assert volumes == [100.0 + i for i in range(6)], f"trial {trial}: {volumes}"
        assert labels == [200.0 + i for i in range(6)], f"trial {trial}: {labels}"


# Two-level indexing on its own. `index(index(s, i), 0)` is where a
# short-cut-fusion rule that pushed a slice through the wrong producer would
# collapse onto the outer level and silently return a neighbour.
_NESTED_PROGRAM = """
inner(i) = [i * 10.0, i * 10.0 + 1.0, i * 10.0 + 2.0]
outer = for i in range(0, 5) do inner(i)
print "first"  for s in outer do index(s, 0)
print "second" for s in outer do index(s, 1)
print "third"  for s in outer do index(s, 2)
"""


@pytest.mark.unit
def test_nested_indexing_addresses_the_inner_level(capsys):
    out = _run(_NESTED_PROGRAM, capsys)
    first = [float(v) for v in out["first"].strip("[]").split(",")]
    second = [float(v) for v in out["second"].strip("[]").split(",")]
    third = [float(v) for v in out["third"].strip("[]").split(",")]
    assert first == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert second == [1.0, 11.0, 21.0, 31.0, 41.0]
    assert third == [2.0, 12.0, 22.0, 32.0, 42.0]


@pytest.mark.unit
def test_the_same_program_gives_the_same_answer_every_time(capsys):
    """Determinism is the property a wrong-element defect breaks first.

    Nothing here is timing-dependent by design, so any variation across runs is
    a scheduling artefact reaching the results -- which is the whole class of
    defect this file exists for, stated without reference to any particular
    mechanism.
    """
    reference = _run(_HETERO_PROGRAM, capsys)
    for trial in range(_TRIALS):
        assert _run(_HETERO_PROGRAM, capsys) == reference, f"diverged on trial {trial}"


# ── The same invariants, with a store in the loop ───────────────────────────
#
# Everything above runs with no backend, and passes. The failure being chased
# had one (`--store-db`), which adds three things the tests above cannot reach:
# values leave RAM and come back through `_rematerialize`, payloads are written
# and read by different threads, and a warm re-run answers from disk instead of
# recomputing. A wrong element could enter at any of the three, and only the
# first is visible without a backend.


def _run_with_store(program: str, db_path, capsys) -> dict[str, str]:
    from voxlogica.storage import SQLiteResultsDatabase

    backend = SQLiteResultsDatabase(db_path=str(db_path))
    try:
        result = ExecutionEngine(storage_backend=backend, use_engine=True).execute_workplan(
            reduce_program(parse_program_content(program))
        )
        assert result.success is True, f"run failed: {result}"
    finally:
        backend.close()
    out: dict[str, str] = {}
    for line in capsys.readouterr().out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


# Strings in slot 0, because the observed failure delivered a STRING where an
# image belonged: a string and a volume take different paths through the codec,
# and a test made only of floats cannot tell a payload mix-up from a correct
# answer.
_TYPED_PROGRAM = """
sizes = [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0]
cost(i) = test.timewaste(index(sizes, i), 60)
name(i) = concat("case_", format_string("{:.0f}", i + (cost(i) * 0.0)))
case(i) = [name(i), [100.0 + i], 200.0 + i]
cases = for i in range(0, 6) do case(i)
print "ids"     for c in cases do index(c, 0)
print "volumes" for c in cases do index(index(c, 1), 0)
print "labels"  for c in cases do index(c, 2)
"""


@pytest.mark.unit
def test_a_typed_triple_survives_a_cold_store(tmp_path, capsys):
    """Slot 0 is a string: a swap shows up as a type, not as a plausible number."""
    expected_ids = "[" + ", ".join(f"case_{i}" for i in range(6)) + "]"
    for trial in range(8):
        out = _run_with_store(_TYPED_PROGRAM, tmp_path / f"cold{trial}.db", capsys)
        assert out["ids"].replace("'", "").replace('"', "") == expected_ids, f"trial {trial}"
        volumes = [float(v) for v in out["volumes"].strip("[]").split(",")]
        assert volumes == [100.0 + i for i in range(6)], f"trial {trial}: {volumes}"


@pytest.mark.unit
def test_a_typed_triple_survives_a_warm_store(tmp_path, capsys):
    """The second run answers from disk, which is a different code path entirely."""
    db = tmp_path / "warm.db"
    reference = _run_with_store(_TYPED_PROGRAM, db, capsys)
    for trial in range(8):
        assert _run_with_store(_TYPED_PROGRAM, db, capsys) == reference, f"trial {trial}"


@pytest.mark.unit
def test_a_squeezed_budget_does_not_change_the_answer(tmp_path, monkeypatch, capsys):
    """Force eviction on every value, so every read is a rematerialisation.

    A wrong element that only appears when a value has been dropped and rebuilt
    is invisible at any comfortable budget, and the memory governor now moves
    the budget on its own -- so the answer must not depend on where it lands.
    """
    reference = _run_with_store(_TYPED_PROGRAM, tmp_path / "ref.db", capsys)
    monkeypatch.setenv("VOXLOGICA_MAX_LIVE_GB", "0.001")
    for trial in range(8):
        assert _run_with_store(_TYPED_PROGRAM, tmp_path / f"tiny{trial}.db", capsys) == reference, (
            f"answer changed under memory pressure on trial {trial}")
