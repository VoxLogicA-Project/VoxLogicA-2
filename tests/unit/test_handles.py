"""A sequence costs what its hashes cost, not what its elements cost.

This is the acceptance test for issue #51, as arithmetic rather than as a
ten-hour training run. A 309-case nnU-Net training was OOM-killed after six
hours because `for g in train do triple(g)` gathered 309 volumes into one list
value -- 51.4 GB, held from second 30 to the kill, with every route out of RAM
closed:

  * it could not be evicted, because a sequence is not `_recomputable`;
  * it could not be spilled, because a sequence of images has no JSON-native
    form and the encoder refuses to inline one;
  * and it could not be released, because its consumer ran for ten hours.

`default.sequence`'s kernel is `[value for _index, value in ordered]`. It never
looks inside what it is given. Given handles it does the same work on hashes.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from voxlogica.execution import ExecutionEngine
from voxlogica.handles import Handle, HANDLE_TAG, iter_handles, resolve_deep, revive_handles
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program


# --- the representation ----------------------------------------------------


def test_a_handle_is_a_hash_and_nothing_else():
    """No resolver, no table, no cached value -- so it is safe to persist."""
    handle = Handle("a" * 64)

    assert handle.node == "a" * 64
    assert handle == Handle("a" * 64)          # by value, like the hash it is
    assert not hasattr(handle, "resolve")
    assert not hasattr(handle, "value")


def test_a_handle_survives_the_form_it_is_stored_in():
    stored = {HANDLE_TAG: "b" * 64}

    assert revive_handles(stored) == Handle("b" * 64)
    assert revive_handles([stored, {"not": "a handle"}]) == [
        Handle("b" * 64), {"not": "a handle"}]


def test_a_dict_that_merely_mentions_the_tag_is_not_a_handle():
    """The tag is a reserved key, so a program's own dict cannot be mistaken."""
    assert revive_handles({HANDLE_TAG: "c" * 64, "and": "more"}) == {
        HANDLE_TAG: "c" * 64, "and": "more"}


# --- the eager adapter -----------------------------------------------------


def test_a_value_with_no_handle_is_returned_as_is_not_rebuilt():
    """Declaring nothing must cost nothing: no allocation, no copy."""
    original = [1, [2, 3], {"k": "v"}]

    def must_not_be_called(node_id):
        raise AssertionError("nothing to resolve")

    assert resolve_deep(original, must_not_be_called) is original


def test_handles_are_replaced_wherever_they_are_nested():
    value = [Handle("d" * 64), {"k": (Handle("e" * 64), 7)}]

    assert resolve_deep(value, lambda node: node[0]) == ["d", {"k": ("e", 7)}]


def test_every_handle_in_a_value_is_found():
    value = {"a": [Handle("f" * 64)], "b": Handle("0" * 64)}

    assert sorted(h.node for h in iter_handles(value)) == ["0" * 64, "f" * 64]


# --- what it buys ----------------------------------------------------------


_SEQUENCE = 'a = [1, 2, 3, 4, 5, 6, 7, 8]\nprint "a" a'


def _run(program: str):
    engine = ExecutionEngine(storage_backend=None, use_engine=True)
    return engine.execute_workplan(reduce_program(parse_program_content(program)))


def test_a_sequence_still_reads_as_its_values(capsys):
    """The change must be invisible from outside. Values, not @deadbeef."""
    result = _run(_SEQUENCE)
    printed = {line.partition("=")[0].strip(): line.partition("=")[2].strip()
               for line in capsys.readouterr().out.splitlines() if "=" in line}

    assert result.success is True
    assert [float(v) for v in printed["a"].strip("[]").split(",")] == [
        1, 2, 3, 4, 5, 6, 7, 8]


def test_the_gathering_node_holds_hashes_not_elements(monkeypatch):
    """The acceptance test, as arithmetic.

    The sequence node's own value must be a list of handles. On `incoming` it is
    a list of the elements themselves, which for 309 BraTS cases was 51.4 GB
    that nothing could evict, spill or release.
    """
    import voxlogica.engine.strategy as strategy_module

    seen: list = []
    original = strategy_module.ComputationEngine

    class Captured(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self)

    monkeypatch.setattr(strategy_module, "ComputationEngine", Captured)

    assert _run(_SEQUENCE).success is True
    assert seen, "no engine was built"

    table = seen[0].table
    sequences = [nid for nid, node in table.nodes.items()
                 if node.operator in ("default.sequence", "sequence")
                 and nid in table.values]
    assert sequences, "the program built no sequence"
    for nid in sequences:
        value = table.values[nid]
        assert isinstance(value, list), value
        assert value and all(isinstance(item, Handle) for item in value), value


# --- the conditional, as proof that rewrite is general ----------------------


_IF_PROGRAM = '''
taken = if(1, 10, 20)
skipped = if(0, 10, 20)
print "taken" taken
print "skipped" skipped
'''


def test_a_conditional_takes_a_branch(capsys):
    result = _run(_IF_PROGRAM)
    printed = {line.partition("=")[0].strip(): line.partition("=")[2].strip()
               for line in capsys.readouterr().out.splitlines() if "=" in line}

    assert result.success is True, result
    assert float(printed["taken"]) == 10.0
    assert float(printed["skipped"]) == 20.0


def test_the_untaken_branch_is_never_computed(monkeypatch, capsys):
    """The whole point. `vox1/compat.imgql` had to compute both and mask them."""
    import voxlogica.engine.strategy as strategy_module

    seen: list = []
    original = strategy_module.ComputationEngine

    class Captured(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self)

    monkeypatch.setattr(strategy_module, "ComputationEngine", Captured)

    # The branches are distinguishable by their VALUE, and only one is taken.
    assert _run('print "x" if(1, 111, 222)').success is True
    table = seen[0].table
    computed = {v for nid, v in table.values.items() if isinstance(v, float)}

    assert 111.0 in computed, "the taken branch was not computed"
    assert 222.0 not in computed, "the untaken branch was computed anyway"


def test_filter_runs_at_all(capsys):
    """It could not. The closure's value is None here, so the kernel raised."""
    result = _run('kept = filter i in [1, 2, 3, 4, 5] do i > 2\nprint "k" kept')
    printed = {line.partition("=")[0].strip(): line.partition("=")[2].strip()
               for line in capsys.readouterr().out.splitlines() if "=" in line}

    assert result.success is True, result
    assert [float(v) for v in printed["k"].strip("[]").split(",")] == [3.0, 4.0, 5.0]


def test_filter_keeps_nothing_and_everything(capsys):
    result = _run('a = filter i in [1, 2] do i > 9\n'
                  'b = filter i in [1, 2] do i > 0\n'
                  'print "a" a\nprint "b" b')
    printed = {line.partition("=")[0].strip(): line.partition("=")[2].strip()
               for line in capsys.readouterr().out.splitlines() if "=" in line}

    assert result.success is True, result
    assert printed["a"] == "[]"
    assert [float(v) for v in printed["b"].strip("[]").split(",")] == [1.0, 2.0]


def test_gather_selects_among_handles_not_values():
    """Filtering N things must not cost N things."""
    from voxlogica.primitives.default import gather

    kept = gather.execute(**{"0": [True, False, True],
                             "1": [Handle("a" * 64), Handle("b" * 64), Handle("c" * 64)]})

    assert kept == [Handle("a" * 64), Handle("c" * 64)]


def test_gather_refuses_a_length_it_cannot_pair():
    from voxlogica.primitives.default import gather

    with pytest.raises(ValueError):
        gather.execute(**{"0": [True], "1": [1, 2, 3]})


def test_registering_what_a_rewriter_made_does_not_recurse():
    """The walk's depth is the depth of what was built, and a fold's chain is
    as deep as its sequence is long.

    Python has no tail calls and this engine builds millions of nodes in one
    process, so a recursive walk would hit the stack limit on a fold of a few
    thousand elements. Five thousand links here, against a default limit of
    about one thousand frames.
    """
    from voxlogica.engine.core import ComputationEngine
    from voxlogica.lazy.ir import NodeSpec

    engine = ComputationEngine()
    seed = engine.table.intern(
        NodeSpec(kind="constant", operator="constant", attrs={"value": 0}))
    accumulator = seed
    for step in range(5000):
        element = engine.table.intern(
            NodeSpec(kind="constant", operator="constant", attrs={"value": step}))
        accumulator = engine.table.intern(
            NodeSpec(kind="primitive", operator="default.combine",
                     args=(accumulator, element), attrs={"operator": "+"}))

    engine._register_new_subtree(accumulator, 0)     # must not raise

    assert accumulator in engine.graph.incomplete


def test_a_loop_whose_body_is_a_constant_still_drains_its_window(capsys):
    """`for i in xs do i` past the admission window used to deadlock forever.

    A body that reduces to the element itself is a CONSTANT, and constants are
    completed at DISCOVERY instead of through the ready queue -- so they never
    reached the hook that decrements a loop job's in-flight count. The window
    filled and never drained: sixteen bodies admitted, the seventeenth never,
    every worker asleep and the event loop in `select()` with nothing to wake it.

    Reproduced exactly at the boundary -- sixteen finished, seventeen hung -- so
    the size here is one past the default window rather than a number chosen to
    look safe. Nothing to do with handles; found while measuring a fold, and it
    had been misattributing that fold's results for a day.
    """
    n = 17
    result = _run('xs = for i in range(0, %d) do i\nprint "s" fold + xs' % n)
    printed = {line.partition("=")[0].strip(): line.partition("=")[2].strip()
               for line in capsys.readouterr().out.splitlines() if "=" in line}

    assert result.success is True, result
    assert float(printed["s"]) == float(sum(range(n)))


# --- compression: zstd where it is available, gzip where it is not -----------


def test_a_payload_round_trips_through_whichever_codec_is_present():
    from voxlogica.pod_codec import _compress, _decompress

    raw = (b"\x00" * 4096 + b"mask") * 64

    assert _decompress(_compress(raw)) == raw


def test_a_gzip_payload_from_an_older_store_still_decodes():
    """Reads are self-describing, which is what makes changing the codec safe."""
    import gzip

    from voxlogica.pod_codec import _decompress

    raw = b"an older store wrote this" * 100

    assert _decompress(gzip.compress(raw, 1)) == raw


def test_an_uncompressed_payload_passes_through():
    from voxlogica.pod_codec import _decompress

    assert _decompress(b"neither magic") == b"neither magic"
    assert _decompress(None) == b""


def test_zstd_is_used_when_it_is_installed():
    from voxlogica.pod_codec import _ZSTD_MAGIC, _compress, _zstd

    if _zstd() is None:
        pytest.skip("zstandard is not installed in this environment")
    assert _compress(b"x" * 10_000)[:4] == _ZSTD_MAGIC
