"""Making the MCP server findable: instance discovery, and client registration.

Two problems, one file.

**Which port?** Every ``voxlogica run`` takes the next free port, so the address
of "the workspace" is not knowable in advance and there may be several. Each
instance therefore drops a small file in a state directory while it is alive, and
anything looking for a workspace reads that directory. The files are pruned by
liveness (is that pid still running?) rather than by trusting a clean shutdown,
because a killed process never gets to tidy up.

**Which client?** An agent should see the workspace without anybody editing JSON,
so at boot we make sure the known MCP clients have a ``voxlogica`` entry. The
entry is stable -- it names ``voxlogica mcp``, a stdio bridge that finds the live
instance itself -- so it is written once and stays correct across runs and ports.

Registration is deliberately conservative: an entry is added only when absent,
nothing else in the file is touched, and any failure is logged and ignored. These
are the user's files, and a computation must never fail because a config was
unwritable.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def state_dir() -> Path:
    """Where live instances announce themselves."""
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        root = Path(base)
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path.home() / ".local" / "state"
    return root / "voxlogica" / "instances"


# ------------------------------------------------------------------ instances


def announce(*, port: int, url: str, program: str | None) -> Path | None:
    try:
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{os.getpid()}.json"
        path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "port": port,
                    "url": url,
                    "program": program,
                    "startedAt": time.time(),
                }
            )
        )
        return path
    except OSError as error:
        logger.debug("could not announce this instance (%s)", error)
        return None


def withdraw(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # somebody else's process, but a process
    return True


def instances() -> list[dict[str, Any]]:
    """Live instances, newest first. Stale files are removed as they are found."""
    found: list[dict[str, Any]] = []
    directory = state_dir()
    if not directory.is_dir():
        return found
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not _alive(int(record.get("pid", -1))):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        found.append(record)
    found.sort(key=lambda record: record.get("startedAt", 0), reverse=True)
    return found


# ------------------------------------------------------------------- clients

#: `voxlogica mcp` rather than a URL: a URL would name a port that changes every
#: run, and would be wrong the moment the instance that wrote it exited.
def _entry() -> dict[str, Any]:
    return {"command": sys.executable, "args": ["-m", "voxlogica.main", "mcp"]}


def _json_client(path: Path, key: str = "mcpServers") -> bool:
    """Add our entry to a JSON config that already belongs to something."""
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return False
    servers = config.setdefault(key, {})
    if not isinstance(servers, dict) or "voxlogica" in servers:
        return False
    servers["voxlogica"] = _entry()
    try:
        # Atomic: a client reading this file while we write it must never see
        # half a JSON document.
        temporary = path.with_suffix(path.suffix + ".voxlogica.tmp")
        temporary.write_text(json.dumps(config, indent=2))
        temporary.replace(path)
        return True
    except OSError as error:
        logger.debug("could not register with %s (%s)", path, error)
        return False


def _toml_client(path: Path) -> bool:
    """Codex keeps its servers in TOML; append a table if ours is missing."""
    try:
        text = path.read_text() if path.exists() else ""
    except OSError:
        return False
    if "[mcp_servers.voxlogica]" in text:
        return False
    args = ", ".join(json.dumps(argument) for argument in _entry()["args"])
    block = (
        f"\n[mcp_servers.voxlogica]\n"
        f"command = {json.dumps(_entry()['command'])}\n"
        f"args = [{args}]\n"
    )
    try:
        with path.open("a") as handle:
            handle.write(block)
        return True
    except OSError as error:
        logger.debug("could not register with %s (%s)", path, error)
        return False


def client_configs() -> dict[str, tuple[Path, Path]]:
    """Per client: the config to write, and the evidence it is installed here.

    The evidence matters. Writing `~/.claude.json` on a machine with no Claude
    Code would leave a config file for an application that does not exist -- so
    each client is registered only when its own file or directory is already
    there, and a machine without a client is left exactly as it was.
    """
    home = Path.home()
    support = home / "Library" / "Application Support"
    return {
        "claude-code": (home / ".claude.json", home / ".claude"),
        "claude-desktop": (
            support / "Claude" / "claude_desktop_config.json",
            support / "Claude",
        ),
        "cursor": (home / ".cursor" / "mcp.json", home / ".cursor"),
        "windsurf": (
            home / ".codeium" / "windsurf" / "mcp_config.json",
            home / ".codeium" / "windsurf",
        ),
        "codex": (home / ".codex" / "config.toml", home / ".codex"),
    }


def register_clients() -> list[str]:
    """Make sure every installed MCP client knows about `voxlogica`.

    Returns the clients that were newly registered, so the CLI can say so once
    rather than on every run. Never raises: these are the user's files, and a
    computation must not fail because one of them was unwritable.
    """
    if os.environ.get("VOXLOGICA_NO_MCP_REGISTER"):
        return []
    added: list[str] = []
    for name, (path, evidence) in client_configs().items():
        if not path.exists() and not evidence.exists():
            continue
        try:
            done = _toml_client(path) if path.suffix == ".toml" else _json_client(path)
        except Exception as error:  # noqa: BLE001 - a config must never break a run
            logger.debug("registration with %s failed (%s)", name, error)
            done = False
        if done:
            added.append(name)
    return added
