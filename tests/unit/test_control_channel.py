"""The control channel must answer, must not lie, and must not perturb.

Every test here is a property that was violated by the method it replaces --
reading a live run from the outside with `ssh` and `/proc`:

  * a reading obtained live must be the SAME reading the report carries, or the
    two contradict each other and neither is evidence;
  * a knob write must take effect on the next read, and must be REFUSED when
    out of range rather than wedging the run it is measuring;
  * a knob write must be stamped into the report, because a run whose
    parameters moved while it ran is not comparable with one whose did not;
  * `Runtime.eval` must be refused unless the run was launched allowing it;
  * the channel must cost nothing on the event loop, so it holds no reference
    to the loop and starts no thread until it is started.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from voxlogica.engine import control as control_mod
from voxlogica.engine.control import ControlChannel, REGISTRY, register_knob, register_probe


def _call(path, method, params=None, timeout=5.0):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall((json.dumps({"id": 7, "method": method,
                                  "params": params or {}}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            assert chunk, "channel closed without answering"
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0])


@pytest.fixture
def clean_registry():
    """The registry is module-level by design; a test must not inherit another's."""
    knobs, probes = dict(REGISTRY._knobs), dict(REGISTRY._probes)
    changes, evals = list(REGISTRY.changes), list(REGISTRY.evals)
    REGISTRY._knobs.clear()
    REGISTRY._probes.clear()
    REGISTRY.changes.clear()
    REGISTRY.evals.clear()
    yield
    REGISTRY._knobs.clear(); REGISTRY._knobs.update(knobs)
    REGISTRY._probes.clear(); REGISTRY._probes.update(probes)
    REGISTRY.changes[:] = changes
    REGISTRY.evals[:] = evals


@pytest.fixture
def channel(tmp_path, clean_registry):
    state = {"share": 0.45, "on": True}

    def set_share(value):
        share = float(value)
        if not 0.05 <= share <= 0.95:
            raise ValueError("out of range")
        state["share"] = share

    register_knob("governor.rss_share", lambda: state["share"], set_share,
                  doc="test knob")
    register_knob("persist.enabled", lambda: state["on"],
                  lambda v: state.__setitem__("on", bool(v)),
                  doc="test switch", kind="boolean")
    register_knob("engine.threads", lambda: 32, None, doc="read-only", hot=False)
    register_probe("queues.depth", lambda: {"ready": 11}, doc="test probe")
    register_probe("thread.census", lambda: {"total": 130}, doc="costly",
                   cost="expensive")

    ch = ControlChannel(tmp_path / "t.ctl",
                        snapshot=lambda: {"nodes_completed": 5, "recomputes": 0})
    ch.start()
    assert ch.address is not None, "the channel did not bind"
    try:
        yield ch, state
    finally:
        ch.stop()


@pytest.mark.unit
def test_describe_lists_every_knob_and_probe_with_its_doc(channel):
    """A client must need no version table: the build describes itself."""
    ch, _ = channel
    out = _call(ch.address, "Runtime.describe")["result"]
    names = {k["name"] for k in out["knobs"]}
    assert {"governor.rss_share", "persist.enabled", "engine.threads"} <= names
    by_name = {k["name"]: k for k in out["knobs"]}
    assert by_name["engine.threads"]["writable"] is False
    assert by_name["engine.threads"]["hot"] is False
    assert by_name["governor.rss_share"]["doc"]
    probes = {p["name"]: p for p in out["probes"]}
    assert probes["thread.census"]["cost"] == "expensive"
    assert "Knob.set" in out["methods"]


@pytest.mark.unit
def test_probe_get_returns_the_same_snapshot_the_report_records(channel):
    """The live reading and the report row must be the same numbers."""
    ch, _ = channel
    out = _call(ch.address, "Probe.get")["result"]
    assert out["snapshot"] == {"nodes_completed": 5, "recomputes": 0}
    # Cheap probes are included by default; expensive ones are not, because the
    # instrument's cost must not depend on who is watching.
    assert out["probes"]["queues.depth"] == {"ready": 11}
    assert "thread.census" not in out["probes"]
    named = _call(ch.address, "Probe.get", {"names": ["thread.census"]})["result"]
    assert named["probes"]["thread.census"] == {"total": 130}


@pytest.mark.unit
def test_a_write_takes_effect_and_is_stamped_into_the_provenance(channel):
    ch, state = channel
    out = _call(ch.address, "Knob.set",
                {"name": "governor.rss_share", "value": 0.6})["result"]
    assert out["previous"] == 0.45 and out["value"] == 0.6
    assert state["share"] == 0.6
    assert _call(ch.address, "Knob.get",
                 {"name": "governor.rss_share"})["result"]["value"] == 0.6
    prov = REGISTRY.provenance()
    assert prov["altered_while_running"] is True
    assert prov["knob_changes"][-1]["knob"] == "governor.rss_share"
    assert prov["knob_changes"][-1]["from"] == 0.45
    assert prov["knob_changes"][-1]["to"] == 0.6
    assert prov["knob_changes"][-1]["at"] > 0


@pytest.mark.unit
def test_an_untouched_run_is_not_flagged_as_altered(channel):
    """The flag must mean something, so reading must never set it."""
    ch, _ = channel
    _call(ch.address, "Probe.get")
    _call(ch.address, "Knob.list")
    _call(ch.address, "Knob.get", {"name": "persist.enabled"})
    assert REGISTRY.provenance()["altered_while_running"] is False


@pytest.mark.unit
def test_a_bad_write_is_refused_and_changes_nothing(channel):
    """A knob that can wedge the run it is measuring is not an instrument."""
    ch, state = channel
    for bad in (2.0, -1, "banana"):
        reply = _call(ch.address, "Knob.set",
                      {"name": "governor.rss_share", "value": bad})
        assert "error" in reply, f"{bad!r} was accepted"
    assert state["share"] == 0.45
    assert REGISTRY.provenance()["altered_while_running"] is False
    assert "error" in _call(ch.address, "Knob.set",
                            {"name": "engine.threads", "value": 8})
    assert "error" in _call(ch.address, "Knob.set", {"name": "nope", "value": 1})
    assert "error" in _call(ch.address, "Knob.get", {"name": "nope"})
    assert "error" in _call(ch.address, "Nope.method")


@pytest.mark.unit
def test_eval_is_refused_unless_the_run_allowed_it(tmp_path, clean_registry):
    off = ControlChannel(tmp_path / "off.ctl", snapshot=dict)
    off.start()
    try:
        reply = _call(off.address, "Runtime.eval", {"expr": "1+1"})
        assert "error" in reply
        assert "--control-eval" in reply["error"]["message"]
        assert REGISTRY.provenance()["evals"] == []
    finally:
        off.stop()

    on = ControlChannel(tmp_path / "on.ctl", snapshot=dict, allow_eval=True)
    on.bind_eval_name("answer", 42)
    on.start()
    try:
        out = _call(on.address, "Runtime.eval", {"expr": "answer * 2"})["result"]
        assert out["value"] == 84
        # A name the wiring did not bind is not reachable: the channel holds no
        # reference to the engine unless it was handed one.
        assert "error" in _call(on.address, "Runtime.eval", {"expr": "engine"})
        prov = REGISTRY.provenance()
        assert [e["ok"] for e in prov["evals"]] == [True, False]
        assert prov["altered_while_running"] is True
    finally:
        on.stop()


@pytest.mark.unit
def test_the_channel_reports_what_each_answer_cost(channel):
    """Rule 4 of measure.py applied to the inspector itself."""
    ch, _ = channel
    out = _call(ch.address, "Probe.get")["result"]
    assert isinstance(out["_served_us"], int) and out["_served_us"] >= 0


@pytest.mark.unit
def test_malformed_input_cannot_take_the_run_down(channel):
    ch, _ = channel
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5.0)
        sock.connect(ch.address)
        sock.sendall(b"not json at all\n")
        assert b"error" in sock.recv(65536)
        sock.sendall(b'{"id":2,"method":"Probe.get","params":3}\n')
        assert b"error" in sock.recv(65536)
        sock.sendall(b'[1,2,3]\n')
        assert b"error" in sock.recv(65536)
    # Still serving afterwards.
    assert "result" in _call(ch.address, "Runtime.describe")


@pytest.mark.unit
def test_a_probe_that_raises_is_reported_not_propagated(channel):
    """Observability must never break the run: a broken probe is a cell value."""
    ch, _ = channel

    def boom():
        raise RuntimeError("torn read")

    register_probe("broken", boom, doc="always raises")
    out = _call(ch.address, "Probe.get")["result"]
    assert "torn read" in out["probes"]["broken"]
    assert out["snapshot"] == {"nodes_completed": 5, "recomputes": 0}


@pytest.mark.unit
def test_stop_removes_the_socket_so_a_later_run_can_bind(tmp_path, clean_registry):
    path = tmp_path / "reuse.ctl"
    first = ControlChannel(path, snapshot=dict)
    first.start()
    assert Path(path).exists()
    first.stop()
    assert not Path(path).exists()
    second = ControlChannel(path, snapshot=dict)
    second.start()
    try:
        assert second.address is not None
        assert "result" in _call(second.address, "Runtime.describe")
    finally:
        second.stop()


@pytest.mark.unit
def test_the_channel_starts_no_thread_before_start(tmp_path, clean_registry):
    """Zero cost when off: constructing it must not add a thread or a socket."""
    before = threading.active_count()
    ch = ControlChannel(tmp_path / "idle.ctl", snapshot=dict)
    assert threading.active_count() == before
    assert ch.address is None
    assert not (tmp_path / "idle.ctl").exists()


@pytest.mark.unit
def test_concurrent_clients_are_bounded(tmp_path, clean_registry):
    """An inspector must not be able to grow the thread count it measures."""
    ch = ControlChannel(tmp_path / "many.ctl", snapshot=dict)
    ch.start()
    held = []
    try:
        for _ in range(ControlChannel.MAX_CLIENTS + 3):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            try:
                sock.connect(ch.address)
            except OSError:
                sock.close()
                continue
            held.append(sock)
        time.sleep(0.3)
        refused = 0
        for sock in held:
            sock.settimeout(0.5)
            try:
                if b"too many control clients" in sock.recv(4096):
                    refused += 1
            except socket.timeout:
                pass
        assert refused >= 1, "the client cap was never enforced"
    finally:
        for sock in held:
            sock.close()
        ch.stop()
