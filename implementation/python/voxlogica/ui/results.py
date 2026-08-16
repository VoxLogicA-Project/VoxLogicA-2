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

    name = type(value).__name__
    # Duck-typed rather than imported: this module must not drag SimpleITK or
    # numpy into the UI server's import graph to describe something.
    shape = getattr(value, "shape", None)
    size = getattr(value, "GetSize", None)
    if shape is not None:
        dtype = getattr(value, "dtype", "")
        return None, "array", f"{name} {tuple(shape)} {dtype}".strip()
    if callable(size):
        try:
            return None, "image", f"{name} {tuple(size())}"
        except Exception:  # a description must never be the thing that fails
            pass
    if isinstance(value, (list, tuple, dict, set)):
        return None, "collection", f"{name} of {len(value)}"
    return None, name, name


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
        if known is not None:
            return dict(known)

        if self._probe is not None:
            try:
                if self._probe(node_id):
                    return self._from_store(node_id)
            except Exception as exc:
                logger.debug("results store could not be asked about %s (%s)", node_id, exc)
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
        if value is not None:
            event["value"] = value
        if kind:
            event["type"] = kind
        if summary:
            event["summary"] = summary
        return event

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
                    event["type"] = kind
                if summary:
                    event["summary"] = summary
            if error is not None:
                event["error"] = error
            self._states[node_id] = event
            watched = node_id in self._watched
            self._changed.notify_all()
        if watched:
            self._publish(event)

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
