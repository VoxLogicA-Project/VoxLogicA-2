"""Load structured example gallery data for serve and UI consumers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GALLERY_ROOT = Path(__file__).resolve().parents[3] / "doc" / "gallery"
MANIFEST_PATH = GALLERY_ROOT / "manifest.json"


def gallery_root() -> Path:
    """Return the repository gallery directory."""
    return GALLERY_ROOT


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _read_program(root: Path, relative_program: str) -> str:
    program_path = root / relative_program
    if not program_path.is_file():
        raise FileNotFoundError(f"Gallery program not found: {program_path}")
    return program_path.read_text(encoding="utf-8")


def load_gallery_manifest(*, root: Path | None = None) -> dict[str, Any]:
    """Load ``manifest.json`` without resolving program sources."""
    gallery_dir = root or GALLERY_ROOT
    manifest_path = gallery_dir / "manifest.json"
    if not manifest_path.is_file():
        return {
            "available": False,
            "path": str(manifest_path),
            "version": None,
            "examples": [],
            "modules": [],
            "updated_at": None,
        }
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "path": str(manifest_path),
        "version": payload.get("version"),
        "examples": list(payload.get("examples") or []),
        "modules": list(payload.get("modules") or []),
        "updated_at": _iso_utc(manifest_path.stat().st_mtime),
    }


def load_gallery(*, root: Path | None = None) -> dict[str, Any]:
    """Load gallery manifest and inline program text for API/UI consumers."""
    gallery_dir = root or GALLERY_ROOT
    manifest = load_gallery_manifest(root=gallery_dir)
    if not manifest["available"]:
        return {
            **manifest,
            "markdown": "",
        }

    examples: list[dict[str, Any]] = []
    for entry in manifest["examples"]:
        program = str(entry["program"])
        code = _read_program(gallery_dir, program).strip()
        examples.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "module": entry["module"],
                "level": entry["level"],
                "description": entry.get("description", ""),
                "strategy": entry.get("strategy", "strict"),
                "program": program,
                "code": code,
            }
        )

    modules = sorted({str(example["module"]) for example in examples})
    return {
        "available": True,
        "path": manifest["path"],
        "version": manifest["version"],
        "markdown": "",
        "examples": examples,
        "modules": modules,
        "updated_at": manifest["updated_at"],
    }
