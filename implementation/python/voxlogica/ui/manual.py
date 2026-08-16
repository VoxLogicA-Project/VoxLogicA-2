"""The manual, served to whoever asks -- which is a person and an agent.

One page, one copy. A second copy written for agents would be a second thing to
keep in step, and the one that fell behind would be the one nobody reads over
somebody's shoulder.

It is read from the tree at the moment it is asked for rather than baked in at
build time, so editing it during a session is enough: the same live-reload
reasoning the UI bundle follows.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: `voxlogica/ui/manual.py` → `implementation/python/voxlogica/ui` → up four to
#: the repository root, where `doc/` lives.
_PAGE = Path(__file__).resolve().parents[4] / "doc" / "user" / "manual.md"

#: What is said when there is no page to read -- a wheel, most likely, which
#: ships the package and not the documentation tree. A sentence pointing at
#: where it lives beats an empty string, which reads as "there is no manual".
_ABSENT = (
    "The manual is not in this installation. It lives at doc/user/manual.md in "
    "the VoxLogicA source tree, and at the project's repository."
)


def path() -> Path:
    return _PAGE


def manual() -> str:
    """The manual as text. Never raises: an agent asking what this is should
    not be answered with a traceback."""
    try:
        return _PAGE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("no manual at %s (%s)", _PAGE, exc)
        return _ABSENT
