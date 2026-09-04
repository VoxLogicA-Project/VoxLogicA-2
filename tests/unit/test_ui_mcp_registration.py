"""Registering with MCP clients touches the user's files, so it is tested hard.

The requirement is that launching VoxLogicA makes the workspace visible to the
agent tools already on the machine, without anybody editing JSON. The danger of
meeting it is a program that writes into config files it does not own, so every
test here is about restraint: only installed clients, only a missing entry, never
anything else in the file, and never a failure that reaches the user's run.
"""

import json

import pytest

from voxlogica.ui import registration


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(registration.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("VOXLOGICA_NO_MCP_REGISTER", raising=False)
    return tmp_path


def test_a_machine_with_no_mcp_clients_is_left_exactly_as_it_was(home):
    assert registration.register_clients() == []
    assert list(home.iterdir()) == []


def test_an_installed_client_gains_an_entry(home):
    (home / ".claude").mkdir()
    assert "claude-code" in registration.register_clients()
    config = json.loads((home / ".claude.json").read_text())
    entry = config["mcpServers"]["voxlogica"]
    # A command, not a URL: the port changes every run, the command does not.
    assert entry["args"][-1] == "mcp"


def test_registering_twice_changes_nothing_the_second_time(home):
    (home / ".claude").mkdir()
    registration.register_clients()
    before = (home / ".claude.json").read_text()
    assert registration.register_clients() == []
    assert (home / ".claude.json").read_text() == before


def test_an_existing_config_keeps_everything_it_had(home):
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text(
        json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "othertool"}}})
    )
    registration.register_clients()
    config = json.loads((home / ".claude.json").read_text())
    assert config["theme"] == "dark"
    assert config["mcpServers"]["other"] == {"command": "othertool"}
    assert "voxlogica" in config["mcpServers"]


def test_a_users_own_voxlogica_entry_is_never_overwritten(home):
    (home / ".claude").mkdir()
    mine = {"command": "/my/own/wrapper", "args": ["--special"]}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"voxlogica": mine}}))
    registration.register_clients()
    config = json.loads((home / ".claude.json").read_text())
    assert config["mcpServers"]["voxlogica"] == mine


def test_codex_gets_a_toml_table_and_only_one(home):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt-5"\n')
    assert "codex" in registration.register_clients()
    text = (home / ".codex" / "config.toml").read_text()
    assert text.startswith('model = "gpt-5"')
    assert text.count("[mcp_servers.voxlogica]") == 1
    assert registration.register_clients() == []
    assert (home / ".codex" / "config.toml").read_text().count("[mcp_servers.voxlogica]") == 1


def test_a_corrupt_config_is_left_alone_rather_than_repaired(home):
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text("{ not json at all")
    assert registration.register_clients() == []
    assert (home / ".claude.json").read_text() == "{ not json at all"


def test_registration_can_be_switched_off(home, monkeypatch):
    (home / ".claude").mkdir()
    monkeypatch.setenv("VOXLOGICA_NO_MCP_REGISTER", "1")
    assert registration.register_clients() == []
    assert not (home / ".claude.json").exists()


def test_an_instance_announces_itself_and_is_found(home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    record = registration.announce(port=10001, url="http://127.0.0.1:10001/", program="p.imgql")
    assert record is not None
    found = registration.instances()
    assert [instance["port"] for instance in found] == [10001]
    registration.withdraw(record)
    assert registration.instances() == []


def test_an_instance_that_died_without_tidying_up_is_pruned(home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    directory = registration.state_dir()
    directory.mkdir(parents=True)
    # A pid that cannot be running: the file is a leftover from a killed process.
    (directory / "999999.json").write_text(
        json.dumps({"pid": 999999, "port": 1, "url": "x", "startedAt": 0})
    )
    assert registration.instances() == []
    assert not (directory / "999999.json").exists()
