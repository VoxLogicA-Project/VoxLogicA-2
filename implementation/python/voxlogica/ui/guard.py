"""Which paths a client may name.

Today every instance binds loopback only, so the only client that can reach it
is the person sitting at this machine, and the answer is deliberately *nothing*:
no restriction at all. A check that stopped somebody opening their own file
would protect nobody -- they can open it with any other program they have -- and
it would cost them the one thing a local tool is for.

The rule exists anyway, in one place, because the day this listens on anything
but loopback the answer has to change in one edit rather than in fifteen call
sites. Off loopback the boundary is the launch directory and its subdirectories
-- nothing above it, nothing beside it -- and the system dialogues are refused
outright, because a file picker that opens on the *host* is not a feature a
remote client gets to have.

Two properties do the work:

* every path a client names goes through `permit`, which resolves symlinks
  before deciding -- a link pointing out of the tree is the oldest way through a
  check like this one;
* a path the user chose in a native dialogue is remembered as permitted, so
  choosing is what widens the boundary, never a client asking.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class Refused(PermissionError):
    """A client named a path outside what this instance may touch."""


#: Set once, when the server binds. False means: confine to the launch directory.
_local_only = True
#: Directories the *user* opened through a system dialogue.
_chosen: set[Path] = set()


def configure(*, local_only: bool) -> None:
    global _local_only
    _local_only = local_only


def is_local_only() -> bool:
    return _local_only


def launch_root() -> Path:
    """Where this instance was started. The boundary when it is not loopback."""
    return Path(os.environ.get("VOXLOGICA_ROOT", Path.cwd())).expanduser().resolve()


def approve(path: str | Path) -> Path:
    """Remember a directory the user chose themselves, and allow it from now on."""
    resolved = Path(path).expanduser().resolve()
    _chosen.add(resolved if resolved.is_dir() else resolved.parent)
    return resolved


def roots() -> list[Path]:
    """The boundary. Empty means there is none, which is the local case."""
    if _local_only:
        return []
    return [launch_root(), *sorted(_chosen)]


def allowed(path: str | Path) -> bool:
    """Local: anything. Otherwise: the launch tree, and what the user chose.

    Local is deliberately unrestricted. The only client that can reach a
    loopback socket is the person sitting here, and an application that refused
    to open a file its own user just named would be fighting them with a check
    that protects nobody -- they can open it with any other program on the
    machine.
    """
    if _local_only:
        return True
    # Resolved first: a symlink pointing out of the tree is the oldest way
    # through a check like this one.
    resolved = Path(path).expanduser().resolve()
    return any(resolved == root or root in resolved.parents for root in roots())


def permit(path: str | Path) -> Path:
    """The resolved path, or `Refused` naming the boundary it fell outside."""
    resolved = Path(path).expanduser().resolve()
    if allowed(resolved):
        return resolved
    raise Refused(f"{resolved} is outside the launch directory")


def may_open_dialogs() -> bool:
    """A system dialogue belongs to whoever is sitting at this machine."""
    return _local_only
