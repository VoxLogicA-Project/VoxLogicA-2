# The UI: architecture and its constraints

Audience: anyone changing `implementation/python/voxlogica/ui/` or
`implementation/ui/`.

The design rules for the front end are in
[ui-design-system.md](ui-design-system.md). This document covers the part below
them: how a browser ends up looking at a running computation, why it is built
this way, and which constraints are not negotiable.

---

## 1. What this is

Every `voxlogica run` **is** a UI server. There is no separate front-end dev
server, no `npm run dev`, no second process, and no build step a user has to
know about.

```
voxlogica run program.imgql
   │
   ├─ binds 127.0.0.1:10001 (first free port)      server.py
   ├─ starts uvicorn on a background thread        server.py  → app.py
   ├─ starts the source watcher (dev only)         watcher.py
   ├─ runs the computation on the main thread      engine/
   └─ on completion: exits when nobody is watching hub.py
```

`voxlogica serve` is the same machinery with no computation attached — a UI for
the store alone. Bare `voxlogica` is that again, in a window of its own (§4a).

### Why in-process

The alternative is a daemon that runs computations on behalf of a CLI. That buys
one thing (a UI that outlives a run) and costs: a lifecycle to manage, an IPC
protocol to version, two places for a computation's state to live, and a
debugging story where a crash is in the other process. In-process, the UI reads
the same objects the engine mutates, and a traceback is where the user is
looking.

The cost of in-process is that the UI's lifetime is the run's lifetime. §5 is
about paying that cost honestly.

---

## 2. Modules

| Module | Responsibility | Does *not* |
|---|---|---|
| `bundler.py` | source tree → served bytes, memoised on a fingerprint | know about HTTP, clients, or runs |
| `hub.py` | who is watching; fan-out of events | touch the filesystem or the network |
| `watcher.py` | notice a UI source edit, rebuild, push a reload | restart, reconfigure, or stop anything |
| `server.py` | bind a port, run uvicorn off the main thread | contain any routes |
| `app.py` | the ASGI app: bundle, `/api/*`, `/ws` | own a lifecycle |
| `window.py` | put a URL in an application window | know what is in it |
| `results.py` | node states, and waiting for one | run anything |

The split is by *what can go wrong*, not by size. Each module has one failure
mode: a build that does not compile, a client that went away without saying so, a
missed filesystem event, a port already taken, a bad request. A single
`ui.py` would have one function in which all five happen at once.

---

## 3. The bundle is a function, not an artefact

`bundler.py` treats the served bundle as a pure function of the source tree,
memoised on a fingerprint of that tree (a stat walk: path, size, mtime_ns of a
few dozen files). Consequences worth naming:

- **No startup build.** A page load is what triggers a build, and only if the
  fingerprint moved.
- **Content-addressed cache** in `~/.cache/voxlogica/ui-bundles/<fingerprint>/`,
  pruned to 12 entries. Undoing an edit, or hopping between branches, is a cache
  hit — measured at ~1 ms for a warm page load.
- **Two modes, auto-detected.** A source checkout (`implementation/ui/` next to
  the Python package) compiles on demand with esbuild; a wheel serves
  `voxlogica/ui/static/` and never needs node.
- **Publication is a rename.** A concurrent second `voxlogica run` shares the
  cache directory and must never observe a half-written bundle.
- **A build failure is memoised against its own fingerprint**, and only that
  one. Reverting a broken edit restores a fingerprint that already built, and
  must not be answered with the error the broken tree produced.

### The stat walk is a correctness net, not the mechanism

The watcher is what makes reload fast; the fingerprint on page load is what makes
it *correct*. Filesystem events get missed — atomic-rename editors, network
mounts, a watcher that started after the edit — and a watcher alone would then
serve stale bytes until someone restarted the server. Checking the fingerprint on
page load bounds the worst case at "one page load late".

---

## 4. Live reload without a restart

Editing a `.svelte` file rebuilds the bundle and reloads the browser **in
0.5–0.9 s, without restarting the Python process**. The run in flight keeps
running; the pid does not change.

That is the requirement, and it rules out the obvious implementation:
`uvicorn --reload` restarts the process, which would kill the computation the UI
exists to show. There is no `--reload` anywhere in this package. The watcher can
only invalidate and rebuild — it has no handle with which to restart anything.

### No watchdog, deliberately

`watchdog` is the obvious choice and is already a dependency. On macOS it imports
`_watchdog_fsevents`, a C extension that has not declared free-threaded
support — **importing it makes CPython re-enable the GIL for the whole
process**. Trading the engine's parallelism for a notification a fraction of a
second sooner is not a trade worth making, so the watcher polls the same stat
walk the bundler fingerprints with (a few dozen `stat()`s, four times a second).

Verified: `sys._is_gil_enabled()` stays `False` with fastapi, uvicorn and
websockets loaded and the UI serving.

This also collapses two mechanisms into one: what the watcher notices and what a
page load notices are *by construction* the same thing, so they cannot disagree.

### When the UI's own sources stop compiling

The page keeps running and keeps its WebSocket; the build error arrives as a
sticky event and renders as an in-page overlay (`BuildError.svelte`). A build
failure must never cost a live session. If there is no app yet — the first load
already fails — `app.py` serves a standalone page that polls `/api/build` and
reloads itself when a good build appears.

---

## 4a. The window

A workspace is an application, not a page: it owns the whole viewport, it has no
use for a URL bar, and a tab lost among thirty others is a workspace you will not
find again. So bare `voxlogica` opens a window, and the window it opens is the
**operating system's own web view** — WKWebView on macOS, WebView2 on Windows,
WebKitGTK on Linux — driven by `pywebview`.

Nothing is bundled. The Electron-shaped answer carries a rendering engine the
user already has, and a chromeless `chrome --app=URL` (which this was) is not an
application: no dock icon, no menu bar, and no window whose closing *means*
anything — its end had to be inferred from a heartbeat that stopped arriving.
Now `run_native` returns when the user closes the window, and that is the
application being over.

Two constraints, neither negotiable, both tested in
`tests/unit/test_ui_window.py`:

- **The window owns the main thread.** Cocoa and GTK insist. `run_native` blocks
  and is the last thing the process does, with the HTTP server already on a
  thread of its own. This is exactly why `voxlogica run` does *not* get a native
  window: its main thread is busy computing, which is the reason anyone opened
  the UI. A run still gets the browser ladder below.
- **It must not cost the engine its parallelism.** Importing a C extension that
  has not declared free-threaded support re-enables the GIL process-wide — the
  same rule that rules out `watchdog` in §4. PyObjC ≥ 12 declares it, and the
  test asserts it in a fresh interpreter, because this one has already imported
  SimpleITK and answered the question another way.

Underneath, unchanged: a Chromium-family `--app` window, then the default
browser. Both are worse windows rather than broken ones, which is what makes the
native path a preference and not a dependency. `VOXLOGICA_NO_NATIVE_WINDOW=1`
takes the ladder from the top.

---

## 5. Lifetime: the run exits when nobody is looking

At the end of a computation the process asks the hub whether anyone is watching,
and waits if so. Presence is a WebSocket **plus a heartbeat**: a connection alone
is not evidence, because a suspended laptop or a network drop without a FIN
leaves a socket that looks open for minutes.

The errors here are asymmetric, and the constants follow from that:

- a false *"nobody is here"* kills a UI somebody is using;
- a false *"somebody is here"* keeps a process alive a few extra seconds.

So `CLIENT_TTL` (120 s) is far above `PING_INTERVAL` (5 s). Browsers throttle
timers in background tabs — Chrome drops a hidden tab's `setInterval` to roughly
once a minute — so a UI left open in another tab keeps pinging, just slowly. A
TTL near the ping interval would read that as an empty room and let the run exit,
killing exactly the session the rule exists to preserve.

Related constraints in the same area:

- The listening socket is left **non-inheritable**. A subprocess that inherited
  it — an nnU-Net training that outlives the run by hours — would keep the port
  bound long after the parent exited.
- **A failed run does not wait for a browser.** On abort the CLI exits
  immediately, publishing a `failed` state, rather than rendering a traceback
  only after the last tab closes. A silent terminal that looks hung is worse
  than a lost UI.
- Ports are **bound, not guessed**: `bind_loopback` takes the first free port
  from 10001, so concurrent runs cannot race. Loopback only; nothing here is
  reachable from off the machine.
- Uvicorn runs with `loop="asyncio"`, `http="h11"`, `ws="websockets"` — the
  pure-Python path, because this is a free-threaded interpreter and those are the
  wheels that exist for it. The UI serves a handful of requests; the C
  accelerators would buy nothing measurable.

---

## 6. Browser side

- **Svelte 5 with runes.** `state.svelte.js` is a module-level `$state`
  singleton: a deep proxy, so the engine mutating `app.run.summary.nodes` is
  observed without a setter or an immutable-update dance. No store library.
- **Measure things that do not depend on what the measurement writes.** The
  board once read its cell pitch off its own grid tracks and divided the zoom
  back out — but the zoom is capped by what fits, and what fits is a question
  about the pitch. The effect read the state it wrote, Svelte stopped it with
  `effect_update_depth_exceeded`, and the board froze on load. It measures two
  hidden rulers now. Any `$effect` that both writes state and observes the DOM
  is worth this second look.
- **One WebSocket** (`connection.js`) carrying hello, heartbeat, engine events,
  result subscriptions and `ui-reload`. Handlers are bound to a local `const` socket, never to the
  shared variable, so a late event from a dead socket cannot close its successor.
- **Sticky events are replayed on connect**, so a browser opened mid-run is not
  staring at an empty screen. `ui-reload` is deliberately *not* sticky: replaying
  it to every new client would reload it, reconnect it, and replay again.
- **A slow client drops events** rather than backpressuring the engine that
  publishes them (bounded queue, `put_nowait`).

---

## 7. A trap that costs an afternoon

`app.py` deliberately has **no** `from __future__ import annotations`.

FastAPI resolves endpoint annotations at decoration time to decide what to
inject. Under postponed evaluation an unresolvable name silently degrades to "a
query parameter of that name" — and for a `WebSocket` parameter that means the
handshake is rejected with a mute 403 that nothing anywhere explains. Real
annotations plus module-level imports make that failure impossible.

Do not add the import back "for consistency".

---

## 8. Tests

`tests/unit/test_ui_server_lifecycle.py` covers the parts where a mistake is
invisible in a browser: port binding under contention, heartbeat expiry and
`wait_until_empty`, sticky replay, fingerprint sensitivity and cache reuse, the
memoised-failure guard, and the asset route's path containment.

`tests/unit/test_ui_design_system_discipline.py` covers the front-end rules
described in [ui-design-system.md](ui-design-system.md).

`tests/unit/test_ui_results.py` and `test_ui_results_transport.py` cover node
states: which of the two sources answers, which way a state may move, and that a
subscription is answered rather than merely recorded. The transport one exists
because the bug it caught — a state's `type` shadowing the message's — showed up
only as a card that never updated.

`tests/unit/test_ui_window.py` covers the window's two constraints (§4a).

Run them with the repo venv:

```bash
.venv/bin/python -m pytest tests/unit -k ui -q
```

---

## 9. Not built yet

**The hub across runs.** Today each `voxlogica run` serves its own UI on its own
port; several runs are several tabs. The intended shape is one UI in which each
run appears as a workspace. What already exists in its favour: results are shared
through the SQLite WAL store, so two runs looking at the same computation see the
same cache — the missing piece is presentation, not correctness.
