import { writable } from "svelte/store";

const MAX_ENTRIES = 400;

const buildEntry = (entry = {}) => ({
  ts: entry.ts || new Date().toISOString(),
  type: String(entry.type || "activity"),
  variable: String(entry.variable || ""),
  path: String(entry.path || ""),
  status: String(entry.status || ""),
  materialization: String(entry.materialization || ""),
  detail: String(entry.detail || ""),
  source: String(entry.source || ""),
});

const store = writable([]);

export const computeActivity = {
  subscribe: store.subscribe,
};

export const pushComputeActivity = (entry = {}) => {
  const normalized = buildEntry(entry);
  store.update((items) => [normalized, ...items].slice(0, MAX_ENTRIES));
};

export const clearComputeActivity = () => {
  store.set([]);
};
