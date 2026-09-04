"""Logic and UI stay apart, and this is what makes that a fact.

R3, from doc/dev/ui-workspace.md: every mutation lives in a UI-less module, and
components read the store, call actions, and never assign to it. It has been the
working rule from the start and it was asserted rather than checked -- the
document itself says "there will be a test for it". This is that test.

The property is not tidiness. It is what lets a person in a browser and an agent
over MCP drive *one* workspace: every change has a name, that name is in one
list, and both sides call it. A component that reached in and assigned would be
a change with no name -- invisible to MCP, absent from undo, and impossible to
replay.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "implementation" / "ui" / "src"
ACTIONS = ROOT / "implementation" / "python" / "voxlogica" / "ui" / "actions.py"


def _components() -> list[Path]:
    return sorted(UI.rglob("*.svelte"))


def _skip_without_sources() -> None:
    if not UI.is_dir():
        pytest.skip("no UI sources here (running from a wheel)")


# --------------------------------------------------- no component assigns state


#: The stores a component may read and must never write.
_STORES = ("workspace", "results")

#: `x.y = `, `x.y += `, `x.y++`, and the same through an index.
_ASSIGNMENT = re.compile(
    r"\b(" + "|".join(_STORES) + r")\.[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]]*\])*"
    r"\s*(?:=[^=]|\+\+|--|\+=|-=)"
)


def test_no_component_assigns_to_a_store():
    """The rule the whole store design rests on.

    A component that assigned would be making a change with no name: MCP would
    not see it, undo would not know it happened, and the two front ends would
    have quietly stopped being two views of one thing.
    """
    _skip_without_sources()
    offences = []
    for path in _components():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if _ASSIGNMENT.search(line):
                offences.append(f"{path.relative_to(UI)}:{number}: {line.strip()}")

    assert not offences, (
        "components read the store and call actions; they never assign to it. "
        "Give the change a name in actions.py and call it:\n  "
        + "\n  ".join(offences)
    )


def test_the_action_modules_render_nothing():
    """R3 asks for `.svelte.ts` -- modules with no markup -- and this is what
    "no markup" has to mean to be checkable."""
    _skip_without_sources()
    for path in (UI / "lib" / "actions").rglob("*"):
        if path.suffix in {".ts", ".js"} and path.is_file():
            text = path.read_text()
            assert "<script" not in text and "</div>" not in text, path


def test_every_action_is_reachable_from_the_typed_facade():
    """Neither side owns the vocabulary, so neither may grow a private half.

    An action only Python knows is a thing an agent can do and a person cannot;
    one only TypeScript knows does not exist at all.
    """
    names = set(re.findall(r'_action\(\s*"([^"]+)"', ACTIONS.read_text()))
    facade = (UI / "lib" / "actions" / "index.ts").read_text()
    # The generic may itself contain `>` (`invoke<Record<string, unknown>>`), so
    # the match runs to the opening bracket rather than trying to balance them.
    called = set(re.findall(r'invoke[^(\n]*\(\s*"([^"]+)"', facade))
    missing = names - called
    assert not missing, f"actions the browser cannot call: {sorted(missing)}"


def test_no_component_talks_to_the_server_directly():
    """One road, so there is one place where a change becomes real.

    A `fetch` in a component is an action that skipped the dispatch table: no
    undo entry, no broadcast, and nothing for MCP to have called.
    """
    _skip_without_sources()
    offences = []
    for path in _components():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\bfetch\s*\(", line) and "/api/" in line:
                offences.append(f"{path.relative_to(UI)}:{number}")
    assert not offences, (
        "components ask for actions by name; they do not call the server:\n  "
        + "\n  ".join(offences)
    )


def test_the_mutation_surface_is_one_file():
    """Every action's implementation is in actions.py, so "what can change a
    workspace" is a question with a file for an answer."""
    text = ACTIONS.read_text()
    declared = re.findall(r"_action\(\s*\"[^\"]+\",[^)]*?,\s*([_a-z][_a-z0-9]*)\)", text, re.S)
    for handler in set(declared):
        assert f"def {handler}(" in text, f"{handler} is registered but not defined here"
