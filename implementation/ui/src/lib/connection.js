// One WebSocket to the hub: presence, heartbeat, and every server event.
//
// The heartbeat is not decoration. The server decides whether to keep the
// process alive after a run by asking "is anybody watching?", and an open TCP
// socket is a bad answer to that question -- a suspended laptop leaves one open
// for minutes. Pinging on the interval the server names is what makes "nobody
// is here" true when it says so.

import { app } from "./state.svelte.js";

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

  function handle(message) {
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
      case "run":
        Object.assign(app.run, message.run ?? {});
        app.record(message);
        break;
      default:
        app.record(message);
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
    };
    ws.onmessage = (event) => handle(JSON.parse(event.data));
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (socket !== ws) return; // superseded; its replacement owns the state
      stopPing();
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
