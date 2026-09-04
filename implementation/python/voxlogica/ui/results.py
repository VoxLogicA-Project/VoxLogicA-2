"""What the engine knows about a node, and how to wait for it. (R6)

A result card names a node and shows what that node currently *is*. This module
is the server half of that: it holds the states, it answers "what is the state
of this hash", and it pushes changes to the browsers that asked for them.

**Two sources, and one of them is not the engine.** A node whose value is already
in the results store is `done` before anything runs -- that is the entire point
of a content-addressed cache, and a cache hit that displayed as `unknown` until
somebody recomputed it would be lying about the most useful thing the system
does. So the store is asked first and the engine is asked second; the engine only
ever has more recent news, never contradictory news, because a hash is what its
value is.

**Subscription is by hash, not by client.** The hub fans out to everyone, so the
set of hashes anybody is watching is one set and an update goes to every open
window. What that buys is the thing worth buying: the volume of traffic is a
function of how many *nodes are being looked at*, not of how many nodes a run
has. A hundred-thousand-node plan with four cards on screen is four
subscriptions. What it does not buy is per-client filtering, and pretending
otherwise would be a protocol with a client id in it that nothing reads.

**Called from three threads.** The engine's event loop reports transitions, the
server's event loop serves subscriptions, and an MCP request may wait on one. So
the state is under a plain lock and everything published goes through the hub,
which is safe from any thread by construction.

See doc/dev/ui-workspace.md section 4.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

#: The five states a node can be in, in the order it passes through them.
UNKNOWN = "unknown"
PENDING = "pending"
COMPUTING = "computing"
DONE = "done"
FAILED = "failed"

#: How far a state may be moved *backwards* by a late report. Transitions arrive
#: from a scheduler that does not serialise its bookkeeping against this module,
#: so a `pending` for a node already `computing` is possible and means nothing.
#: Ranking them makes "the news is older than what we have" a comparison rather
#: than a special case per pair.
_RANK = {UNKNOWN: 0, PENDING: 1, COMPUTING: 2, DONE: 3, FAILED: 3}

#: Values sent whole. Anything else is described instead: a card showing a
#: 240x240x155 volume wants to know that it is one, not to download it.
_SENDABLE = (bool, int, float, str)

#: And only when short. A string result can be a whole program's output.
_MAX_VALUE_CHARS = 4096


def describe(value: Any) -> tuple[Any, str, str]:
    """`(value, type, summary)` for something a node produced.

    The value comes back only when it is small and simple enough that sending it
    is obviously right. Everything else is reduced to a sentence, which is what a
    card can show anyway.
    """
    if isinstance(value, bool):
        return value, "boolean", str(value)
    if isinstance(value, (int, float)):
        return value, "number", str(value)
    if isinstance(value, str):
        if len(value) <= _MAX_VALUE_CHARS:
            return value, "string", ""
        return None, "string", f"{len(value)} characters"

    if isinstance(value, (bytes, bytearray)):
        # What the engine hands over for a volume: the encoded file, with no
        # type beside it. Magic numbers are what file formats are *for*, so
        # this identifies rather than guesses -- and a viewer that can draw a
        # NIfTI needs to be told this is one before it will try.
        return None, _format_of(value), f"{len(value):,} bytes"

    name = type(value).__name__
    # Duck-typed rather than imported: this module must not drag SimpleITK or
    # numpy into the UI server's import graph to describe something.
    shape = getattr(value, "shape", None)
    size = getattr(value, "GetSize", None)
    if shape is not None:
        dtype = getattr(value, "dtype", "")
        # Three dimensions is a volume, whatever class it arrives in. The engine
        # hands images over as its own array type, and calling that "array" was
        # true and useless: it left every printed volume showing its shape as
        # text next to a viewer that could have drawn it.
        kind = "image" if len(tuple(shape)) == 3 else "array"
        return None, kind, f"{name} {tuple(shape)} {dtype}".strip()
    if callable(size):
        try:
            return None, "image", f"{name} {tuple(size())}"
        except Exception:  # a description must never be the thing that fails
            pass
    if isinstance(value, (list, tuple, dict, set)):
        return None, "collection", f"{name} of {len(value)}"
    return None, name, name


#: How much encoded value to hold on to at once. A handful of volumes: enough
#: that the cards on a board can all be drawn, small enough that a long session
#: does not accumulate every intermediate it ever saw.
_PAYLOAD_BUDGET = 256 * 1024 * 1024

#: Where NIfTI-1 keeps the four bytes that say it is one, and what they are.
#: An offset and a constant from the format itself -- this identifies rather
#: than guesses, which is the whole difference between reading a magic number
#: and hoping.
_NIFTI_AT, _NIFTI = 344, (b"n+1\x00", b"ni1\x00")
_PNG = b"\x89PNG\r\n\x1a\n"


def _format_of(payload: bytes) -> str:
    """What a byte stream is, said only when the bytes themselves say it.

    The engine hands a volume over as an encoded file with no type beside it,
    and a viewer that can draw a NIfTI has to be told that is what this is. A
    .nii.gz is gzip, so the header has to be uncompressed before it can be
    read -- and only the header: 400 bytes is enough to reach the magic, and
    decompressing a whole volume to name it would be absurd.
    """
    if payload.startswith(_PNG):
        return "image"
    header = payload
    if payload[:2] == b"\x1f\x8b":
        try:
            import zlib

            # `wbits` with 16 means "expect a gzip wrapper". The stream is
            # truncated on purpose, so an incomplete-data error is the normal
            # ending rather than a problem.
            header = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(payload, 400)
        except Exception:  # noqa: BLE001 - naming a value never fails
            return "bytes"
    if header[_NIFTI_AT : _NIFTI_AT + 4] in _NIFTI:
        return "image"
    return "bytes"


#: Bindings, memoised on the document text. Compiling is not free -- it loads
#: the standard library and reduces the whole program -- and the workspace
#: publishes on every keystroke that lands. Keyed by the text itself, so undoing
#: an edit is a hit and no invalidation rule has to be maintained.
_BINDINGS_CACHE: dict[str, dict[str, str]] = {}
_BINDINGS_CACHE_MAX = 16
_BINDINGS_LOCK = threading.Lock()


def bindings_for(text: str) -> dict[str, str]:
    """`let` name -> node hash, for a document as it currently reads.

    A document mid-edit does not parse, and that is the *normal* case rather
    than an error: somebody is halfway through typing a name. So a failure here
    is an empty map, which renders as cards that do not know their node yet --
    which is what they are.
    """
    with _BINDINGS_LOCK:
        cached = _BINDINGS_CACHE.get(text)
    if cached is not None:
        return dict(cached)

    try:
        from voxlogica.parser import parse_program_content
        from voxlogica.reducer import reduce_program_with_bindings

        _plan, bindings = reduce_program_with_bindings(parse_program_content(text))
        resolved = {name: str(node) for name, node in bindings.items()}
    except Exception as exc:
        logger.debug("could not compile the document for its bindings (%s)", exc)
        resolved = {}

    with _BINDINGS_LOCK:
        if len(_BINDINGS_CACHE) >= _BINDINGS_CACHE_MAX:
            _BINDINGS_CACHE.clear()  # a cache this small is cheaper to drop than to rank
        _BINDINGS_CACHE[text] = resolved
    return dict(resolved)


#: The name a probed sub-expression is bound to while it is being hashed.
#:
#: Improbable rather than illegal: a collision would silently hash the user's own
#: binding instead of their selection. It does *not* start with an underscore --
#: the grammar does not accept those, and the first version of this returned
#: `None` for everything because of it, which reads exactly like "that is not an
#: expression" and is therefore the kind of bug you can stare through.
_PROBE = "voxlogicaProbe0"


def hash_of(source: str, expression: str) -> str | None:
    """The node a sub-expression would compile to, in this document's context.

    Selecting three words in the editor and being told whether they are already
    computed is the whole point, and the hard part is the word *context*: the
    hash of `threshold(flair, 0.6)` depends on what `flair` means here. So the
    selection is not hashed on its own -- it is appended to the document as one
    more binding and the real reducer is asked what that came to.

    That is deliberately not "map the character range back to an AST node". The
    parser carries positions as `file:line:column` strings and not on every
    node, so that mapping would be a second, approximate understanding of the
    grammar; and this way the answer comes from the same reducer the engine
    uses, which is the only way a cache question can be answered *correctly*
    rather than plausibly.

    `None` when the selection is not an expression in its own right -- half of
    one, or a name that only exists inside a `fun` this appends after. That is
    the honest answer, and the common one while a pointer is moving.
    """
    text = (expression or "").strip()
    if not text or _PROBE in text:
        return None
    probed = f"{source}\nlet {_PROBE} = {text}\n"
    return bindings_for(probed).get(_PROBE)


class Results:
    """The node states of one UI instance."""

    def __init__(self, hub, *, probe: Callable[[str], bool] | None = None,
                 fetch: Callable[[str], Any] | None = None) -> None:
        self._hub = hub
        #: "is this in the results store" and "give me its value". Injected so
        #: this module can be tested without a database and so a `--no-cache`
        #: run, which has no store to ask, simply has no second source.
        self._probe = probe
        self._fetch = fetch
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        #: Encoded values seen going past, kept so a viewer can be handed the
        #: file itself. The description that reaches a card deliberately drops
        #: the bytes -- nobody wants a volume down a websocket -- and for a node
        #: the store did not persist, this was then the last copy. Bounded, and
        #: oldest-out: it is a convenience over the store, not a second store.
        self._payloads: OrderedDict[str, bytes] = OrderedDict()
        self._watched: set[str] = set()
        self._bindings: dict[str, str] = {}
        self._changed = threading.Condition(self._lock)

    # ------------------------------------------------------------- bindings

    def set_bindings(self, bindings: dict[str, str]) -> None:
        """The document's `let` names, and the nodes they compiled to.

        Replaced wholesale on every compile: a name is a property of the text,
        and merging an old map into a new one would leave a binding pointing at
        a node the current program does not contain.
        """
        with self._lock:
            self._bindings = dict(bindings)

    @property
    def bindings(self) -> dict[str, str]:
        with self._lock:
            return dict(self._bindings)

    def resolve(self, name: str | None) -> str | None:
        if not name:
            return None
        with self._lock:
            return self._bindings.get(name)

    # ---------------------------------------------------------- the states

    def state_of(self, node_id: str) -> dict[str, Any]:
        """What is known about a hash, asking the store if the engine has not
        spoken. Never raises: an unreadable cache is an `unknown`, not a 500."""
        with self._lock:
            known = self._states.get(node_id)
        # A `done` with nothing in it is the common shape and the misleading
        # one: a node already in the store is never dispatched, so the engine
        # reports it finished without ever holding its value. The card is then
        # told "done" and nothing else -- no type, so no viewer, so a volume
        # that exists on disk draws as a line of text. The store is asked in
        # exactly that case, which is also the case where it certainly has it.
        if known is not None and (known.get("state") != DONE or "valueType" in known):
            return dict(known)

        if self._probe is not None:
            try:
                if self._probe(node_id):
                    stored = self._from_store(node_id)
                    # The store knows the node is done; it does not always know
                    # what the value *is* -- a fetch can fail, or come back as
                    # something with no description. What went past the engine
                    # does know. Two answers about one node, and the one that
                    # says "this is a volume" is the one a card can draw.
                    if "valueType" not in stored:
                        with self._lock:
                            seen = self._states.get(node_id)
                        if seen and "valueType" in seen:
                            stored = {**stored, **{
                                key: seen[key] for key in ("value", "valueType", "summary")
                                if key in seen
                            }}
                    if known is not None:
                        # The engine's news is never overwritten -- only filled
                        # in. It is the authority on *when*; the store is the
                        # authority on *what*.
                        stored = {**stored, **known, **{
                            key: stored[key] for key in ("value", "valueType", "summary")
                            if key in stored
                        }}
                    return stored
            except Exception as exc:
                logger.debug("results store could not be asked about %s (%s)", node_id, exc)
        if known is not None:
            return dict(known)
        return {"hash": node_id, "state": UNKNOWN}

    def _from_store(self, node_id: str) -> dict[str, Any]:
        event = {"hash": node_id, "state": DONE, "at": time.time()}
        if self._fetch is None:
            return event
        try:
            value, kind, summary = describe(self._fetch(node_id))
        except Exception as exc:
            # The node is done -- that much the probe established. Only the
            # description failed, and a card that says "done" is still right.
            logger.debug("could not describe %s (%s)", node_id, exc)
            return event
        if not kind:
            return event
        if value is not None:
            event["value"] = value
        if kind:
            event["valueType"] = kind
        if summary:
            event["summary"] = summary
        return event

    def bytes_of(self, node_id: str) -> tuple[bytes, str] | None:
        """A node's value as a file, and what to call it.

        For viewers that draw the thing rather than describe it. Written to a
        temporary file by the same writer `save` uses, so what a viewer receives
        is byte-identical to what the program would have written -- one encoder,
        and no second format for a volume to be wrong in.
        """
        value = None
        if self._fetch is not None:
            try:
                value = self._fetch(node_id)
            except Exception as exc:
                logger.debug("could not fetch %s (%s)", node_id, exc)
        if value is None:
            # The store is asked first because it is where anything large lives,
            # but it does not hold everything the engine computes: trivial
            # arithmetic is folded and never persisted. What we saw go past is
            # then the only copy, and a viewer asking for it should not be told
            # "not computed" about a node that plainly is.
            with self._lock:
                value = self._payloads.get(node_id)
                if value is None:
                    known = self._states.get(node_id)
                    value = known.get("value") if known else None
        if value is None:
            return None

        if isinstance(value, (bytes, bytearray)):
            # Already encoded: hand it over untouched. Re-encoding what the
            # engine encoded would be a second opinion about a byte stream.
            name = "volume.nii.gz" if _format_of(value) == "image" else "value.bin"
            return bytes(value), f"{node_id[:16]}-{name}"
        # A three-dimensional array is written as a volume, through the same
        # writer a `save` uses -- so what a viewer draws and what the program
        # would have written to disk are the same bytes.
        shape = getattr(value, "shape", None)
        volumetric = hasattr(value, "GetSize") or (shape is not None and len(tuple(shape)) == 3)
        suffix = ".nii.gz" if volumetric else ".json"
        try:
            import tempfile
            from pathlib import Path as _Path

            with tempfile.TemporaryDirectory() as folder:
                target = _Path(folder) / f"{node_id[:16]}{suffix}"
                if suffix == ".nii.gz":
                    import SimpleITK as sitk

                    if not hasattr(value, "GetSize"):
                        import numpy as _np

                        # Booleans are regions; NIfTI has no bool, and uint8 is
                        # what every viewer expects a mask to arrive as.
                        array = _np.asarray(value)
                        if array.dtype == bool:
                            array = array.astype("uint8")
                        value = sitk.GetImageFromArray(array)

                    sitk.WriteImage(value, str(target))
                else:
                    import json

                    target.write_text(json.dumps(value, default=str))
                return target.read_bytes(), target.name
        except Exception as exc:
            logger.debug("could not serialise %s (%s)", node_id, exc)
            return None

    def _keep(self, node_id: str, payload: bytes) -> None:
        """Hold on to an encoded value, within the budget. Caller holds the lock."""
        if len(payload) > _PAYLOAD_BUDGET:
            return  # one value that would evict everything is not worth keeping
        self._payloads.pop(node_id, None)
        self._payloads[node_id] = payload
        held = sum(len(item) for item in self._payloads.values())
        while held > _PAYLOAD_BUDGET and len(self._payloads) > 1:
            _, dropped = self._payloads.popitem(last=False)
            held -= len(dropped)

    def observe(self, node_id: str, state: str, *, value: Any = None,
                error: str | None = None) -> None:
        """A transition, from the engine. Safe from any thread, and cheap: this
        sits in the scheduler's dispatch path, so an unwatched node costs a set
        lookup and nothing else."""
        with self._lock:
            previous = self._states.get(node_id)
            if previous is not None and _RANK[state] < _RANK[previous["state"]]:
                return  # older news than what we have
            event: dict[str, Any] = {"hash": node_id, "state": state, "at": time.time()}
            if state == DONE and value is not None:
                shown, kind, summary = describe(value)
                if shown is not None:
                    event["value"] = shown
                if kind:
                    event["valueType"] = kind
                if summary:
                    event["summary"] = summary
                if isinstance(value, (bytes, bytearray)):
                    self._keep(node_id, bytes(value))
            if error is not None:
                event["error"] = error
            self._states[node_id] = event
            watched = node_id in self._watched
            self._changed.notify_all()
        if watched:
            self._publish(event)

    def forget(self, nodes: Iterable[str]) -> list[dict[str, Any]]:
        """Drop what the engine said about these, and answer afresh.

        For the optimistic `pending` a demand writes. A node the scheduler never
        dispatched -- because it was already satisfied, or folded, or elided
        inside a fused cone -- produces no event, and the optimism would then sit
        on top of the store's own answer forever. A card stuck at `pending` looks
        exactly like a computation that is merely slow, which is the most
        expensive kind of wrong this module can be.

        Returns the states as they read now, so the caller can publish them.
        """
        wanted = [node for node in nodes if node]
        with self._lock:
            for node in wanted:
                self._states.pop(node, None)
            self._changed.notify_all()

        fresh = [self.state_of(node) for node in wanted]
        with self._lock:
            watched = {state["hash"] for state in fresh if state["hash"] in self._watched}
        for state in fresh:
            if state["hash"] in watched:
                self._publish(state)
        return fresh

    # ------------------------------------------------------ subscriptions

    def subscribe(self, hashes: Iterable[str]) -> list[dict[str, Any]]:
        """Watch these nodes; get their current states back at once.

        The reply is what stops a card added mid-run from being blank until the
        next thing happens to it -- and for a node that finished an hour ago in
        another process, the next thing never happens.
        """
        wanted = [h for h in hashes if h]
        with self._lock:
            self._watched.update(wanted)
        return [self.state_of(node_id) for node_id in wanted]

    def unsubscribe(self, hashes: Iterable[str]) -> None:
        with self._lock:
            self._watched.difference_update(h for h in hashes if h)

    def _publish(self, event: dict[str, Any]) -> None:
        # `valueType`, not `type`, is what a state calls the kind of thing it
        # holds -- because `type` is the envelope's, and a state spread into an
        # envelope that shares a key silently becomes a message of the wrong
        # kind. That is a bug you find by watching a card never update.
        self._hub.publish({"type": "result", **event})

    # -------------------------------------------------------------- waiting

    def wait(self, node_id: str, *, state: str = DONE, timeout: float = 60.0) -> dict[str, Any]:
        """Block until a node reaches a state, and return it.

        The server-side twin of `results.wait` in the browser, and the reason an
        agent can say "compute this and tell me when" in one call rather than
        polling. Bounded, because a wait with no bound is a hang with a friendlier
        name: a mistyped node name would otherwise never come back.
        """
        deadline = time.monotonic() + timeout
        wanted = _RANK[state]
        while True:
            current = self.state_of(node_id)
            if _RANK[current["state"]] >= wanted and current["state"] != UNKNOWN:
                return current
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"hash": node_id, "state": current["state"], "timedOut": True}
            with self._changed:
                # Woken by any transition, then re-checked: one condition for
                # every node is simpler than a condition per hash, and the number
                # of waiters is the number of agents, which is small.
                self._changed.wait(min(remaining, 0.25))

    # ----------------------------------------------------------- lifecycle

    def clear(self) -> None:
        """Forget everything the engine said. The store's answers survive,
        because they were never ours to forget."""
        with self._lock:
            self._states.clear()
            self._changed.notify_all()
