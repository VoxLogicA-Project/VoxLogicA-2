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


# --- the payload store, addressed by content --------------------------------


def test_identical_payloads_are_stored_once(tmp_path):
    """Two nodes with the same bytes used to write two files.

    A sweep that varies one parameter recomputes the same mask under a dozen
    expressions; naming a payload by its node meant a dozen copies.
    """
    import hashlib

    from voxlogica.storage import ResultsStore, results_store_paths

    db, payload_dir = results_store_paths(tmp_path / "s.db")
    store = ResultsStore(str(db))
    try:
        payload = b"the same bytes" * 100
        first = store._store_payload(payload)
        second = store._store_payload(payload)

        assert first == second
        digest = hashlib.sha256(payload).hexdigest()
        assert first == f"{digest[:2]}/{digest[2:]}.bin"
        assert (payload_dir / first).read_bytes() == payload
        assert sum(1 for _ in payload_dir.rglob("*.bin")) == 1
    finally:
        store.close()


def test_a_payload_is_named_by_what_it_is_not_by_who_asked(tmp_path):
    from voxlogica.storage import ResultsStore, results_store_paths

    db, _ = results_store_paths(tmp_path / "s.db")
    store = ResultsStore(str(db))
    try:
        one = store._store_payload(b"alpha")
        other = store._store_payload(b"beta")

        assert one != other
        assert "/" in one and one.endswith(".bin")   # sharded, two digits deep
    finally:
        store.close()


def test_no_partial_file_survives_a_failed_write(tmp_path, monkeypatch):
    """A cache must never poison the run that inherits it."""
    from voxlogica.storage import ResultsStore, results_store_paths

    db, payload_dir = results_store_paths(tmp_path / "s.db")
    store = ResultsStore(str(db))
    try:
        def die(self, data):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_bytes", die)
        with pytest.raises(OSError):
            store._store_payload(b"never lands")

        assert list(payload_dir.rglob("*.part")) == []
        assert list(payload_dir.rglob("*.bin")) == []
    finally:
        store.close()
