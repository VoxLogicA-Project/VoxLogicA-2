from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import platform
import shutil
import stat
import urllib.request
import zipfile


LEGACY_BIN_ENV = "VOXLOGICA1_EXPERIMENTAL_BIN"
DEFAULT_LEGACY_BIN = Path("/tmp/VoxLogicA-experimental/src/bin/Release/net9.0/osx-x64/VoxLogicA")
VOX1_CACHE_DIR_ENV = "VOXLOGICA_VOX1_CACHE_DIR"
PINNED_RELEASE_TAG = "v1.3.3-experimental"
PINNED_RELEASE_PAGE = f"https://github.com/vincenzoml/VoxLogicA/releases/tag/{PINNED_RELEASE_TAG}"
RELEASE_TAG_API = f"https://api.github.com/repos/vincenzoml/VoxLogicA/releases/tags/{PINNED_RELEASE_TAG}"
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DownloadedBinary:
    binary: Path
    release_tag: str
    asset_name: str


def _rid_candidates() -> tuple[str, ...]:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        if "arm" in machine or "aarch64" in machine:
            return ("linux-arm64", "linux-x64")
        return ("linux-x64",)
    if system == "darwin":
        if "arm" in machine:
            # VoxLogicA-1 release currently ships only osx-x64 assets.
            return ("osx-arm64", "osx-x64")
        return ("osx-x64",)
    if system == "windows":
        if "arm" in machine:
            return ("win-arm64", "win-x64")
        return ("win-x64",)

    raise RuntimeError(f"Unsupported system for vox1 binary lookup: {system}")


def _cache_dir() -> Path:
    configured = os.environ.get(VOX1_CACHE_DIR_ENV)
    if configured:
        root = Path(configured).expanduser()
    else:
        root = REPO_ROOT / ".cache" / "vox1"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _release_json() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "voxlogica-ci"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        RELEASE_TAG_API,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=60) as response:  # noqa: S310
        payload = response.read()
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected GitHub release response format")
    if str(data.get("tag_name", "")) != PINNED_RELEASE_TAG:
        raise RuntimeError(
            f"Resolved release tag mismatch for VoxLogicA-1 binary: expected "
            f"{PINNED_RELEASE_TAG}, got {data.get('tag_name')!r}"
        )
    return data


def _asset_match_score(asset_name: str, rid: str) -> int | None:
    if not asset_name.endswith(".zip"):
        return None
    if rid not in asset_name:
        return None

    score = 10
    lowered = asset_name.lower()
    if "experimental" in lowered:
        score += 2
    return score


def _pick_release_asset(release: dict, rid_candidates: tuple[str, ...]) -> dict:
    best: tuple[int, dict] | None = None
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError("Pinned release does not expose an asset list")
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_name = str(asset.get("name", ""))
        for rid_priority, rid in enumerate(rid_candidates):
            score = _asset_match_score(asset_name, rid)
            if score is None:
                continue
            priority_boost = (len(rid_candidates) - rid_priority) * 100
            total_score = priority_boost + score
            if best is None or total_score > best[0]:
                best = (total_score, asset)
    if best is None:
        raise RuntimeError(
            f"No release asset found in {PINNED_RELEASE_TAG} for RID candidates "
            f"{', '.join(rid_candidates)}"
        )
    return best[1]


def _find_binary(extracted_root: Path) -> Path:
    candidates: list[Path] = []
    for entry in extracted_root.rglob("*"):
        if not entry.is_file():
            continue
        if entry.name in {"VoxLogicA", "VoxLogicA.exe"}:
            candidates.append(entry)
    if not candidates:
        raise RuntimeError(f"No VoxLogicA binary found under {extracted_root}")

    def _score(path: Path) -> tuple[int, int]:
        has_stdlib = 1 if (path.parent / "stdlib.imgql").exists() else 0
        depth = len(path.parts)
        return (has_stdlib, -depth)

    best = sorted(candidates, key=_score, reverse=True)[0]
    mode = best.stat().st_mode
    best.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return best


def download_legacy_binary_from_releases() -> DownloadedBinary:
    rid_candidates = _rid_candidates()
    release = _release_json()
    asset = _pick_release_asset(release, rid_candidates)

    release_tag = str(release.get("tag_name", "unknown"))
    asset_name = str(asset.get("name", "unknown.zip"))
    asset_url = str(asset.get("browser_download_url", ""))
    if not asset_url:
        raise RuntimeError(f"Release asset '{asset_name}' has no download URL")

    cache_root = _cache_dir() / release_tag
    cache_root.mkdir(parents=True, exist_ok=True)
    zip_path = cache_root / asset_name
    extracted_dir = cache_root / asset_name.removesuffix(".zip")
    marker = extracted_dir / ".ready"

    if not marker.exists():
        if not zip_path.exists():
            headers = {"Accept": "application/octet-stream", "User-Agent": "voxlogica-ci"}
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = urllib.request.Request(
                asset_url,
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310
                zip_path.write_bytes(response.read())

        if extracted_dir.exists():
            shutil.rmtree(extracted_dir)
        extracted_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extracted_dir)
        marker.write_text("ok", encoding="utf-8")

    binary = _find_binary(extracted_dir)
    return DownloadedBinary(binary=binary, release_tag=release_tag, asset_name=asset_name)


def resolve_legacy_binary_path(auto_download: bool = True) -> Path | None:
    configured = os.environ.get(LEGACY_BIN_ENV)
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return path

    if DEFAULT_LEGACY_BIN.exists():
        return DEFAULT_LEGACY_BIN

    # Reuse any previously downloaded binary in cache before hitting network.
    cache = _cache_dir() / PINNED_RELEASE_TAG
    if cache.exists():
        for candidate in sorted(cache.rglob("*")):
            if candidate.is_file() and candidate.name in {"VoxLogicA", "VoxLogicA.exe"}:
                return candidate

    if not auto_download:
        return None

    try:
        downloaded = download_legacy_binary_from_releases()
    except Exception as exc:
        print(
            f"Unable to download VoxLogicA-1 binary from {PINNED_RELEASE_PAGE}: {exc}",
            flush=True,
        )
        return None
    return downloaded.binary
