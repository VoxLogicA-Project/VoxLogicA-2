export const OPERATIONS_HELP_ROWS = [
  {
    label: "Sent HTTP request",
    detail: "The UI asked the backend for a value or a collection page.",
  },
  {
    label: "Received HTTP reply",
    detail: "The backend answered. The reply may still describe a pending value.",
  },
  {
    label: "Live updates active",
    detail: "The UI subscribed to websocket updates and is waiting for backend change events.",
  },
  {
    label: "Fetching nested value",
    detail: "The UI is resolving one selected child path inside a collection.",
  },
  {
    label: "Waiting for nested value",
    detail: "The selected child exists, but the backend has not produced its concrete descriptor yet.",
  },
  {
    label: "Fetching page",
    detail: "The UI is asking for one visible page of collection items.",
  },
  {
    label: "Waiting for page items",
    detail: "The page exists, but some visible items are still unresolved.",
  },
  {
    label: "Session only",
    detail: "This operations log lives only in the browser session. Reloading the page clears it.",
  },
];
