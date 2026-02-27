from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from voxlogica.serve_support import inspect_store_result
from voxlogica.storage import MaterializationStore, ResultRecord, SQLiteResultsDatabase
from voxlogica.value_model import UnsupportedVoxValueError


@dataclass
class _UnsupportedPayload:
    text: str = "unsupported"


class _FakeStorage:
    def __init__(self, record: ResultRecord):
        self._record = record

    def get_record(self, node_id: str) -> ResultRecord | None:
        if node_id == self._record.node_id:
            return self._record
        return None


@pytest.mark.unit
def test_unsupported_runtime_value_is_not_persisted(tmp_path: Path) -> None:
    db = SQLiteResultsDatabase(db_path=tmp_path / "results.db")
    store = MaterializationStore(backend=db, read_through=False, write_through=True)
    try:
        store.put("node-unsupported", _UnsupportedPayload(), metadata={"source": "runtime"})
        meta = store.metadata("node-unsupported")
        warning = meta.get("persist_warning")
        assert meta.get("persisted") is False
        assert isinstance(warning, dict)
        assert warning.get("code") == "E_UNSPECIFIED_VALUE_TYPE"
        assert db.get_record("node-unsupported") is None
    finally:
        store.close()
        db.close()


@pytest.mark.unit
def test_inspect_unsupported_value_path_raises_spec_error() -> None:
    record = ResultRecord(
        node_id="node-seq",
        status="materialized",
        format_version="voxpod/1",
        vox_type="sequence",
        descriptor={
            "vox_type": "sequence",
            "format_version": "voxpod/1",
            "summary": {"length": 10},
            "navigation": {
                "path": "",
                "pageable": True,
                "can_descend": True,
                "default_page_size": 64,
                "max_page_size": 512,
            },
        },
        payload_json={"encoding": "sequence-pages-v1", "length": 10},
        value=None,
        metadata={},
        runtime_version="runtime",
    )
    storage = _FakeStorage(record)
    with pytest.raises(UnsupportedVoxValueError) as exc:
        inspect_store_result(storage, node_id="node-seq", path="/0")
    assert exc.value.code == "E_UNSPECIFIED_VALUE_TYPE"

