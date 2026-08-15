"""The design system's rules, asserted against the real source tree.

Every rule below is stated in ``doc/dev/ui-design-system.md`` with its reason.
They are tested rather than trusted because each one fails *invisibly*:

* A component that names a tier-1 primitive (``--gray-600``) looks perfect --
  in light mode. Dark mode redefines only the semantic tier, so the bug appears
  for whoever has their OS set the other way and for nobody else.
* A hard-coded ``#78716c``, ``160ms`` or ``cubic-bezier(...)`` also looks
  perfect, right up to the day the palette or the motion scale changes and one
  component silently does not follow.
* A component with no ``.gallery.js`` is absent from the gallery, and the
  gallery is the library: a component nobody can see is a component nobody
  maintains, themes, or checks in the state that matters.

Nothing here needs node, except the one test that verifies the gallery is
absent from a production bundle -- which builds one, and skips if the checkout
has no installed build dependencies.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# tests/unit/… -> repo root -> the UI source tree
_UI = Path(__file__).resolve().parents[2] / "implementation" / "ui"
_SRC = _UI / "src"
_DESIGN = _SRC / "lib" / "design"
_COMPONENTS = _SRC / "lib" / "components"

_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Tier 1. Raw ramps, nameable only by the design layer.
_PRIMITIVES = re.compile(r"var\(\s*--(gray|blue|red|green|white)\b")

_RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|lab|lch)\(")
_RAW_DURATION = re.compile(r"(?<![\w-])\d+(?:\.\d+)?m?s(?![\w-])")
_RAW_EASING = re.compile(r"\bcubic-bezier\(|\b(?:ease-in-out|ease-out|ease-in)\b")

#: Properties that place things. These carry the 4px rhythm and must be tokens.
#: Intrinsic control geometry (a 20px switch track, a 180px menu floor) is not
#: spacing and is deliberately not covered.
_RHYTHM_PROPERTY = re.compile(
    r"(?<![\w-])(padding|margin|gap|row-gap|column-gap|border-radius)"
    r"(-top|-right|-bottom|-left|-inline|-block|-inline-start|-inline-end)?"
    r"\s*:\s*([^;{}]+)",
)
_NONZERO_LENGTH = re.compile(r"(?<![\w.-])(?!0(?![\d.]))\d*\.?\d+(px|rem|em)\b")


def _ui_files(suffix: str) -> list[Path]:
    return sorted(p for p in _SRC.rglob(f"*{suffix}") if _DESIGN not in p.parents)


def _styles(path: Path) -> str:
    """The CSS a file declares, comments stripped.

    Only ``<style>`` blocks: prose in a doc comment may perfectly well mention
    ``14px`` or ``#fff``, and a rule that fired on prose would be a rule people
    work around by not writing comments.
    """
    text = path.read_text(encoding="utf-8")
    blocks = _STYLE_BLOCK.findall(text) if path.suffix == ".svelte" else [text]
    return "\n".join(_COMMENT.sub("", block) for block in blocks)


def _offending_lines(path: Path, pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{path.relative_to(_UI)}: {line.strip()}"
        for line in _styles(path).splitlines()
        if pattern.search(line)
    ]


# --------------------------------------------------------------- token tiers


def test_the_design_layer_is_the_only_place_that_names_a_primitive() -> None:
    offences = [line for path in _ui_files(".svelte")
                for line in _offending_lines(path, _PRIMITIVES)]
    assert not offences, (
        "A component may only name semantic roles (--color-*), never a tier-1 "
        "ramp: dark mode redefines the semantic tier and nothing else, so these "
        "are light-mode-only by construction.\n" + "\n".join(offences))


def test_no_component_hard_codes_a_colour() -> None:
    offences = [line for path in _ui_files(".svelte")
                for line in _offending_lines(path, _RAW_COLOUR)]
    assert not offences, (
        "Colours belong in tokens.css as roles. A literal here does not follow "
        "a repaint of the palette and does not follow the theme.\n"
        + "\n".join(offences))


def test_no_component_hard_codes_motion() -> None:
    offences = [
        line
        for path in _ui_files(".svelte")
        for line in _offending_lines(path, _RAW_DURATION) + _offending_lines(path, _RAW_EASING)
    ]
    assert not offences, (
        "Durations and easings are tokens (--motion-*, --easing-*). Only the "
        "tokens collapse to 0ms under prefers-reduced-motion, so a literal here "
        "is an animation that ignores the accessibility setting.\n"
        + "\n".join(offences))


def test_spacing_and_radius_come_from_the_rhythm() -> None:
    offences: list[str] = []
    for path in _ui_files(".svelte"):
        for match in _RHYTHM_PROPERTY.finditer(_styles(path)):
            value = match.group(3)
            if "var(" in value or not _NONZERO_LENGTH.search(value):
                continue
            offences.append(f"{path.relative_to(_UI)}: {match.group(0).strip()}")
    assert not offences, (
        "Padding, margins, gaps and radii are the 4px rhythm (--space-*, "
        "--radius-*). Eight spacings rather than forty is most of what makes "
        "the interface look composed.\n" + "\n".join(offences))


def test_the_app_stylesheet_only_delegates() -> None:
    """Global CSS lives in the design layer; anything else is a style with no
    component to own it -- invisible to the gallery, outside the system."""
    body = _COMMENT.sub("", (_SRC / "app.css").read_text(encoding="utf-8")).strip()
    assert body == '@import "./lib/design/index.css";', body


# ------------------------------------------------------- gallery == library


def _component_dirs() -> list[Path]:
    return sorted(p for p in _COMPONENTS.iterdir() if p.is_dir())


def test_every_component_ships_a_gallery_entry_beside_it() -> None:
    for directory in _component_dirs():
        name = directory.name
        assert (directory / f"{name}.svelte").is_file(), f"{name} has no {name}.svelte"
        entry = directory / f"{name}.gallery.js"
        assert entry.is_file(), (
            f"{name} has no {name}.gallery.js, so it does not appear in the dev "
            "gallery -- and the gallery is the library, not a description of it.")


def test_every_gallery_entry_is_well_formed() -> None:
    """The shape `registry.js` validates at runtime, checked statically too, so
    a malformed entry fails in CI and not only in somebody's browser."""
    for directory in _component_dirs():
        name = directory.name
        source = (directory / f"{name}.gallery.js").read_text(encoding="utf-8")
        assert f'from "./{name}.svelte"' in source, (
            f"{name}.gallery.js must import the real component beside it: a "
            "specimen that is a copy can be right while the component is wrong.")
        assert re.search(rf'name:\s*"{name}"', source), f"{name}.gallery.js: name mismatch"
        assert re.search(r"summary:\s*\n?\s*\"", source), f"{name}.gallery.js: no summary"
        assert re.search(rf"component:\s*{name}\b", source), f"{name}.gallery.js: no component"
        variants = source.count("label:")
        assert variants >= 2, (
            f"{name}.gallery.js declares {variants} variant(s). A component "
            "ships every state it supports, including the ugly ones.")


def test_the_public_surface_and_the_library_are_the_same_set() -> None:
    index = (_COMPONENTS / "index.js").read_text(encoding="utf-8")
    exported = set(re.findall(r"export \{ default as (\w+) \}", index))
    on_disk = {d.name for d in _component_dirs()}
    assert exported == on_disk, (
        "components/index.js is the only surface the app imports from; a "
        "component missing from it is unreachable, and a name in it with no "
        f"folder is a broken import.\nexported={sorted(exported)}\n"
        f"on_disk={sorted(on_disk)}")


def test_the_app_imports_components_only_through_the_public_surface() -> None:
    for path in _ui_files(".svelte") + _ui_files(".js"):
        if _COMPONENTS in path.parents or (_SRC / "lib" / "gallery") in path.parents:
            continue  # the library's own files, and the gallery, reach inside
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not re.search(r'from "[^"]*components/\w+/', line), (
                f"{path.relative_to(_UI)} reaches past components/index.js: "
                f"{line.strip()}")


# ------------------------------------------------- the gallery is dev-only


@pytest.mark.skipif(not (_UI / "node_modules" / "esbuild").is_dir(),
                    reason="UI build dependencies are not installed in this checkout")
def test_a_production_bundle_contains_no_gallery() -> None:
    """`__DEV__` is defined to a literal so esbuild drops the panel entirely.

    Asserted on the bytes, not on the source: the mechanism is a define plus
    dead-code elimination, and a refactor that turned the dynamic import into a
    static one would keep compiling and quietly ship the whole gallery.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")

    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run([node, "build.mjs", "--outdir", tmp],
                              cwd=_UI, capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr or proc.stdout
        out = Path(tmp)
        js = (out / "app.js").read_text(encoding="utf-8")
        css = (out / "app.css").read_text(encoding="utf-8")
        inputs = json.loads((out / "meta.json").read_text(encoding="utf-8"))["inputs"]

    assert not [name for name in inputs if "/lib/gallery/" in name.replace("\\", "/")], \
        "gallery modules reached the production bundle"
    for marker in ("Design system", "Moodboard", "virtual:gallery"):
        assert marker not in js, f"{marker!r} is in the production bundle"
    assert "voxlogica-dev-panel" not in js
    assert "Moodboard" not in css


# ------------------------------------------- the board works where nobody looks


def test_the_board_falls_back_to_the_document_s_geometry() -> None:
    """A tab that is not rendering measures zero, and a board that believed it
    would collapse to one cell and refuse every move as out of bounds.

    Asserted on the source because the alternative is a browser: the point is
    that the measured room is never used raw.
    """
    board = (_SRC / "lib" / "components" / "Bento" / "Bento.svelte").read_text(encoding="utf-8")
    assert "room_.width || cols * basePitch" in board, (
        "the board must fall back to the document's own cols/rows when the "
        "window reports no size at all")
    assert "room_.width + gutter" not in board, (
        "the measured room is used through `space`, which has the fallback")


def test_a_capture_is_sized_by_content_not_by_the_viewport() -> None:
    capture = (_SRC / "lib" / "capture.ts").read_text(encoding="utf-8")
    assert "scrollWidth" in capture and "fallback" in capture, (
        "a screenshot from a background tab must not be a one-pixel strip")


def test_a_tab_that_cannot_render_lets_a_better_one_answer() -> None:
    connection = (_SRC / "lib" / "connection.js").read_text(encoding="utf-8")
    assert "renderable()" in connection, (
        "the first answer wins a capture, so a hidden zero-sized tab has to "
        "give a visible one the chance to answer first")
