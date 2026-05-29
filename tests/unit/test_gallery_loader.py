from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxlogica.gallery import GALLERY_ROOT, load_gallery, load_gallery_manifest


@pytest.mark.unit
def test_gallery_manifest_lists_all_program_files() -> None:
    manifest = load_gallery_manifest()
    assert manifest["available"] is True
    assert manifest["version"] == 1

    for entry in manifest["examples"]:
        program = GALLERY_ROOT / str(entry["program"])
        assert program.is_file(), f"missing program for {entry['id']}: {program}"


@pytest.mark.unit
def test_load_gallery_inlines_program_code() -> None:
    payload = load_gallery()
    assert payload["available"] is True
    assert payload["examples"]
    assert payload["modules"] == sorted({example["module"] for example in payload["examples"]})

    hello = next(example for example in payload["examples"] if example["id"] == "intro-hello")
    assert hello["code"] == "answer = 1 + 2"
    assert hello["module"] == "default"


@pytest.mark.unit
def test_manifest_modules_match_examples(tmp_path: Path) -> None:
    root = tmp_path / "gallery"
    programs = root / "programs" / "default"
    programs.mkdir(parents=True)
    (programs / "demo.imgql").write_text("x = 1\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "examples": [
            {
                "id": "demo",
                "title": "Demo",
                "module": "default",
                "level": "intro",
                "strategy": "strict",
                "description": "Demo card.",
                "program": "programs/default/demo.imgql",
            }
        ],
        "modules": ["default"],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload = load_gallery(root=root)
    assert payload["examples"][0]["code"] == "x = 1"
