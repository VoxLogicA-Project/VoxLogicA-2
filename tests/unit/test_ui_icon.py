"""One drawing, and nothing anywhere that redraws it.

An icon set where one size was regenerated and another was not is the kind of
thing nobody notices until a release. So there is exactly one file, everything
else points at it, and this test is what keeps "everything else" honest -- the
favicon was a hand-inlined copy of the same mark when this was written, and had
already stopped matching.
"""

from __future__ import annotations

import sys

import pytest

from voxlogica.ui import icon

ROOT = icon.Path(icon.__file__).resolve().parents[4]
UI = ROOT / "implementation" / "ui"
INDEX = UI / "index.html"
BUILD = UI / "build.mjs"


def _skip_without_sources() -> None:
    if not UI.is_dir():
        pytest.skip("no UI sources here (running from a wheel)")


def test_there_is_a_mark_and_it_is_a_vector():
    found = icon.path()
    assert found is not None, "the application has no icon"
    assert found.suffix == ".svg"
    assert b"<svg" in (icon.data() or b"")


def test_the_page_links_to_the_mark_rather_than_carrying_a_copy():
    """This is the one that had already gone wrong.

    A data: URI in the HTML is a second copy of the drawing, and the second copy
    is the one nobody edits.
    """
    _skip_without_sources()
    html = INDEX.read_text()
    assert "data:image/svg+xml" not in html, (
        "the favicon is inlined into index.html; link icon.svg instead, so there "
        "is one drawing"
    )
    assert 'href="icon.svg"' in html


def test_the_build_puts_the_mark_beside_the_bundle():
    """Otherwise the link resolves to nothing, which is a missing favicon --
    invisible in development, where the browser has one cached."""
    _skip_without_sources()
    assert 'copyFile(resolve(here, "icon.svg")' in BUILD.read_text()


def test_a_change_to_the_mark_invalidates_the_bundle():
    """The fingerprint is the whole tree, and the icon is part of what is
    served. Left out, editing it would show the old one until something else
    happened to change."""
    from voxlogica.ui.bundler import _ROOT_INPUTS

    assert "icon.svg" in _ROOT_INPUTS


def test_it_reaches_the_dock_without_generating_anything():
    """macOS reads SVG into NSImage, so the vector goes straight to the Dock at
    whatever size it wants. A rasterising build step would be a build step, a
    cache, and six more chances to disagree with the drawing."""
    if sys.platform != "darwin":
        pytest.skip("the Dock is a macOS idea")
    pytest.importorskip("AppKit")
    assert icon.apply_to_dock() is True


def test_asking_for_the_dock_never_raises(monkeypatch):
    """An icon is never worth a window that did not open."""
    monkeypatch.setattr(icon, "data", lambda: None)
    assert icon.apply_to_dock() is False

    monkeypatch.setattr(icon, "data", lambda: b"not an svg at all")
    assert icon.apply_to_dock() in (True, False)  # false on macOS, false elsewhere


def test_the_application_is_called_what_it_is():
    """Without a bundle, macOS names the window after the interpreter -- so the
    menu bar and cmd-Tab said "Python 3.14t", which is not what anybody
    launched."""
    if sys.platform != "darwin":
        pytest.skip("bundles are a macOS idea")
    pytest.importorskip("Foundation")
    from Foundation import NSBundle, NSProcessInfo

    assert icon.name_the_application() is True
    assert NSBundle.mainBundle().infoDictionary().get("CFBundleName") == icon.NAME
    assert NSProcessInfo.processInfo().processName() == icon.NAME


def test_naming_never_raises(monkeypatch):
    """A name is never worth a window that did not open."""
    monkeypatch.setattr(icon.sys, "platform", "linux")
    assert icon.name_the_application() is False


def test_the_window_sets_it_before_the_window_exists():
    """A window that appeared with the generic Python rocket and then changed
    would say "a script somebody left running" for exactly long enough."""
    from voxlogica.ui import window

    source = icon.Path(window.__file__).read_text()
    creation = source.index("webview.create_window")
    # The menu bar is built once; a name set after it is a name nobody sees.
    assert source.index("icon.name_the_application(") < creation
    assert source.index("icon.apply_to_dock()") < creation
