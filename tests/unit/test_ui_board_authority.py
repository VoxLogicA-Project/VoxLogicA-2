"""The document decides where a card may be; the browser only proposes.

The board keeps its own copy of the placement algebra, and it has to: a drag is
answered every frame under the pointer, and a round trip per frame is not a
thing that can be made to feel right. So the rule is not "the browser has no
algebra" -- it is that the browser's answer is a *drawing*, and the document's
answer is the one that is true.

Which makes the interesting question how the drawing ends. It used to end by
timing out: nothing back within 900ms, so the drop must have been refused. A
guess about a refusal on top of a guess about a placement, and both were wrong
in practice -- a slow answer discarded a placement that had in fact been
applied, which is the snap-home-then-jump the preview exists to prevent, and a
prompt refusal still sat on screen for the rest of the grace period.

The action returns whether it applied. These tests are what makes that answer
load-bearing rather than decorative.

See doc/dev/ui-bento.md section 2, "The preview, and how it ends".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxlogica.ui.document import parse

ROOT = Path(__file__).resolve().parents[2]
BENTO = ROOT / "implementation" / "ui" / "src" / "lib" / "components" / "Bento" / "Bento.svelte"

TWO = """\
//@board cols=12 rows=8
//@card id=a kind=code x=0 y=0 w=4 h=3
let a = 1
//@card id=b kind=code x=5 y=0 w=4 h=3
let b = 2
"""


def board():
    if not BENTO.exists():
        pytest.skip("no UI sources here (running from a wheel)")
    return BENTO.read_text()


# ------------------------------------------------------- the answer exists


def test_an_arrangement_that_does_not_fit_is_declined():
    """The answer the browser now waits for. Without a `False` here there is
    nothing to distinguish a refusal from a slow success."""
    document = parse(TWO)
    assert document.arrange([{"id": "b", "x": 0, "y": 0, "w": 4, "h": 3}]) is False


def test_an_arrangement_that_fits_is_applied():
    document = parse(TWO)
    assert document.arrange([{"id": "b", "x": 5, "y": 4, "w": 4, "h": 3}]) is True


def test_a_declined_arrangement_leaves_the_document_alone():
    """A refusal the board draws must be a refusal the file agrees with, or the
    card snaps back to a position that is not where it is."""
    document = parse(TWO)
    before = document.to_imgql()
    document.arrange([{"id": "b", "x": 0, "y": 0, "w": 4, "h": 3}])
    assert document.to_imgql() == before


def test_the_action_hands_that_answer_back_unchanged():
    """`board.arrange` is what the browser actually calls. An action that
    swallowed the boolean would leave the board inferring again."""
    from voxlogica.ui.actions import ACTIONS
    from voxlogica.ui.workspace import Workspace

    workspace = Workspace(path=None)
    workspace.document = parse(TWO)
    run = ACTIONS["board.arrange"].apply

    assert run(workspace, {"cards": [{"id": "b", "x": 0, "y": 0, "w": 4, "h": 3}]}) is False
    assert run(workspace, {"cards": [{"id": "b", "x": 5, "y": 4, "w": 4, "h": 3}]}) is True


# ---------------------------------------------------- the browser waits for it


def test_every_optimistic_placement_goes_through_one_function():
    """Four gestures commit an arrangement -- a drag, a nudge, a maximize, and a
    restore. Each used to call `onarrange` and walk away, so "check the answer"
    would have been four things to remember. One is not a thing to remember."""
    text = board()
    assert "async function commitArrangement(" in text
    assert text.count("onarrange?.(") == 1, (
        "an arrangement is being sent without going through commitArrangement"
    )


def test_a_refusal_is_read_from_the_result_and_not_from_the_envelope():
    """`invoke` answers `{ok, result}`. A declined placement is `ok: true` with
    `result: false` -- being told "no" is not an error -- so testing `ok` alone
    would treat every refusal as a success and leave the preview on screen."""
    text = board()
    assert "outcome.result !== false" in text
    assert "outcome.ok === true" in text


def test_the_timer_is_no_longer_the_refusal_signal():
    """The 900ms grace period *was* the verdict. It is now only the end of
    waiting for an answer that never comes, which is why it can be generous."""
    import re

    # The code, not the prose: the comment above the timer explains the 900ms
    # this replaced, and a test that reads comments tests the wrong thing.
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", board(), flags=re.S)
    waits = [int(found) for found in re.findall(r"setTimeout\([^,]+,\s*(\d+)\)", text)]
    assert waits, "the safety net is gone entirely; an unanswered send would strand a preview"
    assert all(wait >= 5000 for wait in waits), (
        "a short timer is back, which is a verdict wearing a safety net's clothes"
    )


def test_the_board_still_predicts():
    """The other half of the rule, and worth pinning: this is not an argument
    for a round trip per frame. The preview is the reason the board feels
    attached to the pointer at all."""
    text = board()
    assert "function canPlace(" in text
    assert "let pending = $state({})" in text


def test_the_model_is_written_down():
    """The rule is only enforceable if the next person can find out it exists."""
    doc = (ROOT / "doc" / "dev" / "ui-bento.md").read_text()
    assert "The preview, and how it ends" in doc
    assert "commitArrangement" in doc
