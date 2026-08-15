// The one road from the UI to a change in the workspace.
//
// Every action is a named message to the server, which owns the document; the
// new state arrives as a `workspace` event and lands in the replica. There is no
// second path: a component that wants to change something calls an action, and
// nothing in the UI assigns to the store. That is the rule the whole store
// design rests on, and it is what lets an MCP client do everything a person can
// do -- both are sending the same names to the same dispatcher.
//
// See doc/dev/ui-workspace.md sections 3 and 5.

export interface ActionOutcome<T = unknown> {
  ok: boolean;
  result?: T;
  error?: string;
}

type Pending = (outcome: ActionOutcome) => void;

const pending = new Map<string, Pending>();
let socket: WebSocket | null = null;
let nextId = 0;

/** Called by the connection when a socket opens or dies. */
export function attach(next: WebSocket | null): void {
  socket = next;
  if (next === null) {
    // A reply that can never arrive is worse than a rejection: an action
    // awaiting it would hang for as long as the tab is open.
    for (const settle of pending.values()) settle({ ok: false, error: "disconnected" });
    pending.clear();
  }
}

/** Called by the connection for every `actionResult` frame. */
export function settle(id: string, outcome: ActionOutcome): void {
  const waiting = pending.get(id);
  if (waiting === undefined) return;
  pending.delete(id);
  waiting(outcome);
}

export function invoke<T = unknown>(
  name: string,
  params: Record<string, unknown> = {},
): Promise<ActionOutcome<T>> {
  if (socket === null || socket.readyState !== WebSocket.OPEN) {
    return Promise.resolve({ ok: false, error: "disconnected" });
  }
  const id = `a${nextId++}`;
  return new Promise((resolve) => {
    pending.set(id, resolve as Pending);
    socket!.send(JSON.stringify({ type: "action", id, name, params }));
  });
}
