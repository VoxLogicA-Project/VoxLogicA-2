export const buildPlayTargets = ({ precomputedPrintTargets = [], latestGoalResults = [], precomputedSymbolTable = {}, latestSymbolTable = {} }) => {
  const targets = [];
  const seen = new Set();

  const add = (target) => {
    const key = `${target.kind}:${target.label}:${target.nodeId}`;
    if (seen.has(key)) return;
    seen.add(key);
    targets.push(target);
  };

  for (const goal of precomputedPrintTargets) {
    if (!goal?.node_id) continue;
    add({ kind: "print", label: String(goal.name || "print"), nodeId: String(goal.node_id) });
  }

  for (const goal of latestGoalResults) {
    if (!goal || goal.operation !== "print" || !goal.node_id) continue;
    add({ kind: "print", label: String(goal.name || "print"), nodeId: String(goal.node_id) });
  }

  const mergedSymbols = Object.keys(precomputedSymbolTable || {}).length > 0 ? precomputedSymbolTable : latestSymbolTable;
  for (const [name, nodeId] of Object.entries(mergedSymbols || {})) {
    if (!nodeId || typeof nodeId !== "string") continue;
    add({ kind: "variable", label: String(name), nodeId });
  }

  return targets;
};

export const normalizedExecutionErrors = (payload) => {
  const direct = payload && typeof payload === "object" ? payload.execution_errors : null;
  if (direct && typeof direct === "object") return direct;

  const nested = payload?.diagnostics?.execution_errors;
  if (nested && typeof nested === "object") return nested;

  return {};
};

export const normalizedExecutionErrorDetails = (payload) => {
  const direct = payload && typeof payload === "object" ? payload.execution_error_details : null;
  if (direct && typeof direct === "object") return direct;

  const nested = payload?.diagnostics?.execution_error_details;
  if (nested && typeof nested === "object") return nested;

  return {};
};

export const formatExecutionErrors = (errors) => {
  const entries = Object.entries(errors || {});
  if (!entries.length) return "none";
  return entries.map(([node, message]) => `${String(node).slice(0, 12)}: ${String(message)}`).join("\n");
};

export const formatExecutionErrorDetails = (details, executionErrors) => {
  const entries = Object.entries(details || {});
  if (!entries.length) return "";

  const summarize = (value, maxLen = 120) => {
    const text = String(value ?? "");
    if (text.length <= maxLen) return text;
    return `${text.slice(0, maxLen)}...`;
  };

  return entries
    .map(([node, detail]) => {
      const op = detail?.operator ? String(detail.operator) : "unknown";
      const args = Array.isArray(detail?.args) ? detail.args.map((value) => summarize(value)).join(", ") : "";
      const kwargs =
        detail && typeof detail.kwargs === "object" && detail.kwargs
          ? Object.entries(detail.kwargs)
              .map(([key, value]) => `${String(key)}=${summarize(value)}`)
              .join(", ")
          : "";
      const attrs = detail && typeof detail.attrs === "object" && detail.attrs ? summarize(JSON.stringify(detail.attrs)) : "";
      const kind = detail?.kind ? ` kind=${String(detail.kind)}` : "";
      const outputKind = detail?.output_kind ? ` output=${String(detail.output_kind)}` : "";
      const message = executionErrors?.[node] ? String(executionErrors[node]) : "";
      const signature = kwargs ? `${op}(${args}${args ? ", " : ""}${kwargs})` : `${op}(${args})`;
      const attrsPart = attrs && attrs !== "{}" ? ` attrs=${attrs}` : "";
      const errPart = message ? ` error=${message}` : "";
      return `${String(node).slice(0, 12)}: ${signature}${attrsPart}${kind}${outputKind}${errPart}`;
    })
    .join("\n");
};

export const buildFailureDetailsText = (payload, requestState) => {
  const nodeId = String(payload.node_id || requestState.nodeId || "");
  const path = payload.path || requestState.path || "";
  const computeStatus = String(payload.compute_status || payload.status || "failed");
  const lines = [`node: ${nodeId || "-"}`, `path: ${path || "/"}`, `status: ${computeStatus}`];

  const storeStatus = String(payload.store_status || payload?.diagnostics?.store_status || "missing");
  lines.push(`store: ${storeStatus}`);

  if (payload.error) lines.push(`error: ${String(payload.error)}`);
  if (payload.job_error) lines.push(`job_error: ${String(payload.job_error)}`);
  if (payload.store_error) lines.push(`store_error: ${String(payload.store_error)}`);

  const diagnostics = payload?.diagnostics;
  if (diagnostics && typeof diagnostics === "object") {
    if (diagnostics.job_error && !payload.job_error) lines.push(`job_error: ${String(diagnostics.job_error)}`);
    if (diagnostics.store_error && !payload.store_error) lines.push(`store_error: ${String(diagnostics.store_error)}`);
  }

  const executionErrors = normalizedExecutionErrors(payload);
  if (Object.keys(executionErrors).length) {
    lines.push("execution_errors:");
    lines.push(formatExecutionErrors(executionErrors));
  }

  const detailText = formatExecutionErrorDetails(normalizedExecutionErrorDetails(payload), executionErrors);
  if (detailText) {
    lines.push("failed_operations:");
    lines.push(detailText);
  }

  return lines.join("\n");
};

export const summarizeDescriptor = (descriptor) => {
  if (!descriptor || typeof descriptor !== "object") return "value available";
  const voxType = String(descriptor.vox_type || "value");
  const summary = descriptor.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};

  if (["integer", "number", "boolean"].includes(voxType)) return `${voxType}: ${summary.value}`;
  if (voxType === "string") return `string(${summary.length || 0}): ${summary?.value || ""}`;
  if (voxType === "ndarray") return `ndarray ${(summary.shape || []).join("x")} ${summary.dtype || ""}`.trim();
  if (["image2d", "volume3d"].includes(voxType)) return `${voxType} ${(summary.size || []).join("x")} ${summary.pixel_id || ""}`.trim();
  if (voxType === "sequence") return `sequence length=${summary.length || 0}`;
  if (voxType === "mapping") return `mapping length=${summary.length || 0}`;
  return voxType;
};
