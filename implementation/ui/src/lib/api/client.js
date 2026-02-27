const toErrorMessage = (status, statusText, detail) => {
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    if (typeof detail.code === "string") return detail.code;
    return JSON.stringify(detail);
  }
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  return `${status} ${statusText}`;
};

export const apiRequest = async (path, init = {}) => {
  const response = await fetch(path, init);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(toErrorMessage(response.status, response.statusText, payload?.detail));
  }
  return payload;
};

export const getCapabilities = () => apiRequest("/api/v1/capabilities");
export const getVersion = () => apiRequest("/api/v1/version");
export const sendClientLogBatch = (events = []) =>
  apiRequest("/api/v1/log/client", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ events: Array.isArray(events) ? events : [] }),
  });

export const listProgramFiles = () => apiRequest("/api/v1/playground/files");
export const loadProgramFile = (relativePath) =>
  apiRequest(`/api/v1/playground/files/${encodePath(relativePath)}`);
export const getProgramSymbols = (program) =>
  apiRequest("/api/v1/playground/symbols", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ program }),
  });

export const createPlaygroundJob = (program) =>
  apiRequest("/api/v1/playground/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ program, execute: true, execution_strategy: "dask" }),
  });

export const listPlaygroundJobs = () => apiRequest("/api/v1/playground/jobs");
export const getPlaygroundJob = (jobId) => apiRequest(`/api/v1/playground/jobs/${encodeURIComponent(jobId)}`);
export const killPlaygroundJob = (jobId) =>
  apiRequest(`/api/v1/playground/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });

export const resolvePlaygroundValue = ({ program, nodeId = "", variable = "", path = "", enqueue = true }) => {
  const payload = { program, execution_strategy: "dask", variable, path, enqueue };
  if (!variable && nodeId) {
    payload.node_id = nodeId;
  }
  return apiRequest("/api/v1/playground/value", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
};

export const resolvePlaygroundValuePage = ({
  program,
  nodeId = "",
  variable = "",
  path = "",
  offset = 0,
  limit = 64,
  enqueue = true,
}) => {
  const payload = {
    program,
    execution_strategy: "dask",
    variable,
    path,
    offset: Math.max(0, Number(offset || 0)),
    limit: Math.max(1, Number(limit || 64)),
    enqueue,
  };
  if (!variable && nodeId) {
    payload.node_id = nodeId;
  }
  return apiRequest("/api/v1/playground/value/page", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
};

export const listStoreResults = ({ limit = 200, statusFilter = "", nodeFilter = "" } = {}) => {
  const params = new URLSearchParams();
  params.set("limit", `${limit}`);
  if (statusFilter) params.set("status_filter", statusFilter);
  if (nodeFilter) params.set("node_filter", nodeFilter);
  return apiRequest(`/api/v1/results/store?${params.toString()}`);
};

export const inspectStoreResult = (nodeId, path = "") => {
  const suffix = path ? `?path=${encodeURIComponent(path)}` : "";
  return apiRequest(`/api/v1/results/store/${encodeURIComponent(nodeId)}${suffix}`);
};

export const inspectStoreResultPage = ({ nodeId, path = "", offset = 0, limit = 64 }) => {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  params.set("offset", `${Math.max(0, Number(offset || 0))}`);
  params.set("limit", `${Math.max(1, Number(limit || 64))}`);
  return apiRequest(`/api/v1/results/store/${encodeURIComponent(nodeId)}/page?${params.toString()}`);
};

export const getGallery = () => apiRequest("/api/v1/docs/gallery");

export const getTestingReport = () => apiRequest("/api/v1/testing/report");
export const listTestingJobs = () => apiRequest("/api/v1/testing/jobs");
export const getTestingJob = (jobId) => apiRequest(`/api/v1/testing/jobs/${encodeURIComponent(jobId)}`);
export const startTestingJob = ({ profile, includePerf }) =>
  apiRequest("/api/v1/testing/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, include_perf: includePerf }),
  });
export const killTestingJob = (jobId) =>
  apiRequest(`/api/v1/testing/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });

export const getStorageStats = () => apiRequest("/api/v1/storage/stats");

const encodePath = (path) =>
  String(path || "")
    .split("/")
    .map((token) => encodeURIComponent(token))
    .join("/");
