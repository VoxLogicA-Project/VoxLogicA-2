#!/usr/bin/env python3
"""Client for a running engine's control channel (--control).

Deliberately dependency-free and importable from nothing: it must work over a
bare ssh into a host where the only certain thing is a python3.

    ctl.py <socket> describe
    ctl.py <socket> probe [name ...]
    ctl.py <socket> knobs
    ctl.py <socket> get <knob>
    ctl.py <socket> set <knob> <value>
    ctl.py <socket> report [path]        # write a report now, run keeps going
    ctl.py <socket> series on|off|status [path]
    ctl.py <socket> eval '<expr>'       # only with --control-eval
    ctl.py <socket> watch <field> [period_s]

`watch` is the one convenience beyond a raw call: it prints one line per period
with the named dotted field out of `Probe.get`, which is what turns a suspicion
about a decay into a curve. It is a POLL, not a subscription -- the engine does
not push, so the instrument's cost never depends on what a client asked for.
"""

from __future__ import annotations

import json
import socket
import sys
import time


def call(path: str, method: str, params: dict | None = None,
         timeout: float = 20.0) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall((json.dumps({"id": 1, "method": method,
                                  "params": params or {}}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                raise SystemExit("control channel closed without answering")
            buf += chunk
    reply = json.loads(buf.split(b"\n", 1)[0])
    if "error" in reply and reply["error"]:
        raise SystemExit(f"refused: {reply['error'].get('message')}")
    return reply.get("result") or {}


def dig(obj, dotted: str):
    for part in dotted.split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    path, verb, rest = argv[1], argv[2], argv[3:]
    if verb == "describe":
        out = call(path, "Runtime.describe")
        print(f"{out['protocol']}  pid={out['pid']}  up={out['up_s']}s  "
              f"eval={'on' if out['eval_enabled'] else 'off'}")
        print("\nknobs:")
        for knob in out["knobs"]:
            flag = "" if knob["writable"] else "  (read-only)"
            hot = "" if knob["hot"] else "  (not hot: startup only)"
            print(f"  {knob['name']}{flag}{hot}\n      {knob['doc']}")
        print("\nprobes:")
        for probe in out["probes"]:
            print(f"  {probe['name']}  [{probe['cost']}]\n      {probe['doc']}")
        changed = out.get("changes", {})
        if changed.get("altered_while_running"):
            print("\nALTERED WHILE RUNNING:")
            print(json.dumps(changed, indent=2))
        return 0
    if verb == "probe":
        params = {"names": rest} if rest else {}
        print(json.dumps(call(path, "Probe.get", params), indent=2))
        return 0
    if verb == "knobs":
        for knob in call(path, "Knob.list")["knobs"]:
            print(f"{knob['name']:32s} {knob['value']!r}")
        return 0
    if verb == "get":
        print(json.dumps(call(path, "Knob.get", {"name": rest[0]}), indent=2))
        return 0
    if verb == "set":
        raw = rest[1]
        try:
            value = json.loads(raw)          # numbers, true/false, null
        except Exception:                    # noqa: BLE001
            value = raw                      # a bare string
        print(json.dumps(call(path, "Knob.set",
                              {"name": rest[0], "value": value}), indent=2))
        return 0
    if verb == "report":
        params = {"path": rest[0]} if rest else {}
        print(json.dumps(call(path, "Measure.write", params), indent=2))
        return 0
    if verb == "series":
        what = rest[0] if rest else "status"
        if what == "on":
            params = {"path": rest[1]} if len(rest) > 1 else {}
            print(json.dumps(call(path, "Series.start", params), indent=2))
        elif what == "off":
            print(json.dumps(call(path, "Series.stop"), indent=2))
        else:
            print(json.dumps(call(path, "Series.status"), indent=2))
        return 0
    if verb == "eval":
        print(json.dumps(call(path, "Runtime.eval", {"expr": rest[0]}), indent=2))
        return 0
    if verb == "watch":
        field = rest[0]
        period = float(rest[1]) if len(rest) > 1 else 2.0
        while True:
            out = call(path, "Probe.get")
            value = dig(out, field)
            if value is None:
                value = dig(out.get("snapshot") or {}, field)
            if value is None:
                value = dig(out.get("probes") or {}, field)
            print(f"{time.strftime('%H:%M:%S')}  {field} = {value}", flush=True)
            time.sleep(period)
    print(f"unknown verb: {verb}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
