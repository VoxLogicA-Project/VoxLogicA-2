from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import builtins

import pytest

from voxlogica import main as main_mod
from voxlogica.storage import SQLiteResultsDatabase, delete_results_store, results_store_paths


def _cli_args(**overrides) -> Namespace:
    """A COMPLETE namespace for `run_command`, with the defaults it reads.

    Hand-built namespaces go stale every time a flag is added: this file had two
    of them, and both were missing `for_expansion_cap`, so the tests failed on a
    parser detail after doing the thing they were about. One builder means one
    place to add the next flag.
    """
    args = dict(
        debug=False,
        delete_cache=False,
        store_db=None,
        save_syntax=None,
        save_task_graph=None,
        save_task_graph_as_dot=None,
        save_task_graph_as_json=None,
        execute=False,
        no_cache=False,
        engine=True,
        engine_debug=False,
        dynamic_expansion=True,
        for_expansion_cap=4096,
        sparse_cache=False,
        cache_max_gb=None,
        threads=None,
        threads_auto=False,
        profile=None,
        error_details=None,
        open_browser=False,
        stay=False,
        ui_port=None,
    )
    args.update(overrides)
    return Namespace(**args)



@pytest.mark.unit
def test_delete_results_store_removes_db_and_payload_files(tmp_path: Path) -> None:
    db_path = tmp_path / "results.db"
    store = SQLiteResultsDatabase(db_path=str(db_path))
    store.put_success("node-a", {"value": 1})
    store.close()

    db_file, payload_dir = results_store_paths(db_path)
    assert db_file.is_file()
    assert payload_dir.is_dir()

    deleted_db, deleted_payload = delete_results_store(db_path)
    assert deleted_db == db_file
    assert deleted_payload == payload_dir
    assert not db_file.exists()
    assert not payload_dir.exists()


@pytest.mark.unit
def test_delete_cache_prompt_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program = tmp_path / "demo.imgql"
    program.write_text('print "x" 1\n', encoding="utf-8")
    monkeypatch.setattr(builtins, "input", lambda _prompt: "n")

    args = _cli_args(filename=str(program), delete_cache=True, store_db=str(tmp_path / "results.db"), execute=False, no_cache=False)

    assert main_mod.run_command(args) == 0
    assert not (tmp_path / "results.db").exists()


@pytest.mark.unit
def test_delete_cache_prompt_confirmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program = tmp_path / "demo.imgql"
    program.write_text('print "x" 1\n', encoding="utf-8")
    db_path = tmp_path / "results.db"

    store = SQLiteResultsDatabase(db_path=str(db_path))
    store.put_success("node-a", 42)
    store.close()
    assert db_path.is_file()

    monkeypatch.setattr(builtins, "input", lambda _prompt: "y")

    args = _cli_args(filename=str(program), delete_cache=True, store_db=str(db_path), execute=False, no_cache=False)

    assert main_mod.run_command(args) == 0
    assert not db_path.exists()
    assert not results_store_paths(db_path)[1].exists()
