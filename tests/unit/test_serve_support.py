from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxlogica.serve_support import (
    build_storage_stats_snapshot,
    build_test_dashboard_snapshot,
    parse_playground_examples,
)
from voxlogica.storage import SQLiteResultsDatabase


@pytest.mark.unit
def test_parse_playground_examples_extracts_comment_directives():
    markdown = """
    <!-- vox:playground
    id: hello
    title: Hello Example
    module: default
    level: intro
    strategy: strict
    description: tiny sample
    -->
    ```imgql
    print "hello" 1 + 2
    ```
    """
    examples = parse_playground_examples(markdown)
    assert len(examples) == 1
    example = examples[0]
    assert example["id"] == "hello"
    assert example["title"] == "Hello Example"
    assert example["module"] == "default"
    assert example["strategy"] == "strict"
    assert 'print "hello" 1 + 2' in example["code"]


@pytest.mark.unit
def test_build_test_dashboard_snapshot_reads_junit_coverage_and_perf(tmp_path: Path):
    reports = tmp_path / "reports"
    perf = reports / "perf"
    perf.mkdir(parents=True)

    (reports / "junit.xml").write_text(
        """
        <testsuites>
          <testsuite tests="3" failures="1" errors="0" skipped="1" time="0.9">
            <testcase classname="a" name="ok" time="0.1" />
            <testcase classname="a" name="bad" time="0.2"><failure message="boom" /></testcase>
            <testcase classname="a" name="skip" time="0.0"><skipped /></testcase>
          </testsuite>
        </testsuites>
        """,
        encoding="utf-8",
    )
    (reports / "coverage.xml").write_text(
        """
        <coverage line-rate="0.8" branch-rate="0.5" lines-covered="8" lines-valid="10"
                  branches-covered="5" branches-valid="10">
          <packages>
            <package name="vox.a" line-rate="0.8" />
            <package name="vox.b" line-rate="0.6" />
          </packages>
        </coverage>
        """,
        encoding="utf-8",
    )
    (perf / "vox1_vs_vox2_perf.svg").write_text("<svg/>", encoding="utf-8")
    (perf / "vox1_vs_vox2_perf.json").write_text(
        json.dumps(
            {
                "vox1_median_s": 1.2,
                "vox2_median_s": 0.8,
                "speed_ratio": 1.5,
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_test_dashboard_snapshot(reports_dir=reports)
    assert snapshot["available"] is True
    assert snapshot["junit"]["summary"]["total"] == 3
    assert snapshot["junit"]["summary"]["failed"] == 1
    assert snapshot["coverage"]["summary"]["line_percent"] == 80.0
    assert snapshot["performance"]["available"] is True
    assert snapshot["performance"]["speed_ratio"] == 1.5


@pytest.mark.unit
def test_build_storage_stats_snapshot_summarizes_sqlite_backend(tmp_path: Path):
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    db.put_success("node-1", {"x": 1})
    db.put_failure("node-2", "boom")
    try:
        snapshot = build_storage_stats_snapshot(db)
    finally:
        db.close()

    assert snapshot["available"] is True
    assert snapshot["summary"]["total_records"] == 2
    assert snapshot["summary"]["materialized_records"] == 1
    assert snapshot["summary"]["failed_records"] == 1
