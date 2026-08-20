"""A volume reaches the viewer as the file it is.

Three things had to be true at once for a card to draw a volume, and none of
them was. Each is a separate hole, and each closed one:

1. the engine hands a volume over as an encoded file with no type beside it, so
   `describe` called it "bytes" and the viewer table has no row for that;
2. the description that reaches a card deliberately drops the value -- nobody
   wants a volume down a websocket -- and for a node the store did not persist,
   that description was the only copy left, so `/api/node/<hash>` answered 404
   about a node that was plainly done;
3. and the browser addressed those bytes by the card's `node`, which is a *name*
   in a document (`v`), while the route is addressed by content.

The end-to-end proof is a screenshot -- a WebGL drawing buffer reads back empty
once the frame is composited, so counting lit pixels answers the wrong question
-- and it lives outside pytest because it needs a browser. What is here is
everything below the browser, which is where all three faults actually were.
"""

from __future__ import annotations

import gzip
import json

import pytest

from voxlogica.ui.results import Results, _format_of, describe

pytest.importorskip("SimpleITK")


@pytest.fixture
def volume(tmp_path):
    """A small real NIfTI, as bytes. Real because the whole question is whether
    the format identifies itself, and a handmade header would be us agreeing
    with ourselves."""
    import numpy as np
    import SimpleITK as sitk

    path = tmp_path / "v.nii.gz"
    data = (np.arange(4 * 5 * 6, dtype="float32").reshape(6, 5, 4)) / 7.0
    sitk.WriteImage(sitk.GetImageFromArray(data), str(path))
    return path.read_bytes()


# --------------------------------------------------------------- identifying


def test_a_nifti_says_what_it_is(volume):
    """Reading a magic number, not guessing from a file extension nobody sent."""
    assert _format_of(volume) == "image"
    assert describe(volume)[1] == "image"


def test_a_gzip_of_something_else_is_not_an_image():
    """The cheap version of this sniffed for gzip and called it a volume. Most
    things that are gzipped are not volumes."""
    assert _format_of(gzip.compress(b"hello" * 400)) == "bytes"


def test_bytes_that_are_nothing_in_particular_stay_bytes():
    assert _format_of(b"just some bytes") == "bytes"
    assert _format_of(b"") == "bytes"


def test_a_truncated_volume_does_not_raise():
    """Naming a value must never be the thing that fails: a card would then show
    an error about a computation that succeeded."""
    assert _format_of(b"\x1f\x8b" + b"\x00" * 50) in ("image", "bytes")


def test_the_description_leaves_the_bytes_out(volume):
    """It travels to every subscribed browser. A volume must not."""
    value, kind, summary = describe(volume)
    assert value is None
    assert kind == "image"
    assert "bytes" in summary
    # And it must survive the trip: a description is JSON on a websocket.
    json.dumps({"value": value, "valueType": kind, "summary": summary})


# ------------------------------------------------------------------- serving


class Hub:
    """Enough hub to build a Results. It publishes nowhere, which is the point:
    these are questions about what is *kept*, not about what is sent."""

    def publish(self, *args, **kwargs) -> None:
        pass


def test_an_observed_volume_can_still_be_handed_over(volume):
    """The fault behind the 404. The store is asked first and is allowed not to
    have it -- values the engine folds or never persists are exactly the ones a
    card is left staring at."""
    results = Results(Hub())
    results.observe("a" * 64, "done", value=volume)

    assert results.state_of("a" * 64)["valueType"] == "image"
    served = results.bytes_of("a" * 64)
    assert served is not None
    data, filename = served
    assert data == volume, "what a viewer draws must be what the engine produced"
    assert filename.endswith(".nii.gz"), "NiiVue reads the name to pick a reader"


def test_the_store_is_still_preferred_when_it_has_it(volume):
    """The kept copy is a convenience over the store, not a second store: if the
    store answers, that is the answer."""
    results = Results(Hub(), fetch=lambda node: volume)
    results.observe("b" * 64, "done", value=b"\x1f\x8bstale")
    data, _ = results.bytes_of("b" * 64)
    assert data == volume


def test_what_is_kept_is_bounded(volume, monkeypatch):
    """A long session sees every intermediate a program produces. Holding all of
    them would make a board's memory a function of how long it has been open."""
    from voxlogica.ui import results as module

    monkeypatch.setattr(module, "_PAYLOAD_BUDGET", len(volume) * 2)
    results = Results(Hub())
    for n in range(6):
        results.observe(f"{n}" * 64, "done", value=volume + bytes([n]))

    kept = [node for node in (f"{n}" * 64 for n in range(6)) if results.bytes_of(node)]
    assert len(kept) <= 2, "the budget is not being enforced"
    assert "5" * 64 in kept, "the newest is the one a card is most likely to want"


def test_one_value_larger_than_the_whole_budget_is_not_kept(monkeypatch):
    """Keeping it would evict everything else to hold something that will be
    fetched from the store anyway."""
    from voxlogica.ui import results as module

    monkeypatch.setattr(module, "_PAYLOAD_BUDGET", 16)
    results = Results(Hub())
    results.observe("c" * 64, "done", value=b"\x1f\x8b" + b"x" * 200)
    assert results.bytes_of("c" * 64) is None


# --------------------------------------------------- addressing them by content


def test_the_browser_asks_by_hash_and_never_by_card_name():
    """The third fault, and the one that survived the other two being fixed: a
    card's `node` is a name in a document (`v`), the route is addressed by
    content, and `/api/node/v` is a 404 that looks exactly like "not computed".

    Asserted on the source because it is a shape rather than a behaviour: the
    hash has to come from the *result*, which is the thing that knows which node
    it is about. A card cannot know.
    """
    from pathlib import Path

    app = Path(__file__).resolve().parents[2] / "implementation" / "ui" / "src" / "App.svelte"
    text = app.read_text()
    assert "function drawable(shown)" in text, "drawable() takes a result, not a card's node"
    assert "/api/node/${shown.hash}" in text
    assert "/api/node/${hash}`" not in text, "a name is being used as an address again"


def test_both_ways_into_a_drawing_viewer_go_through_one_function():
    """A print card *is* the value; a code card shows it beside the program
    through the lens. They were two pieces of code, and only one of them passed
    `layers` -- so a print card bound to a volume mounted a canvas with nothing
    on it."""
    from pathlib import Path

    app = Path(__file__).resolve().parents[2] / "implementation" / "ui" / "src" / "App.svelte"
    text = app.read_text()
    assert text.count("drawable(") == 3, "one definition, two call sites"
    assert "layers={drawing}" in text
    assert "layers={[{" not in text, "a layer list is being built somewhere else again"


# --------------------------------------------------- the canvas follows the card


def test_the_viewer_keeps_its_drawing_buffer_in_step_with_the_card() -> None:
    """A card resizes without the window moving, and NiiVue only hears the window.

    Measured before the fix, with the card shrunk from 224 to 96 CSS pixels: the
    drawing buffer stayed at 448, so the ratio of buffer to box went from 2 --
    the device pixel ratio, which is correct -- to 4.67, and the slice went on
    being drawn for a canvas that no longer existed. The card clips, so nothing
    spilled outside it; the picture inside was simply wrong, which looks the same
    and is harder to explain.

    NiiVue's own `resizeListener` does this arithmetic, but reaches it through
    `requestAnimationFrame`, which a background tab never delivers -- so the
    computation is done here, synchronously, and the assertion is that it stayed
    that way.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "implementation/ui/src/lib/viewers/Volume.svelte"
    ).read_text(encoding="utf-8")

    assert "new ResizeObserver" in source, (
        "the viewer must watch its own box: a card changes size without the "
        "window moving, and that is the only event there is"
    )
    assert "canvas.offsetWidth" in source and "devicePixelRatio" in source, (
        "the buffer is sized from the box it is drawn into, times the device "
        "pixel ratio -- the same arithmetic NiiVue does when it is asked"
    )
    assert "nv.resizeListener()" not in source, (
        "resizeListener reaches the resize through requestAnimationFrame, which "
        "a background tab never delivers; it was tried and measured not to work"
    )
    # And it observes the host rather than the canvas it is about to resize.
    assert "observer.observe(host)" in source, (
        "observing the element you are about to resize is how a ResizeObserver "
        "loop starts"
    )
