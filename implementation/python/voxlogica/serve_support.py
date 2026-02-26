"""Serve-layer helpers for playground jobs, docs gallery, and dashboard metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import multiprocessing as mp
import json
import os
import pickle
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import quote
import xml.etree.ElementTree as ET
import uuid


REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_GALLERY_PATH = REPO_ROOT / "doc" / "user" / "language-gallery.md"
TEST_REPORTS_DIR = REPO_ROOT / "tests" / "reports"
JUNIT_REPORT_PATH = TEST_REPORTS_DIR / "junit.xml"
COVERAGE_REPORT_PATH = TEST_REPORTS_DIR / "coverage.xml"
PERF_REPORT_DIR = TEST_REPORTS_DIR / "perf"
PERF_REPORT_JSON = PERF_REPORT_DIR / "vox1_vs_vox2_perf.json"
PERF_REPORT_SVG = PERF_REPORT_DIR / "vox1_vs_vox2_perf.svg"
TEST_JOB_LOG_DIR = TEST_REPORTS_DIR / "jobs"
PLAYGROUND_JOB_LOG_DIR = TEST_REPORTS_DIR / "playground"
DEFAULT_PLAYGROUND_LOAD_DIR = REPO_ROOT / "tests"
PLAYGROUND_LOAD_DIR_ENV = "VOXLOGICA_SERVE_LOAD_DIR"
MAX_PLAYGROUND_PROGRAM_BYTES = 2 * 1024 * 1024
MAX_RESULT_LIST_LIMIT = 300
MAX_RESULT_PREVIEW_ITEMS = 16
MAX_RESULT_PREVIEW_DEPTH = 3
MAX_INLINE_STRING_CHARS = 4096

_PLAYGROUND_COMMENT = re.compile(
    r"<!--\s*vox:playground(?P<meta>.*?)-->\s*```imgql\s*(?P<code>.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _iso_utc(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_playground_meta(raw_meta: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip().strip('"').strip("'")
        if key:
            meta[key] = value
    return meta


def parse_playground_examples(markdown: str) -> list[dict[str, Any]]:
    """Extract runnable examples from markdown comment+fence directives."""
    examples: list[dict[str, Any]] = []
    for index, match in enumerate(_PLAYGROUND_COMMENT.finditer(markdown), start=1):
        meta = _parse_playground_meta(match.group("meta"))
        code = match.group("code").strip()
        example_id = meta.get("id") or f"example-{index:03d}"
        title = meta.get("title") or f"Example {index}"
        module = meta.get("module") or "general"
        level = meta.get("level") or "core"
        description = meta.get("description") or ""
        strategy = meta.get("strategy") or "strict"
        examples.append(
            {
                "id": example_id,
                "title": title,
                "module": module,
                "level": level,
                "description": description,
                "strategy": strategy,
                "code": code,
            }
        )
    return examples


def load_gallery_document(path: Path = DOC_GALLERY_PATH) -> dict[str, Any]:
    """Load gallery markdown and parsed playground examples."""
    if not path.exists():
        return {
            "available": False,
            "path": str(path),
            "markdown": "",
            "examples": [],
            "modules": [],
            "updated_at": None,
        }

    markdown = path.read_text(encoding="utf-8")
    examples = parse_playground_examples(markdown)
    modules = sorted({str(example["module"]) for example in examples})
    return {
        "available": True,
        "path": str(path),
        "markdown": markdown,
        "examples": examples,
        "modules": modules,
        "updated_at": _iso_utc(path.stat().st_mtime),
    }


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _load_junit_report(path: Path = JUNIT_REPORT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "path": str(path),
            "error": f"Unable to parse junit.xml: {exc}",
        }
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        suites = [root]

    total = 0
    failures = 0
    errors = 0
    skipped = 0
    duration = 0.0
    failed_cases: list[dict[str, str]] = []
    slow_cases: list[dict[str, Any]] = []
    test_cases: list[dict[str, Any]] = []

    for suite in suites:
        total += _safe_int(suite.attrib.get("tests", 0))
        failures += _safe_int(suite.attrib.get("failures", 0))
        errors += _safe_int(suite.attrib.get("errors", 0))
        skipped += _safe_int(suite.attrib.get("skipped", 0))
        duration += _safe_float(suite.attrib.get("time", 0))

    for case in root.iter("testcase"):
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "")
        case_time = _safe_float(case.attrib.get("time", 0))
        record = {
            "id": f"{classname}::{name}" if classname else name,
            "classname": classname,
            "name": name,
            "time_s": case_time,
        }
        slow_cases.append(record)
        failure = case.find("failure")
        error = case.find("error")
        skipped_node = case.find("skipped")
        status_name = "passed"
        if skipped_node is not None:
            status_name = "skipped"
        if failure is not None or error is not None:
            status_name = "failed"
        test_cases.append({**record, "status": status_name})
        if failure is not None or error is not None:
            node = failure if failure is not None else error
            message = "" if node is None else str(node.attrib.get("message", ""))
            failed_cases.append({**record, "message": message[:240]})

    slow_cases.sort(key=lambda item: float(item["time_s"]), reverse=True)
    passed = max(0, total - failures - errors - skipped)
    return {
        "available": True,
        "path": str(path),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failures + errors,
            "failures": failures,
            "errors": errors,
            "skipped": skipped,
            "duration_s": duration,
            "pass_rate": (passed / total) if total else 0.0,
        },
        "failed_cases": failed_cases[:30],
        "slow_cases": slow_cases[:20],
        "test_cases": test_cases[:400],
        "updated_at": _iso_utc(path.stat().st_mtime),
    }


def _load_coverage_report(path: Path = COVERAGE_REPORT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "path": str(path),
            "error": f"Unable to parse coverage.xml: {exc}",
        }
    line_rate = _safe_float(root.attrib.get("line-rate", 0.0))
    branch_rate = _safe_float(root.attrib.get("branch-rate", 0.0))
    lines_valid = _safe_int(root.attrib.get("lines-valid", 0))
    lines_covered = _safe_int(root.attrib.get("lines-covered", 0))
    branches_valid = _safe_int(root.attrib.get("branches-valid", 0))
    branches_covered = _safe_int(root.attrib.get("branches-covered", 0))

    modules: list[dict[str, Any]] = []
    packages_node = root.find("packages")
    if packages_node is not None:
        for pkg in packages_node.findall("package"):
            pkg_name = str(pkg.attrib.get("name", ""))
            pkg_line_rate = _safe_float(pkg.attrib.get("line-rate", 0.0))
            modules.append(
                {
                    "name": pkg_name,
                    "line_rate": pkg_line_rate,
                    "line_percent": pkg_line_rate * 100.0,
                }
            )
    modules.sort(key=lambda item: float(item["line_rate"]))
    return {
        "available": True,
        "path": str(path),
        "summary": {
            "line_rate": line_rate,
            "line_percent": line_rate * 100.0,
            "branch_rate": branch_rate,
            "branch_percent": branch_rate * 100.0,
            "lines_valid": lines_valid,
            "lines_covered": lines_covered,
            "branches_valid": branches_valid,
            "branches_covered": branches_covered,
        },
        "lowest_modules": modules[:20],
        "updated_at": _iso_utc(path.stat().st_mtime),
    }


def _load_perf_report(json_path: Path = PERF_REPORT_JSON, svg_path: Path = PERF_REPORT_SVG) -> dict[str, Any]:
    if not json_path.exists():
        return {
            "available": False,
            "json_path": str(json_path),
            "svg_path": str(svg_path),
        }
    try:
        import json

        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "json_path": str(json_path),
            "svg_path": str(svg_path),
            "error": f"Invalid perf report JSON: {exc}",
        }
    payload["available"] = True
    payload["json_path"] = str(json_path)
    payload["svg_path"] = str(svg_path)
    payload["svg_available"] = svg_path.exists()
    payload["updated_at"] = _iso_utc(json_path.stat().st_mtime)
    primitive_json = json_path.parent / "primitive_benchmarks.json"
    primitive_svg = json_path.parent / "primitive_benchmarks.svg"
    if primitive_json.exists():
        try:
            primitive_payload = json.loads(primitive_json.read_text(encoding="utf-8"))
            payload["primitive_benchmarks"] = {
                "available": True,
                "cases": primitive_payload.get("cases", []),
                "json_path": str(primitive_json),
                "svg_path": str(primitive_svg),
                "svg_available": primitive_svg.exists(),
                "updated_at": _iso_utc(primitive_json.stat().st_mtime),
            }
        except Exception as exc:  # noqa: BLE001
            payload["primitive_benchmarks"] = {
                "available": False,
                "error": f"Invalid primitive benchmark JSON: {exc}",
                "json_path": str(primitive_json),
                "svg_path": str(primitive_svg),
            }
    else:
        payload["primitive_benchmarks"] = {
            "available": False,
            "json_path": str(primitive_json),
            "svg_path": str(primitive_svg),
            "reason": "Report file not generated yet. Run perf tests and check job logs.",
        }
    perf_test_metrics_json = json_path.parent / "perf_test_metrics.json"
    if perf_test_metrics_json.exists():
        try:
            metrics_payload = json.loads(perf_test_metrics_json.read_text(encoding="utf-8"))
            tests = metrics_payload.get("tests", [])
            payload["test_metrics"] = {
                "available": True,
                "count": int(metrics_payload.get("count", len(tests))),
                "tests": tests,
                "json_path": str(perf_test_metrics_json),
                "updated_at": _iso_utc(perf_test_metrics_json.stat().st_mtime),
            }
        except Exception as exc:  # noqa: BLE001
            payload["test_metrics"] = {
                "available": False,
                "json_path": str(perf_test_metrics_json),
                "error": f"Invalid perf test metrics JSON: {exc}",
            }
    else:
        payload["test_metrics"] = {
            "available": False,
            "json_path": str(perf_test_metrics_json),
            "reason": "Perf telemetry not generated yet. Run perf tests.",
        }
    return payload


def build_test_dashboard_snapshot(reports_dir: Path = TEST_REPORTS_DIR) -> dict[str, Any]:
    """Aggregate test, coverage, and perf report artifacts."""
    junit = _load_junit_report(reports_dir / "junit.xml")
    coverage = _load_coverage_report(reports_dir / "coverage.xml")
    perf = _load_perf_report(
        reports_dir / "perf" / "vox1_vs_vox2_perf.json",
        reports_dir / "perf" / "vox1_vs_vox2_perf.svg",
    )
    available = bool(junit.get("available") or coverage.get("available") or perf.get("available"))
    return {
        "available": available,
        "reports_dir": str(reports_dir),
        "junit": junit,
        "coverage": coverage,
        "performance": perf,
        "generated_at": _iso_utc(time.time()),
    }


def build_storage_stats_snapshot(storage: Any) -> dict[str, Any]:
    """Summarize persistent cache/storage statistics for the dashboard."""
    db_path = Path(getattr(storage, "db_path", "")) if hasattr(storage, "db_path") else None
    if db_path is None or str(db_path) == ".":
        return {"available": False, "error": "Shared storage backend has no SQLite path"}
    if not db_path.exists():
        return {
            "available": False,
            "db_path": str(db_path),
            "error": "Storage database has not been created yet",
        }

    wal_path = db_path.with_suffix(f"{db_path.suffix}-wal")
    shm_path = db_path.with_suffix(f"{db_path.suffix}-shm")
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'materialized' THEN 1 ELSE 0 END) AS materialized,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    AVG(LENGTH(payload)) AS avg_payload_bytes,
                    SUM(LENGTH(payload)) AS payload_bytes,
                    AVG(LENGTH(metadata_json)) AS avg_metadata_bytes,
                    MIN(updated_at) AS first_update,
                    MAX(updated_at) AS last_update
                FROM results
                """
            ).fetchone()
            versions = conn.execute(
                """
                SELECT runtime_version, COUNT(*) AS count
                FROM results
                GROUP BY runtime_version
                ORDER BY count DESC, runtime_version ASC
                LIMIT 15
                """
            ).fetchall()
            buckets = conn.execute(
                """
                SELECT
                    CASE
                        WHEN payload IS NULL THEN 'none'
                        WHEN LENGTH(payload) < 1024 THEN '<1KB'
                        WHEN LENGTH(payload) < 10240 THEN '1KB-10KB'
                        WHEN LENGTH(payload) < 102400 THEN '10KB-100KB'
                        WHEN LENGTH(payload) < 1048576 THEN '100KB-1MB'
                        ELSE '>=1MB'
                    END AS bucket,
                    COUNT(*) AS count
                FROM results
                GROUP BY bucket
                ORDER BY count DESC
                """
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "db_path": str(db_path),
            "error": f"Unable to read storage stats: {exc}",
        }

    total = _safe_int(totals["total"]) if totals is not None else 0
    materialized = _safe_int(totals["materialized"]) if totals is not None else 0
    failed = _safe_int(totals["failed"]) if totals is not None else 0
    avg_payload = _safe_float(totals["avg_payload_bytes"]) if totals is not None else 0.0
    payload_bytes = _safe_int(totals["payload_bytes"]) if totals is not None else 0
    avg_metadata = _safe_float(totals["avg_metadata_bytes"]) if totals is not None else 0.0
    first_update = _safe_float(totals["first_update"]) if totals is not None else 0.0
    last_update = _safe_float(totals["last_update"]) if totals is not None else 0.0
    return {
        "available": True,
        "db_path": str(db_path),
        "summary": {
            "total_records": total,
            "materialized_records": materialized,
            "failed_records": failed,
            "hit_ready_ratio": (materialized / total) if total else 0.0,
            "avg_payload_bytes": avg_payload,
            "total_payload_bytes": payload_bytes,
            "avg_metadata_bytes": avg_metadata,
            "first_update_at": _iso_utc(first_update) if first_update > 0 else None,
            "last_update_at": _iso_utc(last_update) if last_update > 0 else None,
        },
        "disk": {
            "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "wal_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
            "shm_bytes": shm_path.stat().st_size if shm_path.exists() else 0,
        },
        "runtime_versions": [
            {"runtime_version": str(row["runtime_version"]), "count": _safe_int(row["count"])}
            for row in versions
        ],
        "payload_buckets": [
            {"bucket": str(row["bucket"]), "count": _safe_int(row["count"])}
            for row in buckets
        ],
        "generated_at": _iso_utc(time.time()),
    }


def resolve_playground_load_directory() -> Path:
    """Resolve the fixed directory used by the UI for loading programs."""
    configured = os.environ.get(PLAYGROUND_LOAD_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_PLAYGROUND_LOAD_DIR.resolve()


def list_playground_programs(*, limit: int = 400) -> dict[str, Any]:
    """List `.imgql` files under the fixed UI load directory."""
    load_dir = resolve_playground_load_directory()
    if not load_dir.exists():
        return {
            "available": False,
            "load_dir": str(load_dir),
            "error": f"Load directory does not exist: {load_dir}",
            "files": [],
        }
    if not load_dir.is_dir():
        return {
            "available": False,
            "load_dir": str(load_dir),
            "error": f"Load directory is not a directory: {load_dir}",
            "files": [],
        }

    safe_limit = max(1, min(int(limit), 1000))
    entries: list[dict[str, Any]] = []
    for path in sorted(load_dir.rglob("*.imgql")):
        if len(entries) >= safe_limit:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(load_dir).as_posix()
        stat = path.stat()
        entries.append(
            {
                "path": rel,
                "bytes": int(stat.st_size),
                "updated_at": _iso_utc(stat.st_mtime),
            }
        )
    return {
        "available": True,
        "load_dir": str(load_dir),
        "files": entries,
        "truncated": len(entries) >= safe_limit,
        "generated_at": _iso_utc(time.time()),
    }


def _resolve_allowed_program_path(relative_path: str) -> Path:
    load_dir = resolve_playground_load_directory()
    candidate = (load_dir / relative_path).resolve()
    candidate.relative_to(load_dir)
    if candidate.suffix.lower() != ".imgql":
        raise ValueError("Only .imgql files can be loaded from the playground library")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"Program file not found: {relative_path}")
    return candidate


def load_playground_program(relative_path: str) -> dict[str, Any]:
    """Load one `.imgql` file from the fixed UI load directory."""
    try:
        candidate = _resolve_allowed_program_path(relative_path)
    except ValueError as exc:
        raise ValueError(f"Invalid load path: {exc}") from exc

    size = int(candidate.stat().st_size)
    if size > MAX_PLAYGROUND_PROGRAM_BYTES:
        raise ValueError(
            f"Program file too large ({size} bytes). "
            f"Max supported is {MAX_PLAYGROUND_PROGRAM_BYTES} bytes."
        )
    return {
        "available": True,
        "load_dir": str(resolve_playground_load_directory()),
        "path": candidate.relative_to(resolve_playground_load_directory()).as_posix(),
        "absolute_path": str(candidate),
        "content": candidate.read_text(encoding="utf-8"),
        "bytes": size,
        "updated_at": _iso_utc(candidate.stat().st_mtime),
    }


def _storage_db_path(storage: Any) -> Path | None:
    db_attr = getattr(storage, "db_path", None)
    if db_attr is None:
        return None
    try:
        path = Path(db_attr).expanduser().resolve()
    except Exception:
        return None
    return path


def _result_runtime_filter(storage: Any) -> str | None:
    runtime_version = getattr(storage, "runtime_version", None)
    if runtime_version is None:
        return None
    runtime_text = str(runtime_version).strip()
    return runtime_text or None


def list_store_results_snapshot(
    storage: Any,
    *,
    limit: int = 120,
    status_filter: str | None = None,
    node_filter: str | None = None,
) -> dict[str, Any]:
    """List cached result records available for UI inspection."""
    db_path = _storage_db_path(storage)
    if db_path is None:
        return {
            "available": False,
            "error": "Storage backend does not expose a SQLite path",
            "records": [],
        }
    if not db_path.exists():
        return {
            "available": False,
            "db_path": str(db_path),
            "error": "Storage database does not exist yet",
            "records": [],
        }

    safe_limit = max(1, min(int(limit), MAX_RESULT_LIST_LIMIT))
    runtime_filter = _result_runtime_filter(storage)
    where_clauses: list[str] = []
    query_params: list[Any] = []

    if status_filter:
        where_clauses.append("status = ?")
        query_params.append(status_filter)
    if node_filter:
        where_clauses.append("node_id LIKE ?")
        query_params.append(f"%{node_filter}%")
    if runtime_filter:
        where_clauses.append("runtime_version = ?")
        query_params.append(runtime_filter)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    list_sql = f"""
        SELECT
            node_id,
            status,
            runtime_version,
            payload_encoding,
            LENGTH(payload) AS payload_bytes,
            error,
            created_at,
            updated_at
        FROM results
        {where_sql}
        ORDER BY updated_at DESC
        LIMIT ?
    """
    count_sql = f"SELECT COUNT(*) AS total FROM results {where_sql}"
    summary_sql = f"""
        SELECT
            SUM(CASE WHEN status = 'materialized' THEN 1 ELSE 0 END) AS materialized,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM results
        {where_sql}
    """

    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            records = conn.execute(list_sql, [*query_params, safe_limit]).fetchall()
            total_row = conn.execute(count_sql, query_params).fetchone()
            summary_row = conn.execute(summary_sql, query_params).fetchone()
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "db_path": str(db_path),
            "error": f"Unable to query store results: {exc}",
            "records": [],
        }

    payload_records = [
        {
            "node_id": str(row["node_id"]),
            "status": str(row["status"]),
            "runtime_version": str(row["runtime_version"]),
            "payload_encoding": str(row["payload_encoding"]),
            "payload_bytes": _safe_int(row["payload_bytes"]),
            "error": row["error"],
            "created_at": _iso_utc(_safe_float(row["created_at"])),
            "updated_at": _iso_utc(_safe_float(row["updated_at"])),
        }
        for row in records
    ]
    total = _safe_int(total_row["total"]) if total_row is not None else len(payload_records)
    materialized = _safe_int(summary_row["materialized"]) if summary_row is not None else 0
    failed = _safe_int(summary_row["failed"]) if summary_row is not None else 0
    return {
        "available": True,
        "db_path": str(db_path),
        "runtime_version_filter": runtime_filter,
        "status_filter": status_filter,
        "node_filter": node_filter,
        "records": payload_records,
        "summary": {
            "total": total,
            "materialized": materialized,
            "failed": failed,
        },
        "generated_at": _iso_utc(time.time()),
    }


def _load_store_row(storage: Any, node_id: str) -> sqlite3.Row | None:
    db_path = _storage_db_path(storage)
    if db_path is None or not db_path.exists():
        return None
    runtime_filter = _result_runtime_filter(storage)
    query = """
        SELECT
            node_id,
            status,
            payload,
            payload_encoding,
            error,
            metadata_json,
            runtime_version,
            created_at,
            updated_at
        FROM results
        WHERE node_id = ?
    """
    params: list[Any] = [node_id]
    if runtime_filter:
        query += " AND runtime_version = ?"
        params.append(runtime_filter)
    query += " ORDER BY updated_at DESC LIMIT 1"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchone()


def _decode_metadata(metadata_json: Any) -> dict[str, Any]:
    if not isinstance(metadata_json, str):
        return {}
    try:
        parsed = json.loads(metadata_json)
    except Exception:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _deserialize_payload(payload: Any, encoding: str) -> Any:
    if encoding == "none" or payload is None:
        return None
    if encoding != "pickle":
        raise ValueError(f"Unsupported payload encoding: {encoding}")
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("Invalid payload for pickle decoding")
    return pickle.loads(payload)


def _normalize_result_path(path: str | None) -> str:
    raw = (path or "").strip()
    if not raw or raw == "/":
        return ""
    if raw.startswith("/"):
        return raw
    return f"/{raw}"


def _decode_path_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _encode_path_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _append_path(base: str, token: str) -> str:
    encoded = _encode_path_token(token)
    if not base:
        return f"/{encoded}"
    return f"{base}/{encoded}"


def _resolve_value_path(value: Any, path: str) -> Any:
    normalized = _normalize_result_path(path)
    if not normalized:
        return value
    current = value
    for token in [part for part in normalized.split("/") if part]:
        key = _decode_path_token(token)
        if isinstance(current, (list, tuple)):
            try:
                index = int(key)
            except ValueError as exc:
                raise KeyError(f"Invalid list index '{key}' in path '{path}'") from exc
            if index < 0 or index >= len(current):
                raise KeyError(f"List index out of range in path '{path}'")
            current = current[index]
            continue
        if isinstance(current, dict):
            if key not in current:
                raise KeyError(f"Missing key '{key}' in path '{path}'")
            current = current[key]
            continue
        raise KeyError(f"Cannot descend into value type {type(current).__name__} using path '{path}'")
    return current


def _import_numpy() -> Any | None:
    try:
        import numpy as np

        return np
    except Exception:
        return None


def _import_simpleitk() -> Any | None:
    try:
        import SimpleITK as sitk

        return sitk
    except Exception:
        return None


_SITK_IMAGE_REQUIRED_METHODS = (
    "GetDimension",
    "GetSize",
    "GetSpacing",
    "GetOrigin",
    "GetDirection",
    "GetPixelIDTypeAsString",
)


def _is_simpleitk_image_like(value: Any, sitk: Any | None) -> bool:
    if sitk is not None:
        try:
            if isinstance(value, sitk.Image):
                return True
        except Exception:
            pass
    for method_name in _SITK_IMAGE_REQUIRED_METHODS:
        method = getattr(value, method_name, None)
        if not callable(method):
            return False
    return True


def _simpleitk_image_metadata(value: Any) -> dict[str, Any] | None:
    try:
        dimension = int(value.GetDimension())
        size = [int(v) for v in value.GetSize()]
        spacing = [float(v) for v in value.GetSpacing()]
        origin = [float(v) for v in value.GetOrigin()]
        direction = [float(v) for v in value.GetDirection()]
        pixel_id = str(value.GetPixelIDTypeAsString())
    except Exception:
        return None
    return {
        "dimension": dimension,
        "size": size,
        "spacing": spacing,
        "origin": origin,
        "direction": direction,
        "pixel_id": pixel_id,
    }


def _coerce_simpleitk_image(value: Any, sitk: Any) -> Any:
    try:
        if isinstance(value, sitk.Image):
            return value
    except Exception:
        pass

    try:
        coerced = sitk.Image(value)
        if isinstance(coerced, sitk.Image):
            return coerced
    except Exception:
        pass

    try:
        array = sitk.GetArrayFromImage(value)
        coerced = sitk.GetImageFromArray(array)
        try:
            coerced.CopyInformation(value)
        except Exception:
            pass
        return coerced
    except Exception as exc:
        raise ValueError(f"Unsupported SimpleITK image value: {type(value).__name__}") from exc


def _build_render_url(node_id: str, render_kind: str, path: str) -> str:
    encoded_node = quote(node_id, safe="")
    suffix = ""
    normalized = _normalize_result_path(path)
    if normalized:
        suffix = f"?path={quote(normalized, safe='')}"
    return f"/api/v1/results/store/{encoded_node}/render/{render_kind}{suffix}"


def _string_preview(value: str) -> dict[str, Any]:
    if len(value) <= MAX_INLINE_STRING_CHARS:
        return {"text": value, "truncated": False}
    return {
        "text": value[:MAX_INLINE_STRING_CHARS],
        "truncated": True,
        "total_chars": len(value),
    }


def _sequence_numeric_preview(value: list[Any] | tuple[Any, ...]) -> list[float] | None:
    if len(value) > 2048:
        return None
    out: list[float] = []
    for item in value:
        if isinstance(item, bool):
            out.append(1.0 if item else 0.0)
            continue
        if isinstance(item, (int, float)):
            out.append(float(item))
            continue
        return None
    return out


def _ndarray_stats(array: Any) -> dict[str, Any]:
    np = _import_numpy()
    if np is None:
        return {}
    if array.size == 0:
        return {"empty": True}
    if not np.issubdtype(array.dtype, np.number):
        return {}
    sample = array.reshape(-1)
    if sample.size > 1_000_000:
        step = max(1, sample.size // 1_000_000)
        sample = sample[::step]
    finite = sample
    if np.issubdtype(sample.dtype, np.floating):
        finite = sample[np.isfinite(sample)]
        if finite.size == 0:
            return {"has_finite": False}
    return {
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
        "mean": float(np.mean(finite)),
    }


def _describe_value(
    value: Any,
    *,
    node_id: str,
    path: str,
    depth: int = 0,
    seen_ids: set[int] | None = None,
) -> dict[str, Any]:
    np = _import_numpy()
    sitk = _import_simpleitk()
    seen = seen_ids if seen_ids is not None else set()

    if depth > MAX_RESULT_PREVIEW_DEPTH:
        return {"kind": "truncated", "reason": "depth-limit"}

    if value is None:
        return {"kind": "null", "value": None}
    if isinstance(value, bool):
        return {"kind": "boolean", "value": value}
    if isinstance(value, int):
        return {"kind": "integer", "value": value}
    if isinstance(value, float):
        return {"kind": "number", "value": value}
    if isinstance(value, str):
        return {
            "kind": "string",
            "length": len(value),
            "preview": _string_preview(value),
        }
    if isinstance(value, bytes):
        return {"kind": "bytes", "length": len(value)}

    if np is not None and isinstance(value, np.generic):
        return _describe_value(
            value.item(),
            node_id=node_id,
            path=path,
            depth=depth,
            seen_ids=seen,
        )

    tracked = isinstance(value, (list, tuple, dict))
    if np is not None and isinstance(value, np.ndarray):
        tracked = True
    if _is_simpleitk_image_like(value, sitk):
        tracked = True

    if tracked:
        pointer = id(value)
        if pointer in seen:
            return {"kind": "cycle", "type": type(value).__name__}
        seen.add(pointer)

    if np is not None and isinstance(value, np.ndarray):
        summary = {
            "kind": "ndarray",
            "dtype": str(value.dtype),
            "shape": [int(v) for v in value.shape],
            "size": int(value.size),
            "stats": _ndarray_stats(value),
        }
        if value.ndim == 1 and value.size <= 4096:
            summary["values"] = value.tolist()
        if value.ndim == 2:
            summary["render"] = {
                "kind": "image2d",
                "png_url": _build_render_url(node_id, "png", path),
            }
        elif value.ndim == 3:
            summary["render"] = {
                "kind": "medical-volume",
                "nifti_url": _build_render_url(node_id, "nii", path),
            }
        return summary

    if _is_simpleitk_image_like(value, sitk):
        metadata = _simpleitk_image_metadata(value)
        if metadata is None:
            return {
                "kind": "object",
                "type": f"{type(value).__module__}.{type(value).__name__}",
                "repr": repr(value)[:MAX_INLINE_STRING_CHARS],
            }
        summary = {
            "kind": "simpleitk-image",
            "dimension": metadata["dimension"],
            "size": metadata["size"],
            "spacing": metadata["spacing"],
            "origin": metadata["origin"],
            "direction": metadata["direction"],
            "pixel_id": metadata["pixel_id"],
        }
        if int(metadata["dimension"]) == 2:
            summary["render"] = {
                "kind": "image2d",
                "png_url": _build_render_url(node_id, "png", path),
            }
        elif int(metadata["dimension"]) >= 3:
            summary["render"] = {
                "kind": "medical-volume",
                "nifti_url": _build_render_url(node_id, "nii", path),
            }
        return summary

    if isinstance(value, (list, tuple)):
        sequence: list[Any] | tuple[Any, ...] = value
        payload: dict[str, Any] = {
            "kind": "sequence",
            "sequence_type": type(sequence).__name__,
            "length": len(sequence),
        }
        numeric = _sequence_numeric_preview(sequence)
        if numeric is not None:
            payload["numeric_values"] = numeric
        if depth < MAX_RESULT_PREVIEW_DEPTH and sequence:
            items: list[dict[str, Any]] = []
            for index, item in enumerate(sequence[:MAX_RESULT_PREVIEW_ITEMS]):
                child_path = _append_path(path, str(index))
                items.append(
                    {
                        "label": f"[{index}]",
                        "path": child_path,
                        "summary": _describe_value(
                            item,
                            node_id=node_id,
                            path=child_path,
                            depth=depth + 1,
                            seen_ids=seen,
                        ),
                    }
                )
            payload["items"] = items
            payload["truncated"] = len(sequence) > MAX_RESULT_PREVIEW_ITEMS
        return payload

    if isinstance(value, dict):
        keys = list(value.keys())
        key_preview = [str(k) for k in keys[:MAX_RESULT_PREVIEW_ITEMS]]
        payload = {
            "kind": "mapping",
            "mapping_type": type(value).__name__,
            "length": len(value),
            "keys": key_preview,
            "truncated": len(keys) > MAX_RESULT_PREVIEW_ITEMS,
        }
        if depth < MAX_RESULT_PREVIEW_DEPTH and keys:
            entries: list[dict[str, Any]] = []
            for key in keys[:MAX_RESULT_PREVIEW_ITEMS]:
                child_path = _append_path(path, str(key))
                entries.append(
                    {
                        "label": str(key),
                        "path": child_path,
                        "summary": _describe_value(
                            value[key],
                            node_id=node_id,
                            path=child_path,
                            depth=depth + 1,
                            seen_ids=seen,
                        ),
                    }
                )
            payload["entries"] = entries
        return payload

    return {
        "kind": "object",
        "type": f"{type(value).__module__}.{type(value).__name__}",
        "repr": repr(value)[:MAX_INLINE_STRING_CHARS],
    }


def _load_store_materialized_value(storage: Any, node_id: str) -> tuple[sqlite3.Row, Any]:
    row = _load_store_row(storage, node_id)
    if row is None:
        raise KeyError(f"Unknown store result: {node_id}")
    status = str(row["status"])
    if status != "materialized":
        raise ValueError(f"Store result '{node_id}' has status '{status}', not materialized")
    value = _deserialize_payload(row["payload"], str(row["payload_encoding"]))
    return row, value


def inspect_store_result(storage: Any, *, node_id: str, path: str = "") -> dict[str, Any]:
    """Inspect one stored result value (or one sub-path inside it)."""
    row = _load_store_row(storage, node_id)
    if row is None:
        raise KeyError(f"Unknown store result: {node_id}")

    status = str(row["status"])
    payload: dict[str, Any] = {
        "available": True,
        "node_id": str(row["node_id"]),
        "status": status,
        "runtime_version": str(row["runtime_version"]),
        "created_at": _iso_utc(_safe_float(row["created_at"])),
        "updated_at": _iso_utc(_safe_float(row["updated_at"])),
        "metadata": _decode_metadata(row["metadata_json"]),
        "error": row["error"],
        "path": _normalize_result_path(path),
    }
    if status != "materialized":
        payload["descriptor"] = {"kind": "unavailable", "reason": f"status={status}"}
        return payload

    value = _deserialize_payload(row["payload"], str(row["payload_encoding"]))
    target = _resolve_value_path(value, path)
    payload["descriptor"] = _describe_value(
        target,
        node_id=node_id,
        path=_normalize_result_path(path),
    )
    return payload


def describe_runtime_value(*, node_id: str, value: Any, path: str = "") -> dict[str, Any]:
    """Describe an in-memory value using the same schema as store inspection."""
    normalized = _normalize_result_path(path)
    target = _resolve_value_path(value, normalized)
    return {
        "available": True,
        "node_id": node_id,
        "status": "materialized",
        "runtime_version": "runtime",
        "created_at": _iso_utc(time.time()),
        "updated_at": _iso_utc(time.time()),
        "metadata": {"source": "runtime"},
        "error": None,
        "path": normalized,
        "descriptor": _describe_value(target, node_id=node_id, path=normalized),
    }


def _image_to_png_bytes(image: Any) -> bytes:
    sitk = _import_simpleitk()
    if sitk is None:
        raise RuntimeError("SimpleITK is not available")
    scaled = sitk.RescaleIntensity(sitk.Cast(image, sitk.sitkFloat32), 0.0, 255.0)
    scaled = sitk.Cast(scaled, sitk.sitkUInt8)
    fd, tmp_name = tempfile.mkstemp(prefix="vox-result-", suffix=".png")
    os.close(fd)
    path = Path(tmp_name)
    try:
        sitk.WriteImage(scaled, str(path))
        return path.read_bytes()
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _image_to_nifti_bytes(image: Any, *, gzipped: bool = True) -> bytes:
    sitk = _import_simpleitk()
    if sitk is None:
        raise RuntimeError("SimpleITK is not available")
    suffix = ".nii.gz" if gzipped else ".nii"
    fd, tmp_name = tempfile.mkstemp(prefix="vox-result-", suffix=suffix)
    os.close(fd)
    path = Path(tmp_name)
    try:
        sitk.WriteImage(image, str(path))
        return path.read_bytes()
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _to_image2d(value: Any) -> Any:
    sitk = _import_simpleitk()
    np = _import_numpy()
    if sitk is None:
        raise RuntimeError("SimpleITK is not available")
    if _is_simpleitk_image_like(value, sitk):
        metadata = _simpleitk_image_metadata(value)
        if metadata is None:
            raise ValueError(f"Unsupported value type for PNG rendering: {type(value).__name__}")
        if int(metadata["dimension"]) != 2:
            raise ValueError("Only 2D images can be rendered as PNG")
        return _coerce_simpleitk_image(value, sitk)
    if np is not None and isinstance(value, np.ndarray):
        if value.ndim != 2:
            raise ValueError("Only 2D arrays can be rendered as PNG")
        return sitk.GetImageFromArray(value)
    if isinstance(value, (list, tuple)):
        if np is None:
            raise ValueError("NumPy unavailable for list-to-image conversion")
        arr = np.asarray(value)
        if arr.ndim != 2:
            raise ValueError("Only 2D array-like values can be rendered as PNG")
        return sitk.GetImageFromArray(arr)
    raise ValueError(f"Unsupported value type for PNG rendering: {type(value).__name__}")


def _to_image3d(value: Any) -> Any:
    sitk = _import_simpleitk()
    np = _import_numpy()
    if sitk is None:
        raise RuntimeError("SimpleITK is not available")
    if _is_simpleitk_image_like(value, sitk):
        metadata = _simpleitk_image_metadata(value)
        if metadata is None:
            raise ValueError(f"Unsupported value type for NIfTI rendering: {type(value).__name__}")
        if int(metadata["dimension"]) != 3:
            raise ValueError("Only 3D images can be rendered as NIfTI")
        return _coerce_simpleitk_image(value, sitk)
    if np is not None and isinstance(value, np.ndarray):
        if value.ndim != 3:
            raise ValueError("Only 3D arrays can be rendered as NIfTI")
        return sitk.GetImageFromArray(value)
    if isinstance(value, (list, tuple)):
        if np is None:
            raise ValueError("NumPy unavailable for list-to-image conversion")
        arr = np.asarray(value)
        if arr.ndim != 3:
            raise ValueError("Only 3D array-like values can be rendered as NIfTI")
        return sitk.GetImageFromArray(arr)
    raise ValueError(f"Unsupported value type for NIfTI rendering: {type(value).__name__}")


def render_store_result_png(storage: Any, *, node_id: str, path: str = "") -> bytes:
    """Render one stored result (or sub-value) as PNG bytes."""
    _row, value = _load_store_materialized_value(storage, node_id)
    target = _resolve_value_path(value, path)
    image = _to_image2d(target)
    return _image_to_png_bytes(image)


def render_store_result_nifti_gz(storage: Any, *, node_id: str, path: str = "") -> bytes:
    """Render one stored result (or sub-value) as gzipped NIfTI bytes."""
    _row, value = _load_store_materialized_value(storage, node_id)
    target = _resolve_value_path(value, path)
    image = _to_image3d(target)
    return _image_to_nifti_bytes(image, gzipped=True)


def render_store_result_nifti(storage: Any, *, node_id: str, path: str = "") -> bytes:
    """Render one stored result (or sub-value) as uncompressed NIfTI bytes."""
    _row, value = _load_store_materialized_value(storage, node_id)
    target = _resolve_value_path(value, path)
    image = _to_image3d(target)
    return _image_to_nifti_bytes(image, gzipped=False)


def _ru_maxrss_bytes() -> int:
    import resource

    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return int(value)
    return int(value * 1024.0)


def _playground_worker(send_conn: Any, request_payload: dict[str, Any], log_path_str: str) -> None:
    from voxlogica.features import handle_run

    log_path = Path(log_path_str)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    def _append_log(payload: dict[str, Any]) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    started_at = time.time()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    rss_before = _ru_maxrss_bytes()
    import tracemalloc

    tracemalloc.start()
    packet: dict[str, Any] = {"ok": False, "error": "Unknown worker failure"}
    try:
        _append_log(
            {
                "event": "playground.run.started",
                "started_at": _iso_utc(started_at),
                "request": {
                    "execution_strategy": request_payload.get("execution_strategy", "dask"),
                    "execute": bool(request_payload.get("execute", True)),
                    "no_cache": bool(request_payload.get("no_cache", False)),
                    "program_chars": len(str(request_payload.get("program", ""))),
                    "job_kind": request_payload.get("_job_kind", "run"),
                    "priority_node": request_payload.get("_priority_node"),
                },
            }
        )
        run_result = handle_run(**request_payload, _include_execution_events=True)
        if run_result.success:
            result_payload = run_result.data or {}
            execution_payload = result_payload.get("execution", {})
            if not isinstance(execution_payload, dict):
                execution_payload = {}
                result_payload["execution"] = execution_payload
            execution_summary = (
                dict(execution_payload)
                if isinstance(execution_payload, dict)
                else {}
            )
            node_events = execution_payload.get("node_events", [])
            if isinstance(node_events, list):
                for index, node_event in enumerate(node_events):
                    if not isinstance(node_event, dict):
                        continue
                    _append_log(
                        {
                            "event": "playground.node",
                            "index": index + 1,
                            **node_event,
                        }
                    )
            execution_payload.pop("node_events", None)
            execution_summary.pop("node_events", None)
            packet = {"ok": True, "result": result_payload}
            _append_log(
                {
                    "event": "playground.run.completed",
                    "success": True,
                    "result_summary": {
                        "operations": result_payload.get("operations"),
                        "goals": result_payload.get("goals"),
                        "execution": execution_summary,
                        "execution_cache_summary": execution_payload.get("cache_summary", {}),
                        "execution_event_count": len(node_events) if isinstance(node_events, list) else 0,
                    },
                }
            )
        else:
            result_payload = run_result.data if isinstance(run_result.data, dict) else {}
            execution_payload = result_payload.get("execution", {})
            if not isinstance(execution_payload, dict):
                execution_payload = {}
                result_payload["execution"] = execution_payload
            execution_summary = (
                dict(execution_payload)
                if isinstance(execution_payload, dict)
                else {}
            )
            node_events = execution_payload.get("node_events", [])
            if isinstance(node_events, list):
                for index, node_event in enumerate(node_events):
                    if not isinstance(node_event, dict):
                        continue
                    _append_log(
                        {
                            "event": "playground.node",
                            "index": index + 1,
                            **node_event,
                        }
                    )
            execution_payload.pop("node_events", None)
            execution_summary.pop("node_events", None)
            packet = {
                "ok": False,
                "error": run_result.error or "Execution failed",
                "result": result_payload,
            }
            _append_log(
                {
                    "event": "playground.run.completed",
                    "success": False,
                    "error": packet["error"],
                    "result_summary": {
                        "operations": result_payload.get("operations"),
                        "goals": result_payload.get("goals"),
                        "execution": execution_summary,
                        "execution_cache_summary": execution_payload.get("cache_summary", {}),
                        "execution_event_count": len(node_events) if isinstance(node_events, list) else 0,
                    },
                }
            )
    except Exception as exc:  # noqa: BLE001
        packet = {
            "ok": False,
            "error": str(exc),
        }
        _append_log(
            {
                "event": "playground.run.exception",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=15),
            }
        )
    finally:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        wall_s = time.perf_counter() - wall_start
        cpu_s = time.process_time() - cpu_start
        rss_after = _ru_maxrss_bytes()
        finished_at = time.time()
        packet["metrics"] = {
            "wall_time_s": wall_s,
            "cpu_time_s": cpu_s,
            "cpu_utilization": (cpu_s / wall_s) if wall_s > 0 else 0.0,
            "ru_maxrss_before_bytes": rss_before,
            "ru_maxrss_after_bytes": rss_after,
            "ru_maxrss_delta_bytes": max(0, rss_after - rss_before),
            "python_heap_current_bytes": int(current_bytes),
            "python_heap_peak_bytes": int(peak_bytes),
        }
        packet["started_at"] = started_at
        packet["finished_at"] = finished_at
        _append_log(
            {
                "event": "playground.run.metrics",
                "metrics": packet["metrics"],
                "finished_at": _iso_utc(finished_at),
            }
        )
        try:
            send_conn.send(packet)
        except (BrokenPipeError, EOFError, OSError):
            _append_log(
                {
                    "event": "playground.run.send_failed",
                    "error": "Result pipe closed before payload delivery",
                }
            )
        finally:
            try:
                send_conn.close()
            except Exception:
                pass


@dataclass
class PlaygroundJob:
    job_id: str
    request_payload: dict[str, Any]
    created_at: float
    status: str = "queued"
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    process: mp.Process | None = None
    recv_conn: Any = None
    log_path: Path | None = None

    def as_public(
        self,
        include_result: bool = True,
        *,
        include_log_tail: bool = False,
        tail_lines: int = 120,
    ) -> dict[str, Any]:
        payload = {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": _iso_utc(self.created_at),
            "started_at": _iso_utc(self.started_at),
            "finished_at": _iso_utc(self.finished_at),
            "request": {
                "execution_strategy": self.request_payload.get("execution_strategy", "dask"),
                "execute": bool(self.request_payload.get("execute", True)),
                "no_cache": bool(self.request_payload.get("no_cache", False)),
                "job_kind": self.request_payload.get("_job_kind", "run"),
                "priority_node": self.request_payload.get("_priority_node"),
            },
            "metrics": self.metrics,
            "error": self.error,
            "log_path": str(self.log_path) if self.log_path is not None else None,
        }
        if include_result:
            payload["result"] = self.result
        if include_log_tail:
            payload["log_tail"] = _read_log_tail(self.log_path, lines=tail_lines)
        return payload


class PlaygroundJobManager:
    """Multiprocess playground execution manager with explicit kill semantics."""

    def __init__(
        self,
        *,
        stale_seconds: float = 900.0,
        retain_seconds: float = 24 * 3600.0,
        max_jobs: int = 200,
    ):
        self._ctx = mp.get_context("spawn")
        self._lock = threading.RLock()
        self._jobs: dict[str, PlaygroundJob] = {}
        self._stale_seconds = stale_seconds
        self._retain_seconds = retain_seconds
        self._max_jobs = max_jobs

    def _poll_locked(self, job: PlaygroundJob) -> None:
        if job.status in {"completed", "failed", "killed"}:
            return

        process = job.process
        recv_conn = job.recv_conn
        if recv_conn is not None and recv_conn.poll():
            try:
                packet = recv_conn.recv()
            except (EOFError, OSError):
                job.status = "failed"
                job.error = "Execution process terminated before delivering result payload"
                job.finished_at = time.time()
                packet = None
            if isinstance(packet, dict):
                job.started_at = _safe_float(packet.get("started_at", job.started_at or time.time()))
                job.finished_at = _safe_float(packet.get("finished_at", time.time()))
                job.metrics = dict(packet.get("metrics", {}))
                if bool(packet.get("ok")):
                    job.status = "completed"
                    result = packet.get("result")
                    job.result = result if isinstance(result, dict) else {"payload": result}
                else:
                    job.status = "failed"
                    job.error = str(packet.get("error", "Execution failed"))
                    result = packet.get("result")
                    if isinstance(result, dict):
                        job.result = result
            try:
                recv_conn.close()
            except Exception:
                pass
            job.recv_conn = None

        if process is not None and not process.is_alive():
            process.join(timeout=0.0)
            if job.status not in {"completed", "failed", "killed"}:
                job.status = "failed"
                job.error = f"Execution process terminated with exit code {process.exitcode}"
                job.finished_at = time.time()
            job.process = None

    def _cleanup_locked(self) -> None:
        now = time.time()
        for job in list(self._jobs.values()):
            self._poll_locked(job)
            if job.status == "running" and job.started_at is not None:
                if now - job.started_at > self._stale_seconds:
                    self._kill_locked(job, reason="Killed stale computation")

        removable: list[str] = []
        for job_id, job in self._jobs.items():
            if job.status in {"completed", "failed", "killed"} and job.finished_at is not None:
                if now - job.finished_at > self._retain_seconds:
                    removable.append(job_id)

        if len(self._jobs) - len(removable) > self._max_jobs:
            candidates = sorted(
                (
                    (jid, job.finished_at or job.created_at)
                    for jid, job in self._jobs.items()
                    if job.status in {"completed", "failed", "killed"}
                ),
                key=lambda item: item[1],
            )
            for jid, _ in candidates:
                if len(self._jobs) - len(removable) <= self._max_jobs:
                    break
                if jid not in removable:
                    removable.append(jid)

        for job_id in removable:
            self._jobs.pop(job_id, None)

    def _kill_locked(self, job: PlaygroundJob, reason: str) -> None:
        process = job.process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
        if job.recv_conn is not None:
            job.recv_conn.close()
            job.recv_conn = None
        job.process = None
        job.status = "killed"
        job.error = reason
        if job.finished_at is None:
            job.finished_at = time.time()

    def _create_job_locked(self, request_payload: dict[str, Any]) -> PlaygroundJob:
        payload = dict(request_payload)
        payload["execute"] = bool(payload.get("execute", True))
        job_id = uuid.uuid4().hex
        created_at = time.time()
        recv_conn, send_conn = self._ctx.Pipe(duplex=False)
        PLAYGROUND_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = PLAYGROUND_JOB_LOG_DIR / f"{job_id}.log"
        process = self._ctx.Process(
            target=_playground_worker,
            args=(send_conn, payload, str(log_path)),
            daemon=True,
        )

        job = PlaygroundJob(
            job_id=job_id,
            request_payload=payload,
            created_at=created_at,
            status="queued",
            process=process,
            recv_conn=recv_conn,
            log_path=log_path,
        )
        self._jobs[job_id] = job
        try:
            process.start()
        except Exception:
            self._jobs.pop(job_id, None)
            send_conn.close()
            recv_conn.close()
            raise
        send_conn.close()
        job.status = "running"
        job.started_at = time.time()
        return job

    def _find_latest_value_job_locked(
        self,
        *,
        program_hash: str,
        node_id: str,
        execution_strategy: str,
    ) -> PlaygroundJob | None:
        for job in sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True):
            request = job.request_payload
            if str(request.get("_job_kind", "")) != "value-resolve":
                continue
            if str(request.get("_program_hash", "")) != program_hash:
                continue
            if str(request.get("_priority_node", "")) != node_id:
                continue
            if str(request.get("execution_strategy", "dask")) != execution_strategy:
                continue
            return job
        return None

    def create_job(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._cleanup_locked()
            job = self._create_job_locked(request_payload)
            return job.as_public(include_result=False)

    def ensure_value_job(
        self,
        request_payload: dict[str, Any],
        *,
        program_hash: str,
        node_id: str,
        execution_strategy: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._cleanup_locked()
            existing = self._find_latest_value_job_locked(
                program_hash=program_hash,
                node_id=node_id,
                execution_strategy=execution_strategy,
            )
            if existing is not None and existing.status in {"queued", "running"}:
                return existing.as_public(include_result=True, include_log_tail=True, tail_lines=120)
            job = self._create_job_locked(request_payload)
            return job.as_public(include_result=False, include_log_tail=True, tail_lines=80)

    def get_value_job(
        self,
        *,
        program_hash: str,
        node_id: str,
        execution_strategy: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup_locked()
            job = self._find_latest_value_job_locked(
                program_hash=program_hash,
                node_id=node_id,
                execution_strategy=execution_strategy,
            )
            if job is None:
                return None
            self._poll_locked(job)
            return job.as_public(include_result=True, include_log_tail=True, tail_lines=120)

    def list_jobs(self) -> dict[str, Any]:
        with self._lock:
            self._cleanup_locked()
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
            return {
                "jobs": [job.as_public(include_result=False, include_log_tail=False) for job in jobs[:60]],
                "total_jobs": len(self._jobs),
                "generated_at": _iso_utc(time.time()),
            }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            self._poll_locked(job)
            return job.as_public(include_result=True, include_log_tail=True, tail_lines=180)

    def kill_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "running":
                self._kill_locked(job, reason="Killed by user request")
            return job.as_public(include_result=True, include_log_tail=True, tail_lines=180)


@dataclass
class TestingJob:
    job_id: str
    created_at: float
    profile: str
    include_perf: bool
    status: str = "queued"
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    return_code: int | None = None
    process: subprocess.Popen[str] | None = None
    log_handle: Any = None
    log_path: Path | None = None
    report_snapshot: dict[str, Any] | None = None

    def as_public(self, *, include_log_tail: bool = True, tail_lines: int = 80) -> dict[str, Any]:
        payload = {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": _iso_utc(self.created_at),
            "started_at": _iso_utc(self.started_at),
            "finished_at": _iso_utc(self.finished_at),
            "profile": self.profile,
            "include_perf": self.include_perf,
            "return_code": self.return_code,
            "error": self.error,
            "log_path": str(self.log_path) if self.log_path is not None else None,
            "report_snapshot": self.report_snapshot,
        }
        if include_log_tail:
            payload["log_tail"] = _read_log_tail(self.log_path, lines=tail_lines)
        return payload


def _read_log_tail(path: Path | None, *, lines: int = 80) -> str:
    if path is None or not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    split_lines = content.splitlines()
    return "\n".join(split_lines[-lines:])


def _build_test_command(profile: str, include_perf: bool) -> list[str]:
    profile_name = profile.strip().lower()
    cmd = ["bash", "tests/run-tests.sh"]
    if profile_name == "quick":
        cmd.extend(["-m", "unit or contract"])
        return cmd
    if profile_name in {"integration", "regression"}:
        cmd.extend(["-m", "integration or regression"])
        return cmd
    if profile_name in {"perf", "performance"}:
        cmd.extend(["-m", "perf"])
        return cmd
    if profile_name == "full":
        if include_perf:
            return cmd
        cmd.extend(["-m", "not perf"])
        return cmd
    raise ValueError(f"Unsupported test profile: {profile}")


class TestingJobManager:
    """Server-side test runner with job control and logfile tails."""

    def __init__(
        self,
        *,
        retain_seconds: float = 24 * 3600.0,
        max_jobs: int = 80,
    ):
        self._lock = threading.RLock()
        self._jobs: dict[str, TestingJob] = {}
        self._retain_seconds = retain_seconds
        self._max_jobs = max_jobs
        TEST_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)

    def _poll_locked(self, job: TestingJob) -> None:
        process = job.process
        if process is None:
            return
        code = process.poll()
        if code is None:
            return

        job.return_code = int(code)
        job.process = None
        if job.log_handle is not None:
            try:
                job.log_handle.close()
            except Exception:
                pass
            job.log_handle = None
        job.finished_at = time.time()
        if code == 0:
            job.status = "completed"
            job.report_snapshot = build_test_dashboard_snapshot()
            if job.log_path is not None:
                with job.log_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n[testing.run.summary] success=true\n")
                    handle.write(json.dumps(job.report_snapshot, sort_keys=True) + "\n")
        elif job.status != "killed":
            job.status = "failed"
            job.error = f"Test run exited with code {code}"
            job.report_snapshot = build_test_dashboard_snapshot()
            if job.log_path is not None:
                with job.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n[testing.run.summary] success=false return_code={code}\n")
                    handle.write(json.dumps(job.report_snapshot, sort_keys=True) + "\n")

    def _cleanup_locked(self) -> None:
        now = time.time()
        for job in list(self._jobs.values()):
            self._poll_locked(job)

        removable: list[str] = []
        for job_id, job in self._jobs.items():
            if job.status in {"completed", "failed", "killed"} and job.finished_at is not None:
                if now - job.finished_at > self._retain_seconds:
                    removable.append(job_id)

        if len(self._jobs) - len(removable) > self._max_jobs:
            ordered = sorted(
                (
                    (jid, job.finished_at or job.created_at)
                    for jid, job in self._jobs.items()
                    if job.status in {"completed", "failed", "killed"}
                ),
                key=lambda item: item[1],
            )
            for jid, _ in ordered:
                if len(self._jobs) - len(removable) <= self._max_jobs:
                    break
                if jid not in removable:
                    removable.append(jid)

        for jid in removable:
            self._jobs.pop(jid, None)

    def _kill_locked(self, job: TestingJob, reason: str) -> None:
        process = job.process
        if process is not None and process.poll() is None:
            try:
                if process.pid is not None:
                    os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                process.terminate()
            try:
                process.wait(timeout=4.0)
            except Exception:
                try:
                    if process.pid is not None:
                        os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    process.kill()
        job.process = None
        if job.log_handle is not None:
            try:
                job.log_handle.close()
            except Exception:
                pass
            job.log_handle = None
        job.status = "killed"
        job.error = reason
        job.finished_at = time.time()
        job.return_code = -9

    def create_job(self, *, profile: str = "full", include_perf: bool = True) -> dict[str, Any]:
        command = _build_test_command(profile, include_perf)
        created_at = time.time()
        job_id = uuid.uuid4().hex
        log_path = TEST_JOB_LOG_DIR / f"{job_id}.log"

        with self._lock:
            self._cleanup_locked()
            job = TestingJob(
                job_id=job_id,
                created_at=created_at,
                profile=profile,
                include_perf=include_perf,
                status="queued",
                log_path=log_path,
            )
            self._jobs[job_id] = job
            try:
                with log_path.open("w", encoding="utf-8") as log_file:
                    log_file.write(f"[testing.run.started] { _iso_utc(created_at) }\n")
                    log_file.write(f"$ {' '.join(command)}\n")
                    log_file.write(f"cwd={REPO_ROOT}\n")
                    log_file.write(f"profile={profile} include_perf={include_perf}\n")
                log_handle = log_path.open("a", encoding="utf-8")
                process = subprocess.Popen(
                    command,
                    cwd=str(REPO_ROOT),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            except Exception:
                self._jobs.pop(job_id, None)
                raise
            job.process = process
            job.log_handle = log_handle
            job.status = "running"
            job.started_at = time.time()
            return job.as_public(include_log_tail=True, tail_lines=40)

    def list_jobs(self) -> dict[str, Any]:
        with self._lock:
            self._cleanup_locked()
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return {
                "jobs": [job.as_public(include_log_tail=False) for job in jobs[:40]],
                "total_jobs": len(self._jobs),
                "generated_at": _iso_utc(time.time()),
            }

    def get_job(self, job_id: str, *, tail_lines: int = 120) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            self._poll_locked(job)
            return job.as_public(include_log_tail=True, tail_lines=tail_lines)

    def kill_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "running":
                self._kill_locked(job, reason="Killed by user request")
            return job.as_public(include_log_tail=True)
