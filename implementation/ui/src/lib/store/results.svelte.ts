// What the engine knows about a node, and how to wait for it. (R6)
//
// A result card is not a snapshot somebody has to refresh. It names a node, and
// what it shows is whatever that node currently *is* -- unknown, queued,
// computing, done, failed -- changing under the reader as the engine works. That
// is the whole requirement, and it has two readers with genuinely different
// needs:
//
//   - a component, which wants to *read* and be re-rendered when the answer
//     changes: `results.get(hash)` inside a `$derived` or a template;
//   - an action, a test or an agent, which wants to *wait*:
//     `await results.wait(hash, { state: "done", timeout })`.
//
// Both are served from one event stream, so there is no polling anywhere and no
// second source of truth to disagree with the first.
//
// Keyed by hash, because a hash is what a result *is*: two cards naming the same
// node are one subscription and one answer. Names are an index on top of that
// (`hashFor`), because a card names a binding and only the server can say which
// node that binding compiled to.
//
// Subscription is driven by what is on the board: `subscribe`/`unsubscribe` are
// called by the cards that show a node, and the server pushes updates only for
// hashes somebody is looking at. Streaming every node of a large run to every
// open tab would make the cost of a computation a function of how many windows
// are open, which is the wrong thing for it to depend on.
//
// See doc/dev/ui-workspace.md section 4.

export type ResultState = "unknown" | "pending" | "computing" | "done" | "failed";

export interface Result {
  hash: string;
  state: ResultState;
  /** The value, when there is one and it is small enough to have been sent. */
  value?: unknown;
  /** What kind of thing `value` is, as the server names it. Chooses the viewer. */
  type?: string;
  /** A short description of a value too large to send -- shape, size, dtype. */
  summary?: string;
  error?: string;
  /** When this state was reached, epoch seconds. */
  at?: number;
}

const UNKNOWN: Result = { hash: "", state: "unknown" };

interface Waiter {
  hash: string;
  test: (result: Result) => boolean;
  resolve: (result: Result) => void;
  reject: (reason: Error) => void;
  timer: ReturnType<typeof setTimeout> | null;
}

export interface WaitOptions {
  /** Resolve when the node reaches this state. Defaults to `done`. */
  state?: ResultState;
  /** Or resolve on any state this returns true for. Wins over `state`. */
  until?: (result: Result) => boolean;
  /** Seconds. Rejects when it runs out. A wait with no bound is a hang with a
   * nicer name -- an agent that mistyped a node name would sit on it forever. */
  timeout?: number;
}

class ResultsStore {
  /** hash -> what is known about it. A plain object in `$state` is a deep
   * proxy, so a component reading `by[hash].state` re-renders when that one
   * field moves and not when some other node's does. */
  private by = $state<Record<string, Result>>({});

  /** binding name -> hash, as the server resolved it for the open document.
   * Rebuilt whenever the document is compiled: a name is a property of the
   * text, and the text changes. */
  private index = $state<Record<string, string>>({});

  /** hash -> how many cards are showing it. Reference counted because two cards
   * may name one node, and the second one closing must not silence the first. */
  private watchers = new Map<string, number>();

  private waiting: Waiter[] = [];

  /** Set by `connection.js`. Absent while disconnected, which is not an error:
   * the subscriptions are re-sent on reconnect. */
  private send: ((message: unknown) => void) | null = null;

  /** What is known about a node right now. Reactive: read it in a component. */
  get(hash: string | null | undefined): Result {
    if (!hash) return UNKNOWN;
    return this.by[hash] ?? { ...UNKNOWN, hash };
  }

  /** The node a binding compiled to, if the server has said. */
  hashFor(name: string | null | undefined): string | null {
    if (!name) return null;
    return this.index[name] ?? null;
  }

  /** What a card is showing: its binding resolved, then looked up. */
  forCard(card: { node?: string } | null | undefined): Result {
    return this.get(this.hashFor(card?.node));
  }

  /** Wait for a node to reach a state. One promise, off the same events. */
  wait(hash: string, options: WaitOptions = {}): Promise<Result> {
    const wanted = options.state ?? "done";
    const test = options.until ?? ((result: Result) => result.state === wanted);
    const current = this.get(hash);
    if (test(current)) return Promise.resolve(current);

    // Asking about a node is also how you come to be told about it: a `wait`
    // for something no card shows must still get its answer.
    this.subscribe(hash);
    return new Promise<Result>((resolve, reject) => {
      const waiter: Waiter = { hash, test, resolve, reject, timer: null };
      if (options.timeout) {
        waiter.timer = setTimeout(() => {
          this.drop(waiter);
          this.unsubscribe(hash);
          reject(new Error(`timed out waiting for ${hash} (${options.timeout}s)`));
        }, options.timeout * 1000);
      }
      this.waiting.push(waiter);
    });
  }

  subscribe(hash: string | null | undefined): void {
    if (!hash) return;
    const count = this.watchers.get(hash) ?? 0;
    this.watchers.set(hash, count + 1);
    // Only the first watcher asks. The server answers a fresh subscription with
    // the node's current state, so a card added mid-run is not blank until the
    // next thing happens to it.
    if (count === 0) this.tell("subscribe", [hash]);
  }

  unsubscribe(hash: string | null | undefined): void {
    if (!hash) return;
    const count = this.watchers.get(hash) ?? 0;
    if (count <= 1) {
      this.watchers.delete(hash);
      this.tell("unsubscribe", [hash]);
    } else {
      this.watchers.set(hash, count - 1);
    }
  }

  /** A server event. One node, one new state. */
  receive(message: { hash?: string } & Partial<Result>): void {
    const hash = message.hash;
    if (!hash) return;
    const result: Result = {
      hash,
      state: message.state ?? "unknown",
      value: message.value,
      type: message.type,
      summary: message.summary,
      error: message.error,
      at: message.at,
    };
    this.by[hash] = result;
    this.settle(result);
  }

  /** The document's bindings, as the server compiled them. */
  receiveIndex(index: Record<string, string> | null | undefined): void {
    this.index = index ?? {};
  }

  /** Re-state every subscription. Called when a socket opens: the server that
   * answers is not necessarily the one that was asked. */
  attach(send: ((message: unknown) => void) | null): void {
    this.send = send;
    const hashes = [...this.watchers.keys()];
    if (send && hashes.length) this.tell("subscribe", hashes);
  }

  private tell(type: "subscribe" | "unsubscribe", hashes: string[]): void {
    this.send?.({ type: `results.${type}`, hashes });
  }

  private settle(result: Result): void {
    if (!this.waiting.length) return;
    // Copied first: resolving a waiter can start another wait, and mutating the
    // list underneath the loop is how the second one gets skipped.
    for (const waiter of [...this.waiting]) {
      if (waiter.hash !== result.hash || !waiter.test(result)) continue;
      this.drop(waiter);
      this.unsubscribe(waiter.hash);
      waiter.resolve(result);
    }
  }

  private drop(waiter: Waiter): void {
    if (waiter.timer !== null) clearTimeout(waiter.timer);
    const at = this.waiting.indexOf(waiter);
    if (at >= 0) this.waiting.splice(at, 1);
  }
}

export const results = new ResultsStore();
