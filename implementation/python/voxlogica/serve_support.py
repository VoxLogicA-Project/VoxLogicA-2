"""Serve-layer helpers for playground jobs, docs gallery, and dashboard metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import multiprocessing as mp
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
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
            import json

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


def _ru_maxrss_bytes() -> int:
    import resource

    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return int(value)
    return int(value * 1024.0)


def _playground_worker(send_conn: Any, request_payload: dict[str, Any]) -> None:
    from voxlogica.features import handle_run

    started_at = time.time()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    rss_before = _ru_maxrss_bytes()
    import tracemalloc

    tracemalloc.start()
    packet: dict[str, Any] = {"ok": False, "error": "Unknown worker failure"}
    try:
        run_result = handle_run(**request_payload)
        if run_result.success:
            packet = {"ok": True, "result": run_result.data}
        else:
            packet = {"ok": False, "error": run_result.error or "Execution failed"}
    except Exception as exc:  # noqa: BLE001
        packet = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=15),
        }
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
        send_conn.send(packet)
        send_conn.close()


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
    traceback: str | None = None
    process: mp.Process | None = None
    recv_conn: Any = None

    def as_public(self, include_result: bool = True) -> dict[str, Any]:
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
            },
            "metrics": self.metrics,
            "error": self.error,
            "traceback": self.traceback,
        }
        if include_result:
            payload["result"] = self.result
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
            packet = recv_conn.recv()
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
                tb = packet.get("traceback")
                if isinstance(tb, str) and tb:
                    job.traceback = tb
            recv_conn.close()
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

    def create_job(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request_payload)
        payload["execute"] = bool(payload.get("execute", True))
        job_id = uuid.uuid4().hex
        created_at = time.time()
        recv_conn, send_conn = self._ctx.Pipe(duplex=False)
        process = self._ctx.Process(
            target=_playground_worker,
            args=(send_conn, payload),
            daemon=True,
        )

        with self._lock:
            self._cleanup_locked()
            job = PlaygroundJob(
                job_id=job_id,
                request_payload=payload,
                created_at=created_at,
                status="queued",
                process=process,
                recv_conn=recv_conn,
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
            return job.as_public(include_result=False)

    def list_jobs(self) -> dict[str, Any]:
        with self._lock:
            self._cleanup_locked()
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
            return {
                "jobs": [job.as_public(include_result=False) for job in jobs[:60]],
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
            return job.as_public(include_result=True)

    def kill_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "running":
                self._kill_locked(job, reason="Killed by user request")
            return job.as_public(include_result=True)


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
        elif job.status != "killed":
            job.status = "failed"
            job.error = f"Test run exited with code {code}"
            job.report_snapshot = build_test_dashboard_snapshot()

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
                    log_file.write(f"$ {' '.join(command)}\n")
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
