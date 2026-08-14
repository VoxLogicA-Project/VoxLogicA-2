// One WebSocket to the hub: presence, heartbeat, every server event, the
// workspace replica, and the two directions of request that ride on it.
//
// The heartbeat is not decoration. The server decides whether to keep the
// process alive after a run by asking "is anybody watching?", and an open TCP
// socket is a bad answer to that question -- a suspended laptop leaves one open
// for minutes. Pinging on the interval the server names is what makes "nobody
// is here" true when it says so.
//
// Three kinds of traffic share this socket, which is deliberate: one connection
// is one answer to "is this client alive", and a second channel would be a
// second, disagreeing answer.
//   - events pushed by the server (runs, builds, the workspace)
//   - actions this tab asks for, answered by `actionResult`
//   - captures the server asks *this tab* for, answered by `captureResult`

import { app } from "./state.svelte.js";
import { workspace } from "./store/workspace.svelte.ts";
import { attach, settle } from "./actions/dispatch.svelte.ts";
import { capture } from "./capture.ts";

const RECONNECT_DELAY = 1000;

export function connect() {
  let socket = null;
  let pingTimer = null;

  function stopPing() {
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  }

  function startPing(intervalSeconds) {
    stopPing();
    pingTimer = setInterval(() => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "ping" }));
      }
    }, Math.max(1, intervalSeconds) * 1000);
  }

  async function handle(message) {
    switch (message.type) {
      case "hello":
        app.connection.clientId = message.clientId;
        startPing(message.pingInterval ?? 5);
        // The instance snapshot was fetched before this connection existed, so
        // its client count was stale the moment it arrived.
        refreshInstance();
        break;
      case "pong":
        break;
      case "ui-reload":
        // The bundle we are running has been superseded.
        location.reload();
        break;
      case "ui-build-error":
        app.buildError = { summary: message.summary, detail: message.detail };
        break;
      case "workspace":
        workspace.receive(message.workspace);
        break;
      case "actionResult":
        settle(message.id, message);
        break;
      case "capture":
        // The server has no screen. When an agent asks what the workspace looks
        // like, the answer can only come from a tab that is looking at it.
        await respond(message);
        break;
      case "run":
        Object.assign(app.run, message.run ?? {});
        app.record(message);
        break;
      default:
        app.record(message);
    }
  }

  async function respond(message) {
    const reply = { type: "captureResult", id: message.id };
    try {
      reply.png = await capture(message.target ?? null);
      reply.ok = true;
    } catch (error) {
      reply.ok = false;
      reply.error = String(error);
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(reply));
    }
  }

  function open() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    // Bound to a local, not to the shared `socket`: a trailing error event from
    // a connection we have already replaced would otherwise close its
    // successor, and the reconnect loop would never settle.
    const ws = new WebSocket(`${protocol}//${location.host}/ws`);
    socket = ws;
    ws.onopen = () => {
      app.connection.status = "connected";
      attach(ws);
    };
    ws.onmessage = (event) => handle(JSON.parse(event.data));
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (socket !== ws) return; // superseded; its replacement owns the state
      stopPing();
      attach(null);
      app.connection.status = "disconnected";
      app.connection.clientId = null;
      // The server may simply have exited (its run finished with nobody
      // watching). Retrying costs one failed connect per second and makes a
      // restarted server pick the tab back up on its own.
      setTimeout(open, RECONNECT_DELAY);
    };
  }

  open();
  refreshInstance();
}

export async function refreshInstance() {
  try {
    const response = await fetch("/api/instance", { cache: "no-store" });
    app.instance = await response.json();
  } catch {
    app.instance = null;
  }
}
