// The application store.
//
// Svelte 5 runes are the store: `$state` is a deep proxy (mutating
// `app.run.summary.nodes` is observed, no setter and no immutable update
// dance), and `$derived` is a lazy, memoised computed value. Putting them in
// class fields of a module-level singleton is the idiomatic way to share one
// reactive object across components -- there is no store library underneath
// this, and no subscribe/unsubscribe to get wrong.

class AppState {
  /** WebSocket liveness, plus the id the server knows us by. */
  connection = $state({ status: "connecting", clientId: null });

  /** What `/api/instance` said: pid, port, store db, program under run. */
  instance = $state(null);

  /** The computation this process is running, if any. */
  run = $state({ status: "idle", program: null, startedAt: null, finishedAt: null, summary: null });

  /** Everything the hub has pushed, newest last. */
  events = $state([]);

  /** Set while the UI sources do not compile; cleared by the next good build. */
  buildError = $state(null);

  running = $derived(this.run.status === "running");

  /** Deep reactivity in action: this recomputes when a nested field changes. */
  nodeCount = $derived(this.run.summary?.nodes ?? null);

  recentEvents = $derived(this.events.slice(-50).reverse());

  record(event) {
    this.events.push({ ...event, at: Date.now() });
    // The log is a view, not a ledger: an engine that pushes for hours must not
    // grow the tab's heap without bound.
    if (this.events.length > 2000) this.events.splice(0, this.events.length - 2000);
  }
}

export const app = new AppState();
