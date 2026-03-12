import { derived, writable } from "svelte/store";

const MAX_HISTORY_ENTRIES = 500;
const TERMINAL_PHASES = new Set(["finish", "error", "timeout", "cancel"]);
const TERMINAL_STATUSES = new Set(["completed", "computed", "failed", "killed", "timeout", "cancelled"]);
const TERMINAL_MATERIALIZATIONS = new Set(["computed", "cached", "failed"]);

const nowIso = () => new Date().toISOString();

const normalizeText = (value = "") => String(value || "").trim();

const normalizePhase = (value = "") => {
  const raw = normalizeText(value).toLowerCase();
  if (!raw) return "event";
  return raw;
};

const buildTargetLabel = (entry = {}) => {
  const variable = normalizeText(entry.variable);
  const path = normalizeText(entry.path || "/") || "/";
  if (variable) return `${variable} ${path}`.trim();
  if (path) return path;
  return "operation";
};

const defaultSummary = (entry = {}) => {
  const type = normalizeText(entry.type).toLowerCase();
  const target = buildTargetLabel(entry);
  if (!type) return target;
  if (type === "value.request") return `Sent HTTP request for value ${target}`;
  if (type === "value.response") return `Received HTTP reply for value ${target}`;
  if (type === "value.error") return `HTTP error while fetching value ${target}`;
  if (type === "value.timeout") return `HTTP timeout while fetching value ${target}`;
  if (type === "page.request") return `Sent HTTP request for page ${target}`;
  if (type === "page.response") return `Received HTTP reply for page ${target}`;
  if (type === "page.error") return `HTTP error while fetching page ${target}`;
  if (type === "page.timeout") return `HTTP timeout while fetching page ${target}`;
  if (type === "value.cache") return `Reused cached value ${target}`;
  if (type === "page.cache") return `Reused cached page ${target}`;
  return `${type} ${target}`.trim();
};

const inferFinal = (entry = {}) => {
  if (entry.final === true) return true;
  const phase = normalizePhase(entry.phase);
  const type = normalizeText(entry.type).toLowerCase();
  const status = normalizeText(entry.status).toLowerCase();
  const materialization = normalizeText(entry.materialization).toLowerCase();
  if (TERMINAL_PHASES.has(phase)) return true;
  if (type.endsWith(".error") || type.endsWith(".timeout")) return true;
  if (TERMINAL_STATUSES.has(status)) return true;
  if (TERMINAL_MATERIALIZATIONS.has(materialization)) return true;
  return false;
};

const buildEntry = (entry = {}) => ({
  id: normalizeText(entry.id) || `activity:${Math.random().toString(36).slice(2, 10)}`,
  ts: normalizeText(entry.ts) || nowIso(),
  type: normalizeText(entry.type || "activity"),
  phase: normalizePhase(entry.phase),
  operationKey: normalizeText(entry.operationKey),
  summary: normalizeText(entry.summary) || defaultSummary(entry),
  variable: normalizeText(entry.variable),
  path: normalizeText(entry.path),
  status: normalizeText(entry.status),
  materialization: normalizeText(entry.materialization),
  detail: normalizeText(entry.detail),
  source: normalizeText(entry.source),
  trackActive: Boolean(entry.trackActive),
  skipHistory: Boolean(entry.skipHistory),
  final: inferFinal(entry),
});

const sortActiveEntries = (items = []) =>
  [...items].sort((left, right) => {
    const leftTs = Date.parse(String(left?.updatedAt || left?.ts || "")) || 0;
    const rightTs = Date.parse(String(right?.updatedAt || right?.ts || "")) || 0;
    return rightTs - leftTs;
  });

const mergeActiveEntry = (previous = null, nextEntry) => ({
  ...(previous || {}),
  ...nextEntry,
  startedAt: previous?.startedAt || nextEntry.ts,
  updatedAt: nextEntry.ts,
  updates: Number(previous?.updates || 0) + 1,
});

const historyStore = writable([]);
const activeEntriesStore = writable({});

export const computeActivity = {
  subscribe: historyStore.subscribe,
};

export const ongoingComputeActivity = derived(activeEntriesStore, ($activeEntries) =>
  sortActiveEntries(Object.values($activeEntries || {})),
);

export const pushComputeActivity = (entry = {}) => {
  const normalized = buildEntry(entry);
  if (!normalized.skipHistory) {
    historyStore.update((items) => [normalized, ...items].slice(0, MAX_HISTORY_ENTRIES));
  }
  if (normalized.trackActive && normalized.operationKey) {
    activeEntriesStore.update((items) => {
      const next = { ...(items || {}) };
      if (normalized.final) {
        delete next[normalized.operationKey];
      } else {
        next[normalized.operationKey] = mergeActiveEntry(next[normalized.operationKey] || null, normalized);
      }
      return next;
    });
  }
  return normalized;
};

export const clearComputeActivity = () => {
  historyStore.set([]);
  activeEntriesStore.set({});
};
