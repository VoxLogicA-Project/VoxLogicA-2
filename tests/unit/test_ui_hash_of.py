"""Selecting a sub-expression and being told whether it is already computed.

The editor is a probe into a content-addressed store: highlight three words, and
the question "has this been worked out before?" has an answer. What makes it
work is that the answer comes from *the reducer* -- the same one the engine
compiles with -- rather than from a second understanding of the grammar written
in JavaScript, which would drift and then answer cache questions wrongly and
silently.
"""

from __future__ import annotations

from voxlogica.ui.results import bindings_for, hash_of
from voxlogica.ui.workspace import Workspace

SOURCE = "let a = 2\nlet b = 3\nlet s = a + b\n"


def test_a_selection_hashes_to_what_the_same_expression_is_bound_to():
    """The whole idea, in one assertion.

    `a + b` selected in the editor is the same node as `let s = a + b`, because
    a node *is* its content. So a selection can find a value somebody else
    computed under another name, yesterday, in another window.
    """
    assert hash_of(SOURCE, "a + b") == bindings_for(SOURCE)["s"]


def test_a_bare_name_is_the_node_it_names():
    assert hash_of(SOURCE, "a") == bindings_for(SOURCE)["a"]


def test_the_context_is_the_document():
    """The hard part of the whole feature.

    `a + b` means different things in different documents, so the same
    selection in two files must not hash the same. Hashing the text on its own
    would have made it.
    """
    other = "let a = 99\nlet b = 3\n"
    assert hash_of(SOURCE, "a + b") != hash_of(other, "a + b")


def test_half_an_expression_is_not_an_expression():
    """The common case while a pointer is moving, and the honest answer."""
    for selection in ("a +", "let", "", "   ", ")("):
        assert hash_of(SOURCE, selection) is None, selection


def test_whitespace_around_a_selection_does_not_change_it():
    """A drag picks up a leading space more often than not."""
    assert hash_of(SOURCE, "  a + b \n") == hash_of(SOURCE, "a + b")


def test_a_name_the_document_does_not_define_has_no_hash():
    assert hash_of(SOURCE, "nowhere") is None


def test_the_probe_name_does_not_start_with_an_underscore():
    """The grammar rejects those, and the first version of this returned None
    for everything because of it -- which reads exactly like "that is not an
    expression", and is therefore the kind of bug you stare through.
    """
    from voxlogica.ui.results import _PROBE

    assert not _PROBE.startswith("_")
    assert bindings_for(f"let {_PROBE} = 1\n"), "the grammar will not take the probe name"


def test_a_selection_naming_the_probe_is_refused():
    """It would hash the probe's own binding rather than the selection."""
    from voxlogica.ui.results import _PROBE

    assert hash_of(SOURCE, _PROBE) is None


# ---------------------------------------------------------------- the action


def test_the_action_answers_with_the_state_too(tmp_path):
    """One round trip: what this is, and whether it is computed."""
    path = tmp_path / "doc.imgql"
    path.write_text(SOURCE)
    from voxlogica.ui.hub import Hub
    from voxlogica.ui.results import Results

    hub = Hub()
    results = Results(hub)
    workspace = Workspace(hub=hub, path=path, results=results)

    answer = workspace.apply("results.hashOf", {"expression": "a + b"})
    assert answer["hash"] == bindings_for(SOURCE)["s"]
    assert answer["state"] == "unknown"

    results.observe(answer["hash"], "done", value=5)
    assert workspace.apply("results.hashOf", {"expression": "a + b"})["state"] == "done"


def test_the_action_answers_nothing_for_a_non_expression(tmp_path):
    path = tmp_path / "doc.imgql"
    path.write_text(SOURCE)
    workspace = Workspace(path=path)
    assert workspace.apply("results.hashOf", {"expression": "a +"}) is None
