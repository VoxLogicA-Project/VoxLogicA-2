"""A side effect happens only where its value is wanted.

Evaluation is demand-driven, so a write whose result nothing reads is a write
that never happens. That is the correct semantics and it is not going to
change, but it makes one imperative-looking idiom quietly wrong:

    let _ = WriteImage(a, "first.png") in
    WriteImage(b, "second.png")

It reads as "do this, then that". It means "nobody needs the first one", so the
first file is never written -- with no error, because the goal that asked for
this expression is satisfied by the second path alone. It cost five of ten
exported PNGs in doc/gallery/programs/nnunet/nnunet-circle-segmentation.imgql,
and the miscount is the only symptom.

The static plan does NOT distinguish the two forms -- same node count, same
reachability from the goal -- so only running them tells them apart. Hence a
test that runs them.
"""

from __future__ import annotations

import pytest

from voxlogica.execution import ExecutionEngine
from voxlogica.parser import parse_program_content
from voxlogica.reducer import reduce_program

# 16x16 constant images: small enough that the run costs nothing, real enough
# that WriteImage does what it does in the gallery programs.
PROGRAM = """
import "geom"
import "simpleitk"

img(v) = geom.circle(geom.blank(16, 16, 0), 8, 8, 4, v)
to_png(i) = Cast(RescaleIntensity(i, 0, 255), GetPixelIDValueFromString("sitkUInt8"))

discarded(a, b) =
  let _ = WriteImage(to_png(img(a)), "{out}/let_first.png") in
  WriteImage(to_png(img(b)), "{out}/let_second.png")

returned(a, b) =
  [ WriteImage(to_png(img(a)), "{out}/list_first.png"),
    WriteImage(to_png(img(b)), "{out}/list_second.png") ]

print "discarded" discarded(100, 200)
print "returned"  returned(110, 210)
"""


@pytest.fixture
def written(tmp_path):
    """Run the program in tmp_path and return the set of files it wrote."""
    out = str(tmp_path).replace("\\", "/")
    result = ExecutionEngine().execute_workplan(
        reduce_program(parse_program_content(PROGRAM.format(out=out))))
    assert result.success, "the program itself must run; this test is about its writes"
    return {p.name for p in tmp_path.iterdir()}


@pytest.mark.unit
def test_a_write_bound_to_an_unread_name_does_not_happen(written) -> None:
    """The trap, pinned. If this ever fails, the semantics changed."""
    assert "let_second.png" in written, "the returned write must happen"
    assert "let_first.png" not in written, (
        "a `let _ = write in ...` was executed: demand-driven evaluation is not "
        "supposed to compute a value nobody reads")


@pytest.mark.unit
def test_returning_both_writes_performs_both(written) -> None:
    """The fix, pinned: put the write in the goal and it happens."""
    assert {"list_first.png", "list_second.png"} <= written, (
        f"both writes are returned, so both must happen; got {sorted(written)}")


@pytest.mark.unit
def test_no_gallery_program_uses_the_discarding_idiom() -> None:
    """The idiom is a silent data-loss bug in a program that exports files.

    Cheaper to forbid than to catch: nothing in the gallery needs `let _`, and
    a program that grows one loses files without failing.
    """
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"^\s*let\s+_\s*=", re.MULTILINE)
    offenders = [
        p.relative_to(root).as_posix()
        for p in sorted((root / "doc" / "gallery").rglob("*.imgql"))
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "these programs bind a value to `_`, which means it is never computed; "
        f"return it instead: {offenders}")
