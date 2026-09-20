"""The on-disk form of a kept value: one file, self-describing, id-checked.

A kept file is a JSON header line, a newline, and the payload bytes. The header
carries the id of the expression the value came from, and a reader that finds a
different id refuses the file and lets the value be recomputed. That check is
the whole safety argument: without it, editing the program would silently load
yesterday's artefact, which is the worst failure this feature could have.

The encoding is the store's own (`pod_codec`), not a private one, so a kept file
holds exactly what a cache payload holds and gains every type the store can
already round-trip.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from voxlogica.pod_codec import decode_runtime_value, encode_for_storage

#: Bumped only if the layout below changes incompatibly.
FORMAT = 1


def write(path: Path, node_id: str, value: Any) -> None:
    """Write `value` to `path`, stamped with the id it was computed from.

    Atomic by temp-and-rename, for the same reason the store's payloads are: a
    process that dies partway through must not leave a complete-looking file
    pointing at half a value.
    """
    record = encode_for_storage(value)
    header = {
        "voxlogica_keep": FORMAT,
        "node": node_id,
        "vox_type": record.vox_type,
        "format_version": record.format_version,
        "payload_json": record.payload_json,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.part")
    try:
        with tmp.open("wb") as handle:
            handle.write(json.dumps(header).encode("utf-8"))
            handle.write(b"\n")
            if record.payload_bin is not None:
                handle.write(record.payload_bin)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read(path: Path, expect: str) -> tuple[bool, Any]:
    """`(True, value)` if `path` holds the value of `expect`; `(False, None)`.

    Returns a flag rather than `None`-as-absent because `None` is a value a
    program may legitimately keep.

    Every failure is the same answer -- missing, truncated, unreadable, written
    by a newer format, or belonging to a different expression -- because the
    caller's response to all of them is identical: compute it, and write the
    file afterwards. A kept file is an optimisation; the program's meaning does
    not depend on one existing.
    """
    try:
        with path.open("rb") as handle:
            line = handle.readline()
            if not line:
                return False, None
            header = json.loads(line.decode("utf-8"))
            if int(header.get("voxlogica_keep", 0)) != FORMAT:
                return False, None
            if str(header.get("node", "")) != expect:
                return False, None
            payload = handle.read()
        value = decode_runtime_value(str(header.get("vox_type", "")),
                                     dict(header.get("payload_json") or {}),
                                     payload or None)
        return True, value
    except Exception:                                           # noqa: BLE001
        return False, None
