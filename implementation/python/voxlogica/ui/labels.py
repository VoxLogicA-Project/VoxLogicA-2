"""Labels: a file's own opinion of itself, kept inside the file.

A label lives on the document's `//@board` line, beside the geometry, as
`labels="draft,wt"`. There is no index anywhere.

**Why not an index.** An index is faster and it is wrong. A label written into
the file travels with it: copy it, `git mv` it, mail it to a colleague, restore
it from a backup, and the label is still attached, because it never was anywhere
else. An index detaches the first time somebody moves a file outside this UI --
which is not an exotic failure, it is Tuesday -- and detaches *silently*, which
is the part that costs an afternoon.

**Projects have none.** A project is a folder, and a folder has no document to
describe itself in. Inventing one -- a dotfile in every project directory --
would put a second kind of state in the very place we tell people to keep under
git, to solve a problem nobody has yet reported having.

**What this costs, and how it is paid.** The sidebar used to `stat` files and
nothing more; asking for labels means opening them. So only the head of each
file is read, and the answer is memoised on `(path, mtime_ns, size)` -- the same
trick `bundler.py` uses on the source tree, for the same reason. A file whose
labels are asked for a thousand times is read once.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

#: How much of a file is read to find its `//@board` line. Directives are
#: written at the top, and a document that buried one past this is a document
#: that has been edited by hand into a shape the writer never produces.
_HEAD_BYTES = 4096

_BOARD = re.compile(r"^//@board\b(.*)$", re.MULTILINE)
#: Written quoted, always, the way every other prose field in this format is.
#: Read either way: a file somebody edited by hand should not lose its labels
#: for having been written the obvious way instead of the house way.
_LABELS = re.compile(r'\blabels=(?:"([^"]*)"|(\S+))')

#: A label is a word somebody types to find things again, so the only rules are
#: the ones that keep it findable: no commas (the separator), no quotes (the
#: delimiter), no leading or trailing space.
_ILLEGAL = re.compile(r'[",]')

_cache: dict[Path, tuple[tuple[int, int], tuple[str, ...]]] = {}
_lock = threading.Lock()


def clean(label: str) -> str:
    """A label as it will be stored, or empty if it is not one."""
    return _ILLEGAL.sub("", (label or "").strip()).strip()


def parse(text: str) -> list[str]:
    """The labels a document's text declares."""
    board = _BOARD.search(text or "")
    if board is None:
        return []
    found = _LABELS.search(board.group(1))
    if found is None:
        return []
    raw = found.group(1) if found.group(1) is not None else found.group(2)
    return [label for label in (clean(part) for part in raw.split(",")) if label]


def of(path: Path) -> list[str]:
    """The labels of a file, reading only its head, and only when it changed.

    Never raises. A file that has been deleted between the listing and this call
    has no labels, which is true and is also not worth a traceback in a sidebar.
    """
    try:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return []

    with _lock:
        cached = _cache.get(path)
    if cached is not None and cached[0] == key:
        return list(cached[1])

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError as exc:
        logger.debug("could not read %s for its labels (%s)", path, exc)
        return []

    labels = tuple(parse(head))
    with _lock:
        # Bounded by forgetting everything rather than by ranking: a library is
        # a few hundred files, and a cache this small costs less to rebuild than
        # an eviction policy costs to get right.
        if len(_cache) > 2048:
            _cache.clear()
        _cache[path] = (key, labels)
    return list(labels)


def forget(path: Path) -> None:
    """Drop what is remembered about a file. For deletion and for tests."""
    with _lock:
        _cache.pop(path, None)


def matches(labels: list[str], term: str) -> bool:
    """Whether `label:draft` -- or a bare `draft` -- selects this file."""
    wanted = clean(term.split(":", 1)[1] if ":" in term else term).lower()
    if not wanted:
        return True
    return any(label.lower().startswith(wanted) for label in labels)
