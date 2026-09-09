"""A live control channel for the measurement framework.

WHY THIS EXISTS

`measure.py` writes one report AT EXIT. That is the right authority for a
published number, and it is useless for the question that actually blocks
progress, which is always some variant of *what is this run doing right now,
and what happens if I change one thing*. Today's session is the case in point:
a sixty-case sweep had been running for twenty-six minutes, the throughput was
drifting, and answering "is the disk cache thrashing?" took nine ad-hoc `ssh`
probes -- `/proc/<pid>/io`, a per-thread `stat` walk, two `du` runs that raced
the evictor and filled the output with `No such file or directory`, and a
snapshot copy of a live SQLite file. Every one of those was an external
observer, which is exactly the method `measure.py` exists to replace, and none
of them could answer the follow-up: *turn persistence off and see*.

The alternative that must NOT be used is an environment variable. A knob read
from the environment is set before the process starts, so it cannot answer a
question raised by what the run is doing, it cannot be un-set, and it makes the
configuration invisible to the report. It is also against this project's rules.

SO: THE PROCESS SERVES ITS OWN INSPECTOR

Chrome's DevTools Protocol is the shape being copied, and it is copied because
its three decisions are the ones that matter here:

  * one duplex connection carrying request/response pairs keyed by id, so a
    client can have several questions outstanding;
  * `Domain.method` names, so the surface can grow without the client guessing;
  * *describe yourself* as a first-class method, so the client needs no version
    table -- ``Runtime.describe`` lists every knob and probe this build has.

What is deliberately NOT copied is events/subscriptions. A push channel makes
the instrument's cost depend on what a client asked for, and rule 3 of
`measure.py` (bounded, constant per-sample cost) is worth more than
convenience. Sampling stays on the measurement thread with its own period; the
control channel only reads what is already there.

THE COST RULES, WHICH ARE THE WHOLE DESIGN

  1. **The channel never runs on the event loop.** It is a daemon thread on a
     unix socket. The loop-versus-workers split is the decomposition that has
     found every real stall in this engine; an inspector that steals loop time
     would corrupt the one measurement that works.
  2. **Zero cost when nobody is connected.** A blocking `accept()` on an idle
     socket consumes nothing, so the channel is armed for the whole run and
     paid for only while a question is being asked.
  3. **A knob declares whether it is hot.** Changing `governor.rss_share`
     mid-run is meaningful; changing the number of worker coroutines is not,
     because they are created once. A knob that cannot take effect until the
     next run says so in `Knob.list` rather than lying by accepting the write.
  4. **Every set is recorded with its timestamp**, and the list of changes goes
     into the final report. A run whose parameters were altered while it ran is
     not comparable with one that was not, and the report must make that
     impossible to miss -- the same reason `set_outcome` exists.
  5. **`Runtime.eval` is off unless explicitly enabled** at launch. It is the
     honest answer to "can I read something you did not think to expose", and
     it is also arbitrary code in the measured process: it perturbs the run, it
     can deadlock it, and its use is therefore stamped into the report too.

The transport is newline-delimited JSON, one object per line, because it has to
be usable from a shell one-liner on a remote host at two in the morning:

    printf '{"id":1,"method":"Probe.get"}\\n' | socat - UNIX:<path>
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "voxlogica-control/1"

#: How long a client may hold a connection idle before it is dropped. A stuck
#: client must not pin a thread for the length of a fourteen-hour sweep.
_IDLE_TIMEOUT_S = 300.0

#: One request line is bounded so a malformed client cannot make the inspector
#: allocate without limit inside the measured process.
_MAX_LINE = 1 << 16


class _Knob:
    __slots__ = ("name", "get", "set", "doc", "hot", "kind")

    def __init__(self, name: str, get: Callable[[], Any],
                 set_: Callable[[Any], None] | None, doc: str, hot: bool,
                 kind: str) -> None:
        self.name, self.get, self.set = name, get, set_
        self.doc, self.hot, self.kind = doc, hot, kind


class _Probe:
    __slots__ = ("name", "get", "doc", "cost")

    def __init__(self, name: str, get: Callable[[], Any], doc: str,
                 cost: str) -> None:
        self.name, self.get, self.doc, self.cost = name, get, doc, cost


class _Registry:
    """Module-level, because the things worth tuning live in five modules.

    A registry passed down through constructors would mean threading a
    parameter through the governor, the persister, the store and the scheduler
    for the sake of observability -- and observability that changes the code it
    observes is how this project got its two retracted findings. Registration
    is a side effect at import or construction time and costs one dict write.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._knobs: dict[str, _Knob] = {}
        self._probes: dict[str, _Probe] = {}
        #: Every accepted write, in order: (unix_time, name, before, after).
        self.changes: list[dict[str, Any]] = []
        self.evals: list[dict[str, Any]] = []

    def knob(self, name: str, get: Callable[[], Any],
             set_: Callable[[Any], None] | None = None, *, doc: str = "",
             hot: bool = True, kind: str = "number") -> None:
        with self._lock:
            self._knobs[name] = _Knob(name, get, set_, doc, hot, kind)

    def probe(self, name: str, get: Callable[[], Any], *, doc: str = "",
              cost: str = "cheap") -> None:
        with self._lock:
            self._probes[name] = _Probe(name, get, doc, cost)

    def forget(self, name: str) -> None:
        """Drop a registration. Engine objects do not outlive their run."""
        with self._lock:
            self._knobs.pop(name, None)
            self._probes.pop(name, None)

    def knobs(self) -> list[_Knob]:
        with self._lock:
            return list(self._knobs.values())

    def probes(self) -> list[_Probe]:
        with self._lock:
            return list(self._probes.values())

    def find_knob(self, name: str) -> _Knob | None:
        with self._lock:
            return self._knobs.get(name)

    def find_probe(self, name: str) -> _Probe | None:
        with self._lock:
            return self._probes.get(name)

    def record_change(self, name: str, before: Any, after: Any) -> None:
        with self._lock:
            self.changes.append({"at": time.time(), "knob": name,
                                 "from": _plain(before), "to": _plain(after)})

    def record_eval(self, expr: str, ok: bool) -> None:
        with self._lock:
            self.evals.append({"at": time.time(), "expr": expr[:512], "ok": ok})

    def provenance(self) -> dict[str, Any]:
        """What the final report must carry so a run stays comparable."""
        with self._lock:
            return {"knob_changes": list(self.changes),
                    "evals": list(self.evals),
                    "altered_while_running": bool(self.changes or self.evals)}


REGISTRY = _Registry()


def register_knob(name: str, get: Callable[[], Any],
                  set_: Callable[[Any], None] | None = None, *, doc: str = "",
                  hot: bool = True, kind: str = "number") -> None:
    """Expose a tunable. `set_=None` makes it read-only.

    Call it from wherever the value lives. Registration is unconditional and
    costs one dict write: the channel is what is optional, not the registry, so
    a run without ``--control`` still knows what its knobs are and the final
    report can list them.
    """
    REGISTRY.knob(name, get, set_, doc=doc, hot=hot, kind=kind)


def register_probe(name: str, get: Callable[[], Any], *, doc: str = "",
                   cost: str = "cheap") -> None:
    """Expose a reading. ``cost`` is "cheap", "loop" or "expensive".

    "loop" means the getter touches scheduler state that the event loop also
    writes, so a client is told the reading may be torn rather than being given
    a lock that would make the inspector able to stall the run. "expensive"
    means O(threads) or a syscall per item, and is never included in the
    default ``Probe.get``.
    """
    REGISTRY.probe(name, get, doc=doc, cost=cost)


def _plain(value: Any) -> Any:
    """JSON-safe without pulling in a serializer. Unknown objects become text."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return repr(value)[:256]


class ControlChannel:
    """A unix socket serving `Domain.method` requests about this process.

    One thread accepts, and one thread per connection answers. Connections are
    capped: an inspector must not be able to grow the thread count of the
    process it is measuring, because the thread census is one of the readings.
    """

    MAX_CLIENTS = 4

    def __init__(self, path: str | Path, *, snapshot: Callable[[], dict[str, Any]],
                 measurement: Any = None, allow_eval: bool = False) -> None:
        self._path = Path(path)
        self._snapshot = snapshot
        self._measurement = measurement
        self._allow_eval = bool(allow_eval)
        self._eval_globals: dict[str, Any] = {}
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._clients = threading.Semaphore(self.MAX_CLIENTS)
        self._started_at = 0.0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Bind and accept. Never raises into the run: a run must not die
        because its inspector could not bind."""
        try:
            if self._path.exists():
                self._path.unlink()          # a socket left by a killed run
            self._path.parent.mkdir(parents=True, exist_ok=True)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(self._path))
            os.chmod(self._path, 0o600)      # same user only
            sock.listen(self.MAX_CLIENTS)
            sock.settimeout(0.5)             # so stop() is prompt
            self._sock = sock
            self._started_at = time.time()
            self._thread = threading.Thread(target=self._accept_loop,
                                            name="voxlogica-control", daemon=True)
            self._thread.start()
            print(f"[control] {PROTOCOL_VERSION} on {self._path}"
                  + ("  (eval enabled)" if self._allow_eval else ""),
                  file=sys.stderr, flush=True)
        except Exception as exc:                                # noqa: BLE001
            print(f"[control] not available: {exc}", file=sys.stderr, flush=True)
            self._sock = None

    def stop(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except Exception:                                   # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            self._path.unlink()
        except Exception:                                       # noqa: BLE001
            pass

    @property
    def address(self) -> str | None:
        return str(self._path) if self._sock is not None else None

    # ── serving ──────────────────────────────────────────────────────────────

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return                       # closed under us by stop()
            if not self._clients.acquire(blocking=False):
                try:
                    conn.sendall(b'{"error":{"code":-32000,'
                                 b'"message":"too many control clients"}}\n')
                    conn.close()
                except Exception:                               # noqa: BLE001
                    pass
                continue
            threading.Thread(target=self._serve, args=(conn,),
                             name="voxlogica-control-c", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(_IDLE_TIMEOUT_S)
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    return
                if not chunk:
                    return
                buf += chunk
                if len(buf) > _MAX_LINE:
                    conn.sendall(self._error(None, -32600, "request too long"))
                    return
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    conn.sendall(self._handle_line(line))
        except Exception:                                       # noqa: BLE001
            pass
        finally:
            self._clients.release()
            try:
                conn.close()
            except Exception:                                   # noqa: BLE001
                pass

    def _error(self, rid: Any, code: int, message: str) -> bytes:
        return (json.dumps({"id": rid, "error": {"code": code,
                                                 "message": message}}) + "\n").encode()

    def _handle_line(self, line: bytes) -> bytes:
        try:
            request = json.loads(line.decode("utf-8", "replace"))
        except Exception as exc:                                # noqa: BLE001
            return self._error(None, -32700, f"not JSON: {exc}")
        if not isinstance(request, dict):
            return self._error(None, -32600, "request must be an object")
        rid = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str):
            return self._error(rid, -32600, "missing method")
        if not isinstance(params, dict):
            return self._error(rid, -32602, "params must be an object")
        handler = self._METHODS.get(method)
        if handler is None:
            return self._error(rid, -32601,
                               f"no such method: {method} "
                               f"(try Runtime.describe)")
        started = time.perf_counter_ns()
        try:
            result = handler(self, params)
        except _Refused as exc:
            return self._error(rid, -32000, str(exc))
        except Exception as exc:                                # noqa: BLE001
            return self._error(rid, -32603, f"{type(exc).__name__}: {exc}")
        if isinstance(result, dict):
            # The client is told what the question cost, in the same reply.
            # Rule 4 of measure.py applied to this channel: an inspector that
            # does not report its own footprint is not evidence.
            result = dict(result)
            result["_served_us"] = (time.perf_counter_ns() - started) // 1000
        return (json.dumps({"id": rid, "result": _plain(result)}) + "\n").encode()

    # ── methods ──────────────────────────────────────────────────────────────

    def _m_describe(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "socket": str(self._path),
            "up_s": round(time.time() - self._started_at, 3),
            "eval_enabled": self._allow_eval,
            "methods": sorted(self._METHODS),
            "knobs": [{"name": k.name, "doc": k.doc, "hot": k.hot,
                       "kind": k.kind, "writable": k.set is not None}
                      for k in sorted(REGISTRY.knobs(), key=lambda k: k.name)],
            "probes": [{"name": p.name, "doc": p.doc, "cost": p.cost}
                       for p in sorted(REGISTRY.probes(), key=lambda p: p.name)],
            "changes": REGISTRY.provenance(),
        }

    def _m_probe_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """The engine snapshot plus any named probes.

        Without `names` this returns exactly what the periodic sampler stores,
        which is the point: a live reading and a report row are the same
        numbers, so one can never contradict the other. Expensive probes are
        included only when asked for by name.
        """
        wanted = params.get("names")
        out: dict[str, Any] = {}
        if wanted is None:
            try:
                out["snapshot"] = self._snapshot() or {}
            except Exception as exc:                            # noqa: BLE001
                out["snapshot_error"] = f"{type(exc).__name__}: {exc}"
            probes = [p for p in REGISTRY.probes() if p.cost != "expensive"]
        else:
            if not isinstance(wanted, list):
                raise _Refused("names must be a list")
            probes = []
            for name in wanted:
                probe = REGISTRY.find_probe(str(name))
                if probe is None:
                    raise _Refused(f"no such probe: {name}")
                probes.append(probe)
        readings: dict[str, Any] = {}
        for probe in probes:
            try:
                readings[probe.name] = probe.get()
            except Exception as exc:                            # noqa: BLE001
                readings[probe.name] = f"error: {type(exc).__name__}: {exc}"
        out["probes"] = readings
        out["at"] = time.time()
        return out

    def _m_knob_list(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = []
        for knob in sorted(REGISTRY.knobs(), key=lambda k: k.name):
            try:
                value: Any = knob.get()
            except Exception as exc:                            # noqa: BLE001
                value = f"error: {type(exc).__name__}: {exc}"
            rows.append({"name": knob.name, "value": value, "doc": knob.doc,
                         "hot": knob.hot, "kind": knob.kind,
                         "writable": knob.set is not None})
        return {"knobs": rows}

    def _m_knob_get(self, params: dict[str, Any]) -> dict[str, Any]:
        knob = self._require_knob(params)
        return {"name": knob.name, "value": knob.get()}

    def _m_knob_set(self, params: dict[str, Any]) -> dict[str, Any]:
        knob = self._require_knob(params)
        if knob.set is None:
            raise _Refused(f"{knob.name} is read-only")
        if "value" not in params:
            raise _Refused("missing value")
        before = None
        try:
            before = knob.get()
        except Exception:                                       # noqa: BLE001
            pass
        knob.set(params["value"])
        after = knob.get()
        REGISTRY.record_change(knob.name, before, after)
        return {"name": knob.name, "previous": before, "value": after,
                "hot": knob.hot,
                "note": ("in effect now" if knob.hot else
                         "recorded, but this build reads it only at startup")}

    def _require_knob(self, params: dict[str, Any]) -> _Knob:
        name = params.get("name")
        if not isinstance(name, str):
            raise _Refused("missing name")
        knob = REGISTRY.find_knob(name)
        if knob is None:
            raise _Refused(f"no such knob: {name} (try Knob.list)")
        return knob

    def _m_series_status(self, params: dict[str, Any]) -> dict[str, Any]:
        meter = self._measurement
        if meter is None:
            raise _Refused("this run has no --measure, so there is no series")
        return meter.series_status()

    def _m_series_start(self, params: dict[str, Any]) -> dict[str, Any]:
        meter = self._measurement
        if meter is None:
            raise _Refused("this run has no --measure, so there is no series")
        return meter.series_start(path=params.get("path"),
                                  period_s=params.get("period"))

    def _m_series_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        meter = self._measurement
        if meter is None:
            raise _Refused("this run has no --measure, so there is no series")
        return meter.series_stop()

    def _m_measure_write(self, params: dict[str, Any]) -> dict[str, Any]:
        """Write the report NOW, without stopping the run.

        The reason this method exists at all: today a report could only be
        obtained by ending the run that would have produced it.
        """
        meter = self._measurement
        if meter is None:
            raise _Refused("this run has no --measure")
        return meter.write_snapshot(params.get("path"))

    def _m_eval(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._allow_eval:
            raise _Refused("eval is disabled; relaunch with --control-eval. "
                           "It runs arbitrary code inside the measured process, "
                           "so it perturbs the run and is stamped into the report.")
        expr = params.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            raise _Refused("missing expr")
        if len(expr) > 4096:
            raise _Refused("expr too long")
        ok = False
        try:
            value = eval(expr, self._eval_globals)              # noqa: S307
            ok = True
            return {"value": _plain(value), "type": type(value).__name__}
        finally:
            REGISTRY.record_eval(expr, ok)

    def _m_eval_bind(self, params: dict[str, Any]) -> dict[str, Any]:
        """Give `Runtime.eval` a name to work from, without importing here.

        The channel deliberately holds no reference to the engine: an inspector
        that can reach the scheduler by default is one bad expression away from
        deadlocking the loop it is measuring. Whatever wires the channel decides
        what `eval` can see.
        """
        if not self._allow_eval:
            raise _Refused("eval is disabled; relaunch with --control-eval")
        return {"names": sorted(k for k in self._eval_globals
                                if not k.startswith("__"))}

    def bind_eval_name(self, name: str, value: Any) -> None:
        """Called by the wiring, not over the wire."""
        self._eval_globals[name] = value

    _METHODS: dict[str, Callable[["ControlChannel", dict[str, Any]], Any]] = {}


class _Refused(Exception):
    """A well-formed request this build will not serve. Reported, not raised."""


ControlChannel._METHODS = {
    "Runtime.describe": ControlChannel._m_describe,
    "Runtime.eval": ControlChannel._m_eval,
    "Runtime.evalNames": ControlChannel._m_eval_bind,
    "Probe.get": ControlChannel._m_probe_get,
    "Knob.list": ControlChannel._m_knob_list,
    "Knob.get": ControlChannel._m_knob_get,
    "Knob.set": ControlChannel._m_knob_set,
    "Series.status": ControlChannel._m_series_status,
    "Series.start": ControlChannel._m_series_start,
    "Series.stop": ControlChannel._m_series_stop,
    "Measure.write": ControlChannel._m_measure_write,
}
