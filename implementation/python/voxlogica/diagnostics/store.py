"""Small private on-disk store for opt-in diagnostic inspection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets

from voxlogica.diagnostics.model import DiagnosticReport


def _root() -> Path:
    root = Path(os.environ.get("VOXLOGICA_DIAGNOSTIC_DIR", Path.home() / ".voxlogica" / "diagnostics"))
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _trim(root: Path, keep: int = 64) -> None:
    """Bound technical-report retention without touching unrelated user files."""
    reports = sorted(root.glob("VLX-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in reports[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def store_report(report: DiagnosticReport) -> str:
    """Persist technical details and return a short opaque identifier."""
    details_id = f"VLX-{secrets.token_hex(4).upper()}"
    target = _root() / f"{details_id}.json"
    target.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    _trim(target.parent)
    return details_id


def load_report(details_id: str) -> str | None:
    if not details_id.startswith("VLX-") or "/" in details_id or "\\" in details_id:
        return None
    target = _root() / f"{details_id}.json"
    try:
        return target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
