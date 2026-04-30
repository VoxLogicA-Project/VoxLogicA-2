<script>
  import { onDestroy, onMount, tick } from "svelte";
  import { getProgramSymbols, resolvePlaygroundValue, resolvePlaygroundValuePage } from "$lib/api/client.js";
  import { buildExecutionLogRows } from "$lib/utils/logs.js";
  import { buildFailureDetailsText, normalizedExecutionErrors } from "$lib/utils/playground-value.js";
  import VoxCodeEditor from "$lib/components/editor/VoxCodeEditor.svelte";
  import StartValueCanvas from "$lib/components/tabs/StartValueCanvas.svelte";
  import { dreamState, showDream, dissolveDream as storeDissolveDream, clearDream } from "$lib/stores/dreamStore.js";
  import { OPERATIONS_HELP_ROWS } from "$lib/constants/computeActivityHelp.js";
  import {
    clearComputeActivity,
    computeActivity,
    ongoingComputeActivity,
    pushComputeActivity,
  } from "$lib/stores/computeActivity.js";
  import {
    clearPersistedStartState,
    readPersistedStartState,
    updatePersistedStartState,
  } from "$lib/utils/ui-persistence.js";

  export let active = false;
  export let capabilities = {};

  const PRIMARY_VARIABLE_PREFERENCES = ["vi_sweep_overlays", "result", "output", "masks", "vi_sweep_masks"];
  const COMPLETION_BUILTINS = [
    "map",
    "for",
    "range",
    "subsequence",
    "dir",
    "ReadImage",
    "BinaryThreshold",
    "intensity",
    "percentiles",
    "smoothen",
    "grow",
    "touch",
    "not",
    "leq_sv",
    "geq_sv",
    "index",
    "overlay",
  ];

  const DEFAULT_PROGRAM = `import "simpleitk"

dataset_root = "tests/data/datasets/BraTS_2019_HGG"
k = 10
hi_thr = 0.93
vi_thr_start = 83
vi_thr_stop = 92
vi_ticks = range(vi_thr_start, vi_thr_stop)
to_thr(tick) = tick / 100
vi_thresholds = map(to_thr, vi_ticks)
all_flair_paths = dir(dataset_root, "*_flair.nii.gz", true, true)
flair_paths = subsequence(all_flair_paths, 0, k)
read_image(path) = ReadImage(path)
to_intensity(img) = intensity(img)
preprocess_flair(flair) =
  let background = touch(leq_sv(0.1, flair), border) in
  let brain = not(background) in
  percentiles(flair, brain, 0)
sweep_case(pflair) =
  let hyper_intense = smoothen(geq_sv(hi_thr, pflair), 5.0) in
  for vi_thr in vi_thresholds do
    let very_intense = smoothen(geq_sv(vi_thr, pflair), 2.0) in
    grow(hyper_intense, very_intense)
sweep_case_overlays(flair) =
  let pflair = preprocess_flair(to_intensity(flair)) in
  let hyper_intense = smoothen(geq_sv(hi_thr, pflair), 5.0) in
  for vi_thr in vi_thresholds do
    let very_intense = smoothen(geq_sv(vi_thr, pflair), 2.0) in
    overlay(flair, grow(hyper_intense, very_intense))
flair_images = map(read_image, flair_paths)
flair_intensities = map(to_intensity, flair_images)
pflair_images = map(preprocess_flair, flair_intensities)
vi_sweep_masks = map(sweep_case, pflair_images)
vi_sweep_overlays = map(sweep_case_overlays, flair_images)`;

  let programText = DEFAULT_PROGRAM;
  let symbolTable = {};
  let symbolDiagnostics = [];
  let primaryVariable = "";

  let viewer = null;

  let captionVariable = "-";
  let statusValue = "idle";
  let statusText = "Write code and run to compute a value.";
  let errorText = "";
  let staleValueVisible = false;
  let staleValueReason = "";
  let pendingLogSummary = "No execution log yet.";
  let pendingLogRows = [];
  let pendingLogRaw = "";
  let pendingLogJobId = "";
  let symbolStatuses = {};
  let symbolMaterializations = {};
  let materializedRecords = {};
  let symbolTypeHints = {};
  let editorSymbolTypes = {};
  let selectedVisualSymbols = [];
  let viewerSupportsMultiValue = false;
  let showCodePanel = true;
  let showResultsPanel = true;
  let showOperationsPanel = true;
  let showOperationsHelp = false;
  let viewerRecords = [];
  let viewerMode = "empty";
  let viewerMessage = "";
  let viewerErrorMessage = "";
  let recordPages = {};
  let recordPagePointers = {};
  let recordPageSources = {};
  let recordPagesLoading = {};
  let recordPagesErrors = {};
  let collectionSelections = {};
  let expandedCollectionStages = {};
  let recordPagePollTimers = {};
  let recordPageSockets = {};
  let recordPageSocketReconnectTimers = {};
  let recordPageSocketAttempts = {};
  let recordPageSubscriptions = {};
  let pathRecords = {};
  let pathRecordsLoading = {};
  let pathRecordsErrors = {};
  let pathRecordPollTimers = {};
  let resolutionActivityRows = [];
  let resolutionActivitySummary = "No resolution activity yet.";
  let activitySeenKeys = new Set();
  let uiInteractionSeq = 0;
  let lastUiInteraction = {
    intent: "primary-refresh",
    source: "startup",
    sequence: 0,
    atMs: 0,
    variable: "",
    path: "",
    direct: false,
    visible: true,
  };
  let pendingDreamCleanup = null;
  let currentPath = "";
  let pendingPoll = null;
  let pendingPollTicks = 0;
  let valueWs = null;
  let valueWsReconnectTimer = null;
  let valueWsAttempts = 0;
  let activeValueSubscription = null;
  let pendingSave = null;
  let pendingProbe = null;
  let pendingEditRefresh = null;
  let probeToken = 0;
  let resolveTraceSeq = 0;
  let resolveRequestSeq = 0;
  let editRefreshSeq = 0;
  let resolveInFlight = false;
  let maximizedViewerIndex = -1;
  let startPrimeGridEl = null;
  let startEditorRef = null;
  let editorViewState = null;
  let splitRatio = 0.48;
  let splitDragActive = false;
  let splitDragCleanup = null;
  let recentlyMaterialized = {};
  let recentMaterializeTimers = {};
  let persistenceReady = false;
  let hasPersistedViewerRestore = false;
  const MAX_PENDING_POLL_TICKS = 45;
  const COLLECTION_PAGE_SIZE = 18;
  const EDIT_REQUEST_DEBOUNCE_MS = 160;
  const EDIT_PENDING_RETRY_MS = 1000;
  const SPLIT_MIN = 0.32;
  const SPLIT_MAX = 0.68;
  const ACTIVE_COLLECTION_ITEM_STATES = new Set(["queued", "blocked", "running", "persisting"]);

  let loadToken = 0;
  let destroyed = false;

  const TYPE_LABELS = {
    scalar: "value",
    unknown: "value",
    integer: "number",
    number: "number",
    boolean: "boolean",
    string: "text",
    bytes: "bytes",
    ndarray: "array",
    image2d: "image",
    volume3d: "volume",
    overlay: "overlay",
    mapping: "object",
    sequence: "collection",
    closure: "function",
    effect: "effect",
    dataset: "dataset",
    tree: "tree",
    unavailable: "pending",
    error: "error",
  };

  const parseDiagnosticLocation = (diag) => {
    const location = String(diag?.location || "");
    const message = String(diag?.message || "");
    const raw = `${location} ${message}`;
    let match = raw.match(/line\s+(\d+)(?:\s*,\s*column\s+(\d+))?/i);
    if (match) {
      return {
        line: Number(match[1]),
        column: match[2] ? Number(match[2]) : null,
      };
    }
    match = raw.match(/(?:^|\s)(\d+):(\d+)(?:\s|$)/);
    if (match) {
      return {
        line: Number(match[1]),
        column: Number(match[2]),
      };
    }
    return null;
  };

  const diagnosticsRows = () => {
    const rows = Array.isArray(symbolDiagnostics) ? symbolDiagnostics : [];
    if (!rows.length) return [];
    return rows.map((diag) => {
      const message = String(diag?.message || "Static error").trim();
      const code = String(diag?.code || "").trim();
      const location = parseDiagnosticLocation(diag);
      const locationLabel = location?.line
        ? `Line ${location.line}${location?.column ? `:${location.column}` : ""}`
        : String(diag?.location || "").trim();
      const summary = locationLabel ? `${locationLabel} - ${message}` : message;
      return {
        message,
        code,
        location,
        locationLabel,
        summary,
      };
    });
  };

  const diagnosticsSummaryText = () => {
    const rows = diagnosticsRows();
    if (!rows.length) return "";
    const head = rows[0];
    const suffix = rows.length > 1 ? ` (+${rows.length - 1} more)` : "";
    return `${head.summary}${suffix}`;
  };

  const diagnosticsDetailsText = () => {
    const rows = diagnosticsRows();
    if (!rows.length) return "";
    return rows
      .map((diag, index) => {
        const prefix = `${index + 1}. `;
        const code = diag.code ? ` [${diag.code}]` : "";
        return `${prefix}${diag.summary}${code}`;
      })
      .join("\n");
  };

  const diagnosticsFromError = (error) => {
    const direct = Array.isArray(error?.diagnostics) ? error.diagnostics : null;
    if (direct?.length) return direct;
    const nested = Array.isArray(error?.detail?.diagnostics) ? error.detail.diagnostics : null;
    if (nested?.length) return nested;
    return [];
  };

  const applyStaticDiagnosticsState = (diagnostics, fallbackMessage = "Static diagnostics detected.") => {
    symbolDiagnostics = Array.isArray(diagnostics) ? diagnostics : [];
    const summary = diagnosticsSummaryText() || String(fallbackMessage || "Static diagnostics detected.");
    markStaleValue(summary);
    statusValue = "idle";
    statusText = staleValueVisible ? "Results are stale until editor diagnostics are fixed." : summary;
    errorText = "";
  };

  const statusRank = {
    idle: 0,
    queued: 1,
    running: 2,
    persisting: 2,
    computed: 3,
    failed: 4,
  };

  const normalizeStatus = (status) => {
    const normalized = String(status || "").trim().toLowerCase();
    if (normalized === "cached" || normalized === "completed") return "computed";
    if (normalized === "pending" || normalized === "missing") return "queued";
    if (normalized === "killed") return "failed";
    if (normalized in statusRank) return normalized;
    return "idle";
  };

  const normalizeMaterialization = (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) return "unresolved";
    if (normalized === "missing") return "pending";
    if (normalized === "materialized" || normalized === "completed") return "computed";
    if (normalized === "killed") return "failed";
    if (["cached", "computed", "pending", "queued", "running", "persisting", "failed", "unresolved"].includes(normalized)) {
      return normalized;
    }
    return "unresolved";
  };

  const isActiveComputeStatus = (status) =>
    ["queued", "running", "persisting"].includes(String(status || "").trim().toLowerCase());

  const isPendingLikePayload = (payload = null) => {
    const materialization = String(payload?.materialization || "").trim().toLowerCase();
    const computeStatus = String(payload?.compute_status || "").trim().toLowerCase();
    return ["pending", "missing"].includes(materialization) || isActiveComputeStatus(computeStatus);
  };

  const hasLiveProgressSignal = (payload = null) =>
    isActiveComputeStatus(payload?.compute_status) ||
    Boolean(payload?.request_enqueued) ||
    Boolean(payload?.job_id);

  const clearStaleValue = () => {
    staleValueVisible = false;
    staleValueReason = "";
  };

  const markStaleValue = (reason = "") => {
    const hasRenderedValue = viewerMode === "value" && Array.isArray(viewerRecords) && viewerRecords.length > 0;
    staleValueVisible = hasRenderedValue;
    staleValueReason = hasRenderedValue
      ? String(reason || "Showing the last computed value while you edit.")
      : "";
  };

  const normalizeCollectionItemState = (item) => {
    const rawState = String(item?.state || item?.status || "").trim().toLowerCase();
    if (["ready", "queued", "blocked", "running", "persisting", "failed", "not_loaded"].includes(rawState)) {
      return rawState;
    }
    if (["materialized", "computed", "completed", "cached"].includes(rawState)) return "ready";
    if (["pending", "missing"].includes(rawState)) return "not_loaded";
    if (["error", "killed"].includes(rawState)) return "failed";
    const itemType = String(item?.descriptor?.vox_type || "").trim().toLowerCase();
    if (!itemType || itemType === "unavailable") return "not_loaded";
    return "ready";
  };

  const hasConcreteDescriptor = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "").trim().toLowerCase();
    return Boolean(voxType) && voxType !== "unavailable" && voxType !== "error";
  };

  const collectionItemStateFromPayload = (payload = null) => {
    const materialization = String(payload?.materialization || "").trim().toLowerCase();
    const computeStatus = String(payload?.compute_status || "").trim().toLowerCase();
    if (materialization === "failed" || ["failed", "killed"].includes(computeStatus)) {
      return "failed";
    }
    if (["queued", "blocked", "running", "persisting"].includes(computeStatus)) {
      return computeStatus;
    }
    if (hasConcreteDescriptor(payload?.descriptor)) {
      return "ready";
    }
    if (["computed", "cached", "materialized", "completed"].includes(materialization)) {
      return "ready";
    }
    if (["pending", "missing"].includes(materialization)) {
      return "not_loaded";
    }
    return "not_loaded";
  };

  const collectionItemStatusFromState = (state = "not_loaded") => {
    const normalized = String(state || "not_loaded").trim().toLowerCase();
    if (normalized === "ready") return "materialized";
    if (normalized === "failed") return "failed";
    if (["queued", "blocked", "running", "persisting"].includes(normalized)) return normalized;
    return "pending";
  };

  const descriptorPriority = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "").trim().toLowerCase();
    if (!voxType || voxType === "unavailable" || voxType === "error") return 0;
    let score = 10;
    if (["sequence", "mapping"].includes(voxType)) score += 10;
    const summary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
    if (summary.length !== null && summary.length !== undefined && summary.length !== "") score += 6;
    if (Array.isArray(summary.size) && summary.size.length) score += 6;
    if (summary.value !== undefined) score += 4;
    const render = descriptor?.render && typeof descriptor.render === "object" ? descriptor.render : {};
    if (String(render?.kind || "").trim()) score += 8;
    return score;
  };

  const preferDescriptor = (left = null, right = null) =>
    descriptorPriority(left) >= descriptorPriority(right) ? left : right;

  const mergeCollectionItemRecords = (baseItem = null, preferredItem = null) => {
    if (!baseItem && !preferredItem) return null;
    if (!baseItem) return preferredItem;
    if (!preferredItem) return baseItem;
    const baseState = normalizeCollectionItemState(baseItem);
    const preferredState = normalizeCollectionItemState(preferredItem);
    const failedBase = baseState === "failed";
    const failedPreferred = preferredState === "failed";
    let nextState = preferredState;
    let nextStatus = collectionItemStatusFromState(preferredState);
    let nextError = String(preferredItem?.error || baseItem?.error || "");
    let nextBlockedOn = String(preferredItem?.blocked_on || baseItem?.blocked_on || "");
    let nextStateReason = String(preferredItem?.state_reason || baseItem?.state_reason || "");

    if (failedBase && !failedPreferred) {
      nextState = baseState;
      nextStatus = collectionItemStatusFromState(baseState);
      nextError = String(baseItem?.error || nextError || "");
    }
    if (
      hasConcreteDescriptor(baseItem?.descriptor) &&
      !hasConcreteDescriptor(preferredItem?.descriptor) &&
      !failedPreferred &&
      !["queued", "blocked", "running", "persisting"].includes(preferredState)
    ) {
      nextState = baseState;
      nextStatus = collectionItemStatusFromState(baseState);
      nextError = String(baseItem?.error || nextError || "");
      nextBlockedOn = String(baseItem?.blocked_on || nextBlockedOn || "");
      nextStateReason = String(baseItem?.state_reason || nextStateReason || "");
    }

    return {
      ...baseItem,
      ...preferredItem,
      descriptor: preferDescriptor(baseItem?.descriptor, preferredItem?.descriptor) || preferredItem?.descriptor || baseItem?.descriptor,
      state: nextState,
      status: nextStatus,
      error: nextError,
      blocked_on: nextBlockedOn,
      state_reason: nextStateReason,
    };
  };

  const pageSocketsEnabled = () =>
    typeof window !== "undefined" &&
    typeof window.WebSocket === "function" &&
    String(import.meta?.env?.MODE || "").trim().toLowerCase() !== "test";

  const typeLabelFromDescriptor = (descriptor) => {
    const rawType = String(descriptor?.vox_type || "").trim().toLowerCase();
    if (!rawType) return "value";
    return TYPE_LABELS[rawType] || rawType.replaceAll("_", " ");
  };

  const symbolTypeLabel = (name) => {
    const key = String(name || "");
    if (!key) return "value";
    const hinted = String(symbolTypeHints?.[key] || "").trim().toLowerCase();
    if (hinted) return TYPE_LABELS[hinted] || hinted.replaceAll("_", " ");
    const record = materializedRecords?.[key];
    return typeLabelFromDescriptor(record?.descriptor);
  };

  const symbolTypeTitle = (name) => `${String(name || "")} (${symbolTypeLabel(name)})`;

  const statusLabel = (name) => {
    const key = String(name || "");
    if (!key) return "idle";
    if (materializedRecords?.[key]?.descriptor) return "computed";
    const status = normalizeStatus(symbolStatuses?.[key] || "idle");
    const materialization = normalizeMaterialization(symbolMaterializations?.[key] || "unresolved");
    if (materialization === "computed") return "computed";
    if (materialization === "failed") return "failed";
    if (["queued", "running", "persisting", "failed", "computed"].includes(status)) return status;
    if (["queued", "running", "persisting"].includes(materialization)) return materialization;
    return "idle";
  };

  const shortNodeId = (nodeId) => {
    const text = String(nodeId || "");
    if (!text) return "";
    if (text.length <= 20) return text;
    return `${text.slice(0, 10)}…${text.slice(-6)}`;
  };

  const activityPathLabel = (path = "") => {
    const text = String(path || "").trim();
    return text || "/";
  };

  const clockMs = () =>
    typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now();

  const interactionPathDepth = (path = "") =>
    String(path || "")
      .split("/")
      .map((token) => token.trim())
      .filter(Boolean).length;

  const noteUiInteraction = ({ intent = "primary-refresh", source = "ui", variable = "", path = "", direct = false, visible = true } = {}) => {
    uiInteractionSeq += 1;
    lastUiInteraction = {
      intent: String(intent || "primary-refresh"),
      source: String(source || "ui"),
      sequence: uiInteractionSeq,
      atMs: clockMs(),
      variable: String(variable || primaryVariable || ""),
      path: String(path || currentPath || ""),
      direct: Boolean(direct),
      visible: Boolean(visible),
    };
    return lastUiInteraction;
  };

  const buildInteractionContext = ({ intent = "", source = "", variable = "", path = "", direct = null, visible = true, selected = null } = {}) => {
    const baseline = lastUiInteraction?.sequence ? lastUiInteraction : null;
    const targetVariable = String(variable || baseline?.variable || primaryVariable || "").trim();
    const targetPath = String(path || baseline?.path || currentPath || "");
    const ageMs = baseline?.atMs ? Math.max(0, Math.round(clockMs() - baseline.atMs)) : 0;
    return {
      intent: String(intent || baseline?.intent || "primary-refresh"),
      source: String(source || baseline?.source || "ui"),
      sequence: Number(baseline?.sequence || 0),
      age_ms: ageMs,
      selected: selected ?? Boolean(targetVariable && targetVariable === String(primaryVariable || "")),
      visible: Boolean(visible),
      direct: direct ?? Boolean(baseline?.direct),
      path_depth: interactionPathDepth(targetPath),
    };
  };

  const activityTargetLabel = ({ variable = "", path = "" } = {}) => {
    const name = String(variable || "").trim();
    const resolvedPath = activityPathLabel(path);
    return name ? `${name} ${resolvedPath}`.trim() : resolvedPath;
  };

  const resolveOperationKey = ({ traceId = 0, variable = "", path = "" } = {}) =>
    traceId ? `resolve:${traceId}:${String(variable || "").trim()}:${activityPathLabel(path)}` : "";

  const pollOperationKey = ({ variable = "", path = "" } = {}) =>
    `poll:${String(variable || "").trim()}:${activityPathLabel(path)}`;

  const pathLoadOperationKey = ({ variable = "", path = "" } = {}) =>
    `path:${String(variable || "").trim()}:${activityPathLabel(path)}`;

  const pageLoadOperationKey = ({ variable = "", path = "", offset = 0, limit = COLLECTION_PAGE_SIZE } = {}) =>
    `page:${String(variable || "").trim()}:${activityPathLabel(path)}:${Math.max(0, Number(offset || 0))}:${Math.max(1, Number(limit || COLLECTION_PAGE_SIZE))}`;

  const valueWatchOperationKey = ({ variable = "", path = "" } = {}) =>
    `value-watch:${String(variable || "").trim()}:${activityPathLabel(path)}`;

  const pageWatchOperationKey = ({ variable = "", path = "", offset = 0, limit = COLLECTION_PAGE_SIZE } = {}) =>
    `page-watch:${String(variable || "").trim()}:${activityPathLabel(path)}:${Math.max(0, Number(offset || 0))}:${Math.max(1, Number(limit || COLLECTION_PAGE_SIZE))}`;

  const activeOperationStatus = (entry = {}) =>
    normalizeStatus(entry?.status || entry?.materialization || entry?.phase || "running");

  function logComputeActivity(entry = {}) {
    try {
      pushComputeActivity(entry);
    } catch {
      // ignore logging failures
    }
  }

  const logResolveLifecycle = (event, details = {}) => {
    const variable = String(details?.variable || primaryVariable || "").trim();
    const path = activityPathLabel(details?.path || currentPath || "/");
    const baseTarget = activityTargetLabel({ variable, path });
    const computeStatus = String(details?.computeStatus || details?.status || "").trim().toLowerCase();
    const materialization = String(details?.materialization || "").trim().toLowerCase();
    const isTimeout = /timed out/i.test(String(details?.message || ""));

    if (event === "poll-start" || event === "poll-tick" || event === "poll-timeout") {
      const summary =
        event === "poll-start"
          ? `Live updates active for ${baseTarget}`
          : event === "poll-timeout"
            ? `Live updates closed for ${baseTarget}`
            : `Waiting for backend result ${baseTarget}`;
      logComputeActivity({
        type: `resolve.${event}`,
        phase: event === "poll-start" ? "start" : event === "poll-timeout" ? "finish" : "update",
        trackActive: true,
        skipHistory: event === "poll-tick",
        final: event === "poll-timeout",
        operationKey: pollOperationKey({ variable, path }),
        summary,
        variable,
        path,
        status: event === "poll-timeout" ? "timeout" : "running",
        detail: Number.isFinite(Number(details?.ticks)) && Number(details?.ticks) > 0 ? `ticks=${Number(details.ticks)}` : "",
        source: "start-tab",
      });
      return;
    }

    const operationKey = resolveOperationKey({
      traceId: Number(details?.traceId || 0),
      variable,
      path,
    });
    let entry = null;

    switch (event) {
      case "start":
        entry = {
          type: "resolve.primary",
          phase: "start",
          trackActive: true,
          operationKey,
          summary: `Fetching selected value ${baseTarget}`,
          variable,
          path,
          status: "running",
          detail: details?.background ? "background refresh" : details?.enqueue === false ? "cache-first probe" : "",
        };
        break;
      case "request-dispatch":
        entry = {
          type: "resolve.primary",
          phase: "update",
          trackActive: true,
          operationKey,
          summary: `Sent resolve request for ${baseTarget}`,
          variable,
          path,
          status: "running",
          detail: details?.enqueue === false ? "enqueue=false" : "enqueue=true",
        };
        break;
      case "response":
        entry = {
          type: "resolve.primary",
          phase: "update",
          trackActive: true,
          operationKey,
          summary: `Received resolve reply for ${baseTarget}`,
          variable,
          path,
          status: computeStatus || materialization || "running",
          materialization,
          detail: [
            Number.isFinite(Number(details?.elapsedMs)) ? `elapsed=${Number(details.elapsedMs).toFixed(1)}ms` : "",
            details?.jobId ? `job=${details.jobId}` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        };
        break;
      case "branch-materialized":
        entry = {
          type: "resolve.primary",
          phase: "finish",
          trackActive: true,
          final: true,
          operationKey,
          summary: `Resolved ${baseTarget}`,
          variable,
          path,
          status: "completed",
          materialization,
          detail: details?.voxType ? `type=${details.voxType}` : "",
        };
        break;
      case "branch-failed":
        entry = {
          type: "resolve.primary",
          phase: "finish",
          trackActive: true,
          final: true,
          operationKey,
          summary: `Failed ${baseTarget}`,
          variable,
          path,
          status: "failed",
          materialization,
          detail: String(details?.error || ""),
        };
        break;
      case "branch-pending":
        entry = {
          type: "resolve.primary",
          phase: "finish",
          trackActive: true,
          final: true,
          operationKey,
          summary: `Waiting for backend result ${baseTarget}`,
          variable,
          path,
          status: computeStatus || materialization || "running",
          materialization,
          detail: details?.hasProgressSignal ? "handoff to live updates" : "no live progress signal",
        };
        break;
      case "branch-unexpected":
        entry = {
          type: "resolve.primary",
          phase: "finish",
          trackActive: true,
          final: true,
          operationKey,
          summary: `Unexpected resolve state for ${baseTarget}`,
          variable,
          path,
          status: "failed",
          materialization,
        };
        break;
      case "request-error":
        entry = {
          type: "resolve.primary",
          phase: "finish",
          trackActive: true,
          final: true,
          operationKey,
          summary: isTimeout ? `Request timed out for ${baseTarget}` : `Request failed for ${baseTarget}`,
          variable,
          path,
          status: isTimeout ? "timeout" : "failed",
          detail: String(details?.message || ""),
        };
        break;
      case "response-stale":
      case "request-error-stale":
        entry = {
          type: "resolve.primary",
          phase: "finish",
          trackActive: true,
          final: true,
          operationKey,
          summary: `Discarded stale response for ${baseTarget}`,
          variable,
          path,
          status: "cancelled",
        };
        break;
      case "run-primary":
        entry = {
          type: "resolve.intent",
          phase: "event",
          summary: `Run requested for ${baseTarget}`,
          variable,
          path,
          status: statusValue,
        };
        break;
      case "symbol-click":
        entry = {
          type: "resolve.intent",
          phase: "event",
          summary: `Selected ${String(details?.token || variable || "").trim()}`,
          variable: String(details?.token || variable || "").trim(),
          path: "/",
          status: normalizeStatus(details?.knownStatus || "idle"),
        };
        break;
      case "skip-diagnostics":
        entry = {
          type: "resolve.intent",
          phase: "event",
          summary: `Resolve blocked by diagnostics for ${baseTarget}`,
          variable,
          path,
          status: "failed",
          detail: `diagnostics=${Number(details?.diagnostics || 0)}`,
        };
        break;
      case "skip-no-primary":
        entry = {
          type: "resolve.intent",
          phase: "event",
          summary: "No primary variable to resolve",
          status: "idle",
        };
        break;
      default:
        break;
    }

    if (entry) {
      logComputeActivity({
        ...entry,
        source: "start-tab",
      });
    }
  };

  const logValueWatchActivity = ({
    phase = "update",
    final = false,
    variable = "",
    path = "",
    status = "",
    detail = "",
    skipHistory = false,
  } = {}) => {
    const resolvedVariable = String(variable || primaryVariable || "").trim();
    const resolvedPath = activityPathLabel(path);
    logComputeActivity({
      type: "value.watch",
      phase,
      final,
      skipHistory,
      trackActive: true,
      operationKey: valueWatchOperationKey({ variable: resolvedVariable, path: resolvedPath }),
      summary:
        phase === "start"
          ? `Live updates active for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
          : final
            ? `Live updates closed for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
            : `Live value update received for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`,
      variable: resolvedVariable,
      path: resolvedPath,
      status: status || (final ? "completed" : "running"),
      detail,
      source: "start-tab",
    });
  };

  const logPageWatchActivity = ({
    phase = "update",
    final = false,
    variable = "",
    path = "",
    offset = 0,
    limit = COLLECTION_PAGE_SIZE,
    status = "",
    detail = "",
    skipHistory = false,
  } = {}) => {
    const resolvedVariable = String(variable || primaryVariable || "").trim();
    const resolvedPath = activityPathLabel(path);
    logComputeActivity({
      type: "page.watch",
      phase,
      final,
      skipHistory,
      trackActive: true,
      operationKey: pageWatchOperationKey({ variable: resolvedVariable, path: resolvedPath, offset, limit }),
      summary:
        phase === "start"
          ? `Live page updates active for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
          : final
            ? `Live page updates closed for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
            : `Live page update received for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`,
      variable: resolvedVariable,
      path: resolvedPath,
      status: status || (final ? "completed" : "running"),
      detail,
      source: "start-tab",
    });
  };

  const logPathLoadActivity = ({
    phase = "update",
    final = false,
    variable = "",
    path = "",
    status = "",
    detail = "",
    skipHistory = false,
  } = {}) => {
    const resolvedVariable = String(variable || "").trim();
    const resolvedPath = activityPathLabel(path);
    logComputeActivity({
      type: "path.load",
      phase,
      final,
      skipHistory,
      trackActive: true,
      operationKey: pathLoadOperationKey({ variable: resolvedVariable, path: resolvedPath }),
      summary:
        phase === "start"
          ? `Fetching nested value ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
          : final
            ? `Nested value settled for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
            : `Waiting for nested value ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`,
      variable: resolvedVariable,
      path: resolvedPath,
      status: status || (final ? "completed" : "running"),
      detail,
      source: "start-tab",
    });
  };

  const logPageLoadActivity = ({
    phase = "update",
    final = false,
    variable = "",
    path = "",
    offset = 0,
    limit = COLLECTION_PAGE_SIZE,
    status = "",
    detail = "",
    skipHistory = false,
  } = {}) => {
    const resolvedVariable = String(variable || "").trim();
    const resolvedPath = activityPathLabel(path);
    logComputeActivity({
      type: "page.load",
      phase,
      final,
      skipHistory,
      trackActive: true,
      operationKey: pageLoadOperationKey({ variable: resolvedVariable, path: resolvedPath, offset, limit }),
      summary:
        phase === "start"
          ? `Fetching page ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
          : final
            ? `Page settled for ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`
            : `Waiting for page items ${activityTargetLabel({ variable: resolvedVariable, path: resolvedPath })}`,
      variable: resolvedVariable,
      path: resolvedPath,
      status: status || (final ? "completed" : "running"),
      detail: [detail, `offset=${Math.max(0, Number(offset || 0))}`, `limit=${Math.max(1, Number(limit || COLLECTION_PAGE_SIZE))}`]
        .filter(Boolean)
        .join(" · "),
      source: "start-tab",
    });
  };

  const traceResolve = (event, details = {}) => {
    try {
      console.info("[start-tab.resolve]", {
        event,
        ...details,
      });
    } catch {
      // best-effort instrumentation only
    }
    logResolveLifecycle(event, details);
  };

  const clearResolutionActivity = () => {
    resolutionActivityRows = [];
    resolutionActivitySummary = "No resolution activity yet.";
    activitySeenKeys = new Set();
  };

  const hasActiveComputation = () => {
    if (pendingPoll || resolveInFlight) return true;
    if (Object.values(symbolStatuses || {}).some((value) => ["queued", "running", "persisting"].includes(normalizeStatus(value)))) {
      return true;
    }
    if (Object.keys(recordPagesLoading || {}).length) return true;
    if (Object.keys(pathRecordsLoading || {}).length) return true;
    if (Object.keys(recordPagePollTimers || {}).length) return true;
    if (Object.keys(pathRecordPollTimers || {}).length) return true;
    return false;
  };

  const syncSymbolStatuses = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      next[name] = normalizeStatus(symbolStatuses?.[name] || "idle");
    }
    symbolStatuses = next;
  };

  const normalizeSelectionNames = (names = []) =>
    [...new Set((Array.isArray(names) ? names : [names]).map((name) => String(name || "").trim()).filter(Boolean))];

  const syncSymbolMaterializations = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      next[name] = normalizeMaterialization(symbolMaterializations?.[name] || "unresolved");
    }
    symbolMaterializations = next;
  };

  const syncMaterializedRecords = (symbols, preservedNames = []) => {
    const next = {};
    for (const name of new Set([...Object.keys(symbols || {}), ...normalizeSelectionNames(preservedNames)])) {
      if (materializedRecords?.[name]) {
        next[name] = materializedRecords[name];
      }
    }
    materializedRecords = next;
  };

  const syncSymbolTypeHints = (symbols, staticHints = {}, preservedNames = []) => {
    const next = {};
    for (const name of new Set([...Object.keys(symbols || {}), ...normalizeSelectionNames(preservedNames)])) {
      const hinted = String(staticHints?.[name] || symbolTypeHints?.[name] || "").trim();
      const materializedType = String(materializedRecords?.[name]?.descriptor?.vox_type || "").trim();
      if (materializedType && (!hinted || hinted === "scalar" || hinted === "unknown")) {
        next[name] = materializedType;
      } else if (hinted) {
        next[name] = hinted;
      } else if (materializedType) {
        next[name] = String(materializedRecords[name].descriptor.vox_type);
      }
    }
    symbolTypeHints = next;
  };

  const ensureSelectedVisualSymbols = ({ preserveRenderedSelection = false } = {}) => {
    const names = Object.keys(symbolTable || {});
    const retained = normalizeSelectionNames(selectedVisualSymbols).filter((name) => names.includes(name));
    if (retained.length) {
      selectedVisualSymbols = retained;
      return;
    }
    if (preserveRenderedSelection) {
      const preserved = normalizeSelectionNames(selectedVisualSymbols).filter((name) => materializedRecords?.[name]);
      if (preserved.length) {
        selectedVisualSymbols = preserved;
        return;
      }
      if (primaryVariable && materializedRecords?.[primaryVariable]) {
        selectedVisualSymbols = [primaryVariable];
        return;
      }
    }
    if (primaryVariable && names.includes(primaryVariable)) {
      selectedVisualSymbols = [primaryVariable];
      return;
    }
    if (names.length) {
      selectedVisualSymbols = [names[0]];
      return;
    }
    selectedVisualSymbols = [];
  };

  const setSymbolMaterialization = (name, materialization) => {
    const symbolName = String(name || "").trim();
    if (!symbolName || !symbolTable?.[symbolName]) return;
    symbolMaterializations = {
      ...symbolMaterializations,
      [symbolName]: normalizeMaterialization(materialization),
    };
  };

  const setSymbolStatus = (name, status) => {
    const symbolName = String(name || "").trim();
    if (!symbolName || !symbolTable?.[symbolName]) return;
    const nextStatus = normalizeStatus(status);
    const currentStatus = normalizeStatus(symbolStatuses?.[symbolName] || "idle");
    if (statusRank[nextStatus] < statusRank[currentStatus] && currentStatus !== "failed") return;
    symbolStatuses = { ...symbolStatuses, [symbolName]: nextStatus };
  };

  const hasKnownPendingState = (variableName = "") => {
    const symbolName = String(variableName || "").trim();
    if (!symbolName || !symbolTable?.[symbolName]) return false;
    const status = normalizeStatus(symbolStatuses?.[symbolName] || "idle");
    return isActiveComputeStatus(status);
  };

  const symbolNamesByNodeId = () => {
    const byNode = {};
    for (const [name, node] of Object.entries(symbolTable || {})) {
      if (!node) continue;
      const key = String(node);
      if (!byNode[key]) byNode[key] = [];
      byNode[key].push(String(name));
    }
    return byNode;
  };

  const pushResolutionActivity = ({
    variableName = "",
    nodeId = "",
    operator = "",
    status = "running",
    cacheSource = "",
    durationS = 0,
  } = {}) => {
    const normalizedStatus = normalizeStatus(status);
    const key = `${variableName}|${nodeId}|${operator}|${normalizedStatus}|${cacheSource}|${Number(durationS || 0).toFixed(6)}`;
    if (activitySeenKeys.has(key)) return;
    activitySeenKeys.add(key);
    const nextRow = {
      ts: new Date().toISOString(),
      variableName: String(variableName || ""),
      nodeId: String(nodeId || ""),
      operator: String(operator || ""),
      status: normalizedStatus,
      cacheSource: String(cacheSource || ""),
      durationS: Number(durationS || 0),
    };
    resolutionActivityRows = [nextRow, ...resolutionActivityRows].slice(0, 180);
    resolutionActivitySummary = `events ${resolutionActivityRows.length} | computed ${Object.values(symbolStatuses).filter((s) => s === "computed").length}/${Object.keys(symbolTable || {}).length || 0}`;
  };

  const provideEditorCompletions = ({ prefix = "" }) => {
    const base = new Map();
    for (const keyword of ["import", "let", "in", "for", "do", "true", "false"]) {
      base.set(keyword, {
        label: keyword,
        insertText: keyword,
        kind: "keyword",
        detail: "language keyword",
      });
    }
    for (const builtin of COMPLETION_BUILTINS) {
      base.set(builtin, {
        label: builtin,
        insertText: builtin,
        kind: "primitive",
        detail: "known primitive",
      });
    }
    for (const symbol of Object.keys(symbolTable || {})) {
      base.set(symbol, {
        label: symbol,
        insertText: symbol,
        kind: "symbol",
        detail: "declared symbol",
      });
    }
    const prefixLower = String(prefix || "").toLowerCase();
    return Array.from(base.values())
      .filter((item) => !prefixLower || item.label.toLowerCase().startsWith(prefixLower))
      .sort((a, b) => String(a.label).localeCompare(String(b.label)));
  };

  const stopPoll = () => {
    if (pendingPoll) {
      clearInterval(pendingPoll);
      pendingPoll = null;
    }
    pendingPollTicks = 0;
  };

  const stopEditRefresh = () => {
    if (!pendingEditRefresh) return;
    clearTimeout(pendingEditRefresh);
    pendingEditRefresh = null;
  };

  const scheduleEditRefreshRetry = (token, delayMs = EDIT_PENDING_RETRY_MS) => {
    stopEditRefresh();
    pendingEditRefresh = setTimeout(() => {
      if (destroyed) return;
      pendingEditRefresh = null;
      void refreshDisplayedValuesAfterEdit(token);
    }, delayMs);
  };

  const wsReconnectDelayMs = (attempt) => {
    const clamped = Math.max(0, Math.min(6, Number(attempt || 0)));
    return 220 + clamped * 300;
  };

  const wsBaseUrl = () => {
    const configured = String(import.meta?.env?.VITE_VOXLOGICA_DEV_BACKEND_URL || "").trim();
    if (configured) {
      try {
        const parsed = new URL(configured);
        parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
        return parsed.toString().replace(/\/$/, "");
      } catch {
        // fall through
      }
    }
    const protocol = window?.location?.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}`;
  };

  const stopValueSocket = ({ logFinal = true } = {}) => {
    const subscription = activeValueSubscription ? { ...activeValueSubscription } : null;
    if (valueWsReconnectTimer) {
      clearTimeout(valueWsReconnectTimer);
      valueWsReconnectTimer = null;
    }
    const socket = valueWs;
    valueWs = null;
    if (socket) {
      try {
        socket.close();
      } catch {
        // ignore close errors
      }
    }
    if (logFinal && subscription?.variable) {
      logValueWatchActivity({
        phase: "finish",
        final: true,
        variable: subscription.variable,
        path: subscription.path || "/",
        status: "cancelled",
        detail: "subscription closed",
        skipHistory: true,
      });
    }
  };

  const stopRecordPageSocket = (baseKey = "", { dropSubscription = true, logFinal = true } = {}) => {
    const key = String(baseKey || "");
    if (!key) return;
    const subscription = recordPageSubscriptions?.[key] ? { ...recordPageSubscriptions[key] } : null;
    const reconnectTimer = recordPageSocketReconnectTimers?.[key];
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }
    const socket = recordPageSockets?.[key] || null;
    const nextSockets = { ...recordPageSockets };
    delete nextSockets[key];
    recordPageSockets = nextSockets;
    const nextReconnectTimers = { ...recordPageSocketReconnectTimers };
    delete nextReconnectTimers[key];
    recordPageSocketReconnectTimers = nextReconnectTimers;
    const nextAttempts = { ...recordPageSocketAttempts };
    delete nextAttempts[key];
    recordPageSocketAttempts = nextAttempts;
    if (dropSubscription) {
      const nextSubscriptions = { ...recordPageSubscriptions };
      delete nextSubscriptions[key];
      recordPageSubscriptions = nextSubscriptions;
    }
    if (socket) {
      try {
        socket.close();
      } catch {
        // ignore close errors
      }
    }
    if (logFinal && subscription?.variable) {
      logPageWatchActivity({
        phase: "finish",
        final: true,
        variable: subscription.variable,
        path: subscription.path || "/",
        offset: subscription.offset,
        limit: subscription.limit,
        status: "cancelled",
        detail: "page subscription closed",
        skipHistory: true,
      });
    }
  };

  const stopAllRecordPageSockets = () => {
    for (const baseKey of Object.keys(recordPageSockets || {})) {
      stopRecordPageSocket(baseKey);
    }
    for (const timer of Object.values(recordPageSocketReconnectTimers || {})) {
      clearTimeout(timer);
    }
    recordPageSockets = {};
    recordPageSocketReconnectTimers = {};
    recordPageSocketAttempts = {};
    recordPageSubscriptions = {};
  };

  const applyStreamValuePayload = (payload = null) => {
    if (!payload || !activeValueSubscription) return;
    const requestedVariable = String(activeValueSubscription.variable || "");
    const requestedPath = String(activeValueSubscription.path || "");
    if (requestedVariable && requestedVariable !== String(primaryVariable || "")) return;
    if (requestedPath !== String(currentPath || "")) return;

    const materialization = String(payload?.materialization || "").toLowerCase();
    const computeStatus = String(payload?.compute_status || "").toLowerCase();
    const descriptor = payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : null;
    const isMaterialized = (materialization === "cached" || materialization === "computed") && !!descriptor;
    const isPending = isPendingLikePayload(payload);

    if (isMaterialized) {
      activeValueSubscription = null;
      stopValueSocket({ logFinal: false });
      stopPoll();
      applyMaterialized(payload, requestedVariable || String(primaryVariable || ""));
      return;
    }
    if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") {
      activeValueSubscription = null;
      stopValueSocket({ logFinal: false });
      stopPoll();
      setSymbolStatus(requestedVariable || String(primaryVariable || ""), "failed");
      applyFailure(payload, requestedVariable || String(primaryVariable || ""));
      return;
    }
    if (isPending) {
      if (!hasLiveProgressSignal(payload)) {
        activeValueSubscription = null;
        stopValueSocket({ logFinal: false });
        stopPoll();
        statusValue = "idle";
        statusText = `${requestedVariable || String(primaryVariable || "")} is not ready yet. Click Run or click the tag again to refresh.`;
        errorText = "";
        setSymbolStatus(requestedVariable || String(primaryVariable || ""), "idle");
        setSymbolMaterialization(
          requestedVariable || String(primaryVariable || ""),
          materialization === "missing" ? "unresolved" : materialization || "unresolved",
        );
        dissolveDream();
        return;
      }
      applyPending(payload, requestedVariable || String(primaryVariable || ""));
    }
  };

  const ensureValueSocket = () => {
    if (valueWs && (valueWs.readyState === WebSocket.OPEN || valueWs.readyState === WebSocket.CONNECTING)) {
      return;
    }
    stopValueSocket({ logFinal: false });
    valueWs = new WebSocket(`${wsBaseUrl()}/ws/playground/value`);
    valueWs.onopen = () => {
      if (destroyed) return;
      valueWsAttempts = 0;
      if (!activeValueSubscription) return;
      logValueWatchActivity({
        phase: "start",
        variable: activeValueSubscription.variable,
        path: activeValueSubscription.path || "/",
        status: "running",
        detail: "websocket subscribed",
      });
      valueWs.send(
        JSON.stringify({
          type: "subscribe",
          request: {
            program: activeValueSubscription.program || programText,
            execution_strategy: "dask",
            node_id: "",
            variable: activeValueSubscription.variable || "",
            path: activeValueSubscription.path || "",
            enqueue: activeValueSubscription.enqueue !== false,
          },
        }),
      );
    };
    valueWs.onmessage = (event) => {
      if (destroyed) return;
      try {
        const message = JSON.parse(String(event.data || "{}"));
        if (message?.type === "value" || message?.type === "terminal") {
          applyStreamValuePayload(message?.payload || null);
          const payloadStatus = String(message?.payload?.compute_status || message?.payload?.materialization || "").toLowerCase();
          const terminal =
            message?.type === "terminal" ||
            ["computed", "cached", "failed", "killed"].includes(String(message?.payload?.materialization || "").toLowerCase()) ||
            ["completed", "failed", "killed"].includes(payloadStatus);
          logValueWatchActivity({
            phase: terminal ? "finish" : "update",
            final: terminal,
            variable: String(message?.payload?.variable || activeValueSubscription?.variable || ""),
            path: String(message?.payload?.path || activeValueSubscription?.path || "/"),
            status: payloadStatus || String(message?.type || ""),
            detail: String(message?.payload?.error || message?.payload?.state_reason || ""),
          });
          logComputeActivity({
            type: "value.ws",
            phase: "event",
            summary: terminal
              ? `Live value update completed for ${activityTargetLabel({
                  variable: String(message?.payload?.variable || activeValueSubscription?.variable || ""),
                  path: String(message?.payload?.path || activeValueSubscription?.path || "/"),
                })}`
              : `Live value update for ${activityTargetLabel({
                  variable: String(message?.payload?.variable || activeValueSubscription?.variable || ""),
                  path: String(message?.payload?.path || activeValueSubscription?.path || "/"),
                })}`,
            variable: String(message?.payload?.variable || activeValueSubscription?.variable || ""),
            path: String(message?.payload?.path || activeValueSubscription?.path || ""),
            status: String(message?.type || ""),
            materialization: String(message?.payload?.materialization || ""),
            detail: String(message?.payload?.error || ""),
            source: "primary-ws",
          });
        }
      } catch {
        // ignore malformed ws messages
      }
    };
    valueWs.onclose = () => {
      if (destroyed) return;
      valueWs = null;
      if (!activeValueSubscription) return;
      valueWsAttempts += 1;
      const delay = wsReconnectDelayMs(valueWsAttempts);
      if (valueWsReconnectTimer) clearTimeout(valueWsReconnectTimer);
      valueWsReconnectTimer = setTimeout(() => {
        if (destroyed) return;
        ensureValueSocket();
      }, delay);
    };
    valueWs.onerror = () => {
      if (destroyed) return;
      try {
        valueWs?.close();
      } catch {
        // ignore
      }
    };
  };

  const subscribeValueSocket = ({ variable = "", path = "", enqueue = true } = {}) => {
    if (typeof window === "undefined" || typeof window.WebSocket !== "function") return false;
    activeValueSubscription = {
      program: programText,
      variable: String(variable || ""),
      path: String(path || ""),
      enqueue: Boolean(enqueue),
    };
    logValueWatchActivity({
      phase: "start",
      variable: activeValueSubscription.variable,
      path: activeValueSubscription.path || "/",
      status: "running",
      detail: activeValueSubscription.enqueue ? "awaiting live updates" : "cache-first live updates",
    });
    ensureValueSocket();
    if (valueWs && valueWs.readyState === WebSocket.OPEN) {
      valueWs.send(
        JSON.stringify({
          type: "subscribe",
          request: {
            program: activeValueSubscription.program,
            execution_strategy: "dask",
            node_id: "",
            variable: activeValueSubscription.variable,
            path: activeValueSubscription.path,
            enqueue: activeValueSubscription.enqueue,
          },
        }),
      );
    }
    return true;
  };

  const isTimeoutError = (error) => /timed out/i.test(String(error?.message || error || ""));

  const isUnknownNodeSelectionError = (error) => /unknown node selection/i.test(String(error?.message || error || ""));

  const ensurePendingPoll = ({ traceId = 0, variable = "", path = "" } = {}) => {
    if (pendingPoll) return;
    traceResolve("poll-start", {
      traceId,
      variable,
      path: path || "/",
    });
    pendingPollTicks = 0;
    pendingPoll = setInterval(() => {
      if (destroyed) return;
      if (resolveInFlight) return;
      pendingPollTicks += 1;
      if (pendingPollTicks > MAX_PENDING_POLL_TICKS) {
        traceResolve("poll-timeout", {
          traceId,
          variable,
          path: path || "/",
          ticks: pendingPollTicks,
        });
        stopPoll();
        statusValue = "idle";
        statusText = `${variable} is still computing in the background. Click Run to refresh status.`;
        errorText = "";
        dissolveDream();
        return;
      }
      traceResolve("poll-tick", {
        traceId,
        variable,
        path: path || "/",
        ticks: pendingPollTicks,
      });
      void resolvePrimaryValue({ enqueue: false, path: currentPath, background: true });
    }, 1000);
  };

  const clampSplitRatio = (value) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, Number(value || 0.5)));

  const updateSplitRatioFromClientX = (clientX) => {
    const rect = startPrimeGridEl?.getBoundingClientRect?.();
    if (!rect || !rect.width) return;
    const ratio = (Number(clientX || 0) - rect.left) / rect.width;
    splitRatio = clampSplitRatio(ratio);
  };

  const stopSplitDrag = () => {
    if (splitDragCleanup) {
      splitDragCleanup();
      splitDragCleanup = null;
    }
    splitDragActive = false;
  };

  const handleSplitPointerDown = (event) => {
    if (event?.button !== 0) return;
    event.preventDefault();
    updateSplitRatioFromClientX(event.clientX);
    splitDragActive = true;
    const onMove = (moveEvent) => {
      if (!splitDragActive) return;
      updateSplitRatioFromClientX(moveEvent.clientX);
    };
    const onUp = () => stopSplitDrag();
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    splitDragCleanup = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  };

  const handleSplitKeyDown = (event) => {
    const key = String(event?.key || "");
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(key)) return;
    event.preventDefault();
    if (key === "Home") {
      splitRatio = SPLIT_MIN;
      return;
    }
    if (key === "End") {
      splitRatio = SPLIT_MAX;
      return;
    }
    const delta = key === "ArrowLeft" ? -0.02 : 0.02;
    splitRatio = clampSplitRatio(splitRatio + delta);
  };

  const markRecordMaterialized = (nodeId = "") => {
    const key = String(nodeId || "").trim();
    if (!key) return;
    const existingTimer = recentMaterializeTimers?.[key];
    if (existingTimer) clearTimeout(existingTimer);
    recentlyMaterialized = {
      ...recentlyMaterialized,
      [key]: true,
    };
    const timer = setTimeout(() => {
      if (destroyed) return;
      const nextPulse = { ...recentlyMaterialized };
      delete nextPulse[key];
      recentlyMaterialized = nextPulse;
      const nextTimers = { ...recentMaterializeTimers };
      delete nextTimers[key];
      recentMaterializeTimers = nextTimers;
    }, 1100);
    recentMaterializeTimers = {
      ...recentMaterializeTimers,
      [key]: timer,
    };
  };

  const stopProbe = () => {
    if (!pendingProbe) return;
    clearTimeout(pendingProbe);
    pendingProbe = null;
  };

  const buildPersistedStartState = () => ({
    programText: String(programText || ""),
    editor:
      editorViewState && typeof editorViewState === "object"
        ? {
            selectionStart: Math.max(0, Number(editorViewState.selectionStart || 0)),
            selectionEnd: Math.max(0, Number(editorViewState.selectionEnd || editorViewState.selectionStart || 0)),
            scrollTop: Math.max(0, Number(editorViewState.scrollTop || 0)),
            scrollLeft: Math.max(0, Number(editorViewState.scrollLeft || 0)),
          }
        : null,
    layout: {
      showCodePanel,
      showResultsPanel,
      showOperationsPanel,
      showOperationsHelp,
      splitRatio,
    },
    viewer: {
      primaryVariable: String(primaryVariable || ""),
      currentPath: String(currentPath || ""),
      selectedVisualSymbols: Array.isArray(selectedVisualSymbols) ? [...selectedVisualSymbols] : [],
      maximizedViewerIndex,
      collectionSelections,
      recordPagePointers,
      expandedCollectionStages,
    },
  });

  const schedulePersist = () => {
    if (pendingSave) clearTimeout(pendingSave);
    pendingSave = setTimeout(() => {
      if (destroyed) return;
      updatePersistedStartState(buildPersistedStartState());
    }, 180);
  };

  const restorePersistedStartState = async () => {
    const persisted = readPersistedStartState();
    programText = String(persisted?.programText || programText || "");
    editorViewState = persisted?.editor && typeof persisted.editor === "object" ? { ...persisted.editor } : null;
    showCodePanel = Boolean(persisted?.layout?.showCodePanel ?? showCodePanel);
    showResultsPanel = Boolean(persisted?.layout?.showResultsPanel ?? showResultsPanel);
    showOperationsPanel = Boolean(persisted?.layout?.showOperationsPanel ?? showOperationsPanel);
    showOperationsHelp = Boolean(persisted?.layout?.showOperationsHelp ?? showOperationsHelp);
    splitRatio = clampSplitRatio(persisted?.layout?.splitRatio ?? splitRatio);
    primaryVariable = String(persisted?.viewer?.primaryVariable || primaryVariable || "");
    captionVariable = primaryVariable || captionVariable;
    currentPath = String(persisted?.viewer?.currentPath || "");
    selectedVisualSymbols = Array.isArray(persisted?.viewer?.selectedVisualSymbols)
      ? [...persisted.viewer.selectedVisualSymbols]
      : [];
    maximizedViewerIndex = Number.isInteger(persisted?.viewer?.maximizedViewerIndex)
      ? Number(persisted.viewer.maximizedViewerIndex)
      : -1;
    collectionSelections = persisted?.viewer?.collectionSelections && typeof persisted.viewer.collectionSelections === "object"
      ? { ...persisted.viewer.collectionSelections }
      : {};
    recordPagePointers = persisted?.viewer?.recordPagePointers && typeof persisted.viewer.recordPagePointers === "object"
      ? { ...persisted.viewer.recordPagePointers }
      : {};
    expandedCollectionStages = persisted?.viewer?.expandedCollectionStages && typeof persisted.viewer.expandedCollectionStages === "object"
      ? { ...persisted.viewer.expandedCollectionStages }
      : {};
    hasPersistedViewerRestore = Boolean(
      String(primaryVariable || "").trim() ||
        String(currentPath || "").trim() ||
        (Array.isArray(selectedVisualSymbols) && selectedVisualSymbols.length) ||
        (recordPagePointers && Object.keys(recordPagePointers).length) ||
        (collectionSelections && Object.keys(collectionSelections).length) ||
        (expandedCollectionStages && Object.keys(expandedCollectionStages).length),
    );
    await tick();
    if (startEditorRef && typeof startEditorRef.restoreViewState === "function" && editorViewState) {
      startEditorRef.restoreViewState(editorViewState);
    }
  };

  const handleEditorViewState = (event) => {
    const state = event?.detail;
    if (!state || typeof state !== "object") return;
    editorViewState = {
      selectionStart: Math.max(0, Number(state.selectionStart || 0)),
      selectionEnd: Math.max(0, Number(state.selectionEnd || state.selectionStart || 0)),
      scrollTop: Math.max(0, Number(state.scrollTop || 0)),
      scrollLeft: Math.max(0, Number(state.scrollLeft || 0)),
    };
  };

  const applyDisplayedValueRefresh = (payload, variableName) => {
    refreshVariableNestedCaches(variableName, payload);
    cacheInlinePreviewPageForRecord(payload, variableName, String(payload?.path || currentPath || "/"));
    const descriptor = payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : null;
    const materialization = String(payload?.materialization || payload?.status || "materialized");
    setSymbolStatus(variableName, "computed");
    setSymbolMaterialization(variableName, materialization);
    if (descriptor?.vox_type) {
      symbolTypeHints = {
        ...symbolTypeHints,
        [variableName]: String(descriptor.vox_type),
      };
    }
    materializedRecords = {
      ...materializedRecords,
      [variableName]: payload,
    };
    markRecordMaterialized(String(payload?.node_id || ""));
    renderSelectedRecords({ fallbackRecord: payload });
  };

  const markDisplayedValueRefreshPending = (payload, variableName) => {
    const state = String(payload?.compute_status || payload?.materialization || "running");
    setSymbolStatus(variableName, state);
    setSymbolMaterialization(variableName, payload?.materialization || state);
    if (String(variableName || "") === String(primaryVariable || "")) {
      statusValue = "running";
      statusText = `Updating ${variableName}...`;
      captionVariable = variableName || "-";
      errorText = "";
      applyPendingLogs(payload, state);
    }
  };

  const markDisplayedValueRefreshFailed = (payload, variableName) => {
    setSymbolStatus(variableName, "failed");
    setSymbolMaterialization(variableName, "failed");
    if (materializedRecords?.[variableName]) {
      const next = { ...materializedRecords };
      delete next[variableName];
      materializedRecords = next;
    }
    if (String(variableName || "") === String(primaryVariable || "")) {
      applyFailure(payload, variableName);
    } else {
      renderSelectedRecords();
    }
  };

  const refreshDisplayedValuesAfterEdit = async (token) => {
    if (destroyed) return;
    if (token !== editRefreshSeq) return;
    if (symbolDiagnostics.length) return;
    const names = [...new Set((selectedVisualSymbols.length ? selectedVisualSymbols : [primaryVariable])
      .map((name) => String(name || "").trim())
      .filter((name) => name && symbolTable?.[name]))];
    if (!names.length) return;

    const programSnapshot = String(programText || "");
    let primaryPending = false;
    let secondaryPending = false;
    const primaryName = String(primaryVariable || "");

    for (const variableName of names) {
      if (token !== editRefreshSeq) return;
      if (programSnapshot !== String(programText || "")) return;
      try {
        const payload = await resolvePlaygroundValue({
          program: programSnapshot,
          variable: variableName,
          path: variableName === String(primaryVariable || "") ? currentPath : "",
          enqueue: false,
          uiAwaited: false,
          interaction: buildInteractionContext({
            intent: "edit-refresh",
            source: "editor",
            variable: variableName,
            path: variableName === String(primaryVariable || "") ? currentPath : "",
            direct: false,
            visible: variableName === String(primaryVariable || ""),
          }),
        });
        if (destroyed) return;
        if (token !== editRefreshSeq) return;
        if (programSnapshot !== String(programText || "")) return;

        const materialization = String(payload?.materialization || "").toLowerCase();
        const computeStatus = String(payload?.compute_status || "").toLowerCase();
        const descriptor = payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : null;
        const isMaterialized = (materialization === "cached" || materialization === "computed") && !!descriptor;
        const isPending = isPendingLikePayload(payload);
        const hasProgressSignal = hasLiveProgressSignal(payload);

        if (isMaterialized) {
          applyDisplayedValueRefresh(payload, variableName);
          continue;
        }

        if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") {
          markDisplayedValueRefreshFailed(payload, variableName);
          continue;
        }

        if (isPending) {
          if (hasProgressSignal) {
            if (variableName === primaryName) {
              primaryPending = true;
            } else {
              secondaryPending = true;
            }
            markDisplayedValueRefreshPending(payload, variableName);
            continue;
          }

          if (variableName === primaryName) {
            const livePayload = await resolvePlaygroundValue({
              program: programSnapshot,
              variable: variableName,
              path: currentPath,
              enqueue: true,
              uiAwaited: false,
              interaction: buildInteractionContext({
                intent: "edit-refresh",
                source: "editor",
                variable: variableName,
                path: currentPath,
                direct: false,
                visible: true,
              }),
            });
            if (destroyed) return;
            if (token !== editRefreshSeq) return;
            if (programSnapshot !== String(programText || "")) return;

            const liveMaterialization = String(livePayload?.materialization || "").toLowerCase();
            const liveComputeStatus = String(livePayload?.compute_status || "").toLowerCase();
            const liveDescriptor = livePayload?.descriptor && typeof livePayload.descriptor === "object" ? livePayload.descriptor : null;
            const liveMaterialized = (liveMaterialization === "cached" || liveMaterialization === "computed") && !!liveDescriptor;
            const livePending = isPendingLikePayload(livePayload);
            const liveHasProgressSignal = hasLiveProgressSignal(livePayload);

            if (liveMaterialized) {
              applyDisplayedValueRefresh(livePayload, variableName);
              continue;
            }

            if (liveMaterialization === "failed" || liveComputeStatus === "failed" || liveComputeStatus === "killed") {
              markDisplayedValueRefreshFailed(livePayload, variableName);
              continue;
            }

            if (livePending && liveHasProgressSignal) {
              primaryPending = true;
              markDisplayedValueRefreshPending(livePayload, variableName);
            }
          }
          continue;
        }
      } catch {
        // keep the current rendered value until a later edit or explicit run resolves it
      }
    }

    if (token !== editRefreshSeq) return;
    if (programSnapshot !== String(programText || "")) return;

    if (primaryPending && primaryName && names.includes(primaryName)) {
      stopEditRefresh();
      subscribeValueSocket({ variable: primaryName, path: currentPath, enqueue: false });
      ensurePendingPoll({ variable: primaryName, path: currentPath || "/" });
    }

    if (secondaryPending) {
      scheduleEditRefreshRetry(token);
    }
  };

  const probeOneSymbolStatus = async (symbolName, token) => {
    if (!symbolName || token !== probeToken) return;
    try {
      const payload = await resolvePlaygroundValue({
        program: programText,
        variable: symbolName,
        path: "",
        enqueue: false,
        uiAwaited: false,
        interaction: {
          intent: "probe",
          source: "probe",
          sequence: 0,
          age_ms: 0,
          selected: false,
          visible: false,
          direct: false,
          path_depth: 0,
        },
      });
      if (destroyed) return;
      if (token !== probeToken) return;
      const materialization = String(payload?.materialization || "").trim().toLowerCase();
      const computeStatus = String(payload?.compute_status || "").trim().toLowerCase();
      if (materialization === "computed" || materialization === "cached") {
        setSymbolStatus(symbolName, "computed");
        setSymbolMaterialization(symbolName, payload?.materialization || "cached");
        if (payload?.descriptor?.vox_type) {
          symbolTypeHints = {
            ...symbolTypeHints,
            [symbolName]: String(payload.descriptor.vox_type),
          };
        }
        pushResolutionActivity({
          variableName: symbolName,
          nodeId: String(payload?.node_id || ""),
          operator: "probe",
          status: "computed",
          cacheSource: "store",
          durationS: 0,
        });
        return;
      }
      if (computeStatus === "failed" || materialization === "failed") {
        setSymbolStatus(symbolName, "failed");
        setSymbolMaterialization(symbolName, "failed");
        return;
      }
      if (isActiveComputeStatus(computeStatus)) {
        setSymbolStatus(symbolName, computeStatus);
        setSymbolMaterialization(symbolName, payload?.materialization || computeStatus);
        return;
      }
      setSymbolStatus(symbolName, "idle");
      setSymbolMaterialization(symbolName, "unresolved");
    } catch {
      // Ignore per-symbol probe failures; interactive click still drives execution.
    }
  };

  const scheduleSymbolProbe = () => {
    stopProbe();
    if (statusValue === "running" || pendingPoll) return;
    const names = Object.keys(symbolTable || {});
    if (!names.length || symbolDiagnostics.length) return;
    const token = probeToken + 1;
    probeToken = token;
    pendingProbe = setTimeout(() => {
      if (destroyed) return;
      pendingProbe = null;
      if (token !== probeToken || statusValue === "running" || pendingPoll) return;
      const safeNames = Object.keys(symbolTable || {});
      const batchSize = 4;
      const run = async () => {
        for (let idx = 0; idx < safeNames.length; idx += batchSize) {
          if (token !== probeToken || statusValue === "running" || pendingPoll) return;
          const batch = safeNames.slice(idx, idx + batchSize);
          await Promise.all(batch.map((name) => probeOneSymbolStatus(name, token)));
        }
      };
      void run();
    }, 120);
  };

  const inferPrimaryVariable = (sourceText, symbols) => {
    const symbolKeys = Object.keys(symbols || {});
    if (!symbolKeys.length) return "";

    for (const preferred of PRIMARY_VARIABLE_PREFERENCES) {
      if (symbolKeys.includes(preferred)) return preferred;
    }

    const declarationMatches = [...String(sourceText || "").matchAll(/^\s*([A-Za-z_.$][A-Za-z0-9_.$]*)\s*=/gm)];
    for (let idx = declarationMatches.length - 1; idx >= 0; idx -= 1) {
      const candidate = String(declarationMatches[idx]?.[1] || "");
      if (candidate && symbols[candidate]) return candidate;
    }

    return symbolKeys[symbolKeys.length - 1] || symbolKeys[0] || "";
  };

  const recordDescriptor = (record) =>
    record?.descriptor && typeof record.descriptor === "object" ? record.descriptor : { vox_type: "unavailable", summary: {} };

  const recordType = (record) => String(recordDescriptor(record)?.vox_type || "unavailable").toLowerCase();

  const collectionRecord = (record) => ["sequence", "mapping"].includes(recordType(record));

  const recordLabel = (record, index = 0) => {
    const nodeId = String(record?.node_id || "");
    const names = symbolNamesByNodeId()[nodeId] || [];
    const selected = names.find((name) => selectedVisualSymbols.includes(name));
    return selected || names[0] || (selectedVisualSymbols[index] ? selectedVisualSymbols[index] : `value ${index + 1}`);
  };

  const recordPath = (record) => String(record?.path || "");

  const pageKey = (record, path = "") => `${String(record?.node_id || "")}:${String(path || recordPath(record) || "/")}`;

  const pageCacheKey = (record, path = "", offset = 0, limit = COLLECTION_PAGE_SIZE) =>
    `${pageKey(record, path)}@${Math.max(0, Number(offset || 0))}:${Math.max(1, Number(limit || COLLECTION_PAGE_SIZE))}`;

  const pageForRecord = (record, path = "") => {
    const baseKey = pageKey(record, path);
    const pointer = recordPagePointers?.[baseKey];
    if (pointer && recordPages?.[pointer]) {
      return recordPages[pointer];
    }
    const prefix = `${baseKey}@`;
    const fallbackCacheKey = Object.keys(recordPages || {}).find((cacheKey) => cacheKey.startsWith(prefix));
    if (!fallbackCacheKey) return null;
    return recordPages[fallbackCacheKey] || null;
  };

  const mergePathRecordIntoPageItem = (item, sourceVariable = "") => {
    const itemPath = String(item?.path || "").trim();
    const variableName = String(sourceVariable || "").trim();
    if (!itemPath || !variableName) return item;
    const cached = pathRecordFor(variableName, itemPath);
    if (!cached || typeof cached !== "object") return item;
    const nextState = collectionItemStateFromPayload(cached);
    return mergeCollectionItemRecords(item, {
      ...item,
      descriptor: cached?.descriptor && typeof cached.descriptor === "object" ? cached.descriptor : item?.descriptor,
      state: nextState,
      status: collectionItemStatusFromState(nextState),
      error: String(cached?.error || item?.error || ""),
      blocked_on: String(cached?.blocked_on || item?.blocked_on || ""),
      state_reason: String(cached?.state_reason || item?.state_reason || ""),
    });
  };

  const pageErrorForRecord = (record, path = "") => recordPagesErrors?.[pageKey(record, path)] || "";

  const pageLoadingForRecord = (record, path = "") => {
    const baseKey = pageKey(record, path);
    const pointer = recordPagePointers?.[baseKey];
    if (pointer && recordPagesLoading?.[pointer]) return true;
    const prefix = `${baseKey}@`;
    return Object.keys(recordPagesLoading || {}).some((cacheKey) => cacheKey.startsWith(prefix));
  };

  const pagePollingForRecord = (record, path = "") =>
    Boolean(recordPagePollTimers?.[pageKey(record, path)] || recordPageSubscriptions?.[pageKey(record, path)]);

  const ensureRecordPageSocket = (
    record,
    { path = "", offset = 0, limit = COLLECTION_PAGE_SIZE, sourceVariable = "", enqueue = false } = {},
  ) => {
    if (!pageSocketsEnabled()) return false;
    if (!record || !collectionRecord(record)) return false;
    const resolvedPath = String(path || recordPath(record) || "");
    const baseKey = pageKey(record, resolvedPath);
    const variableName = String(sourceVariable || sourceVariableForRecord(record, 0) || "").trim();
    const subscription = {
      program: programText,
      variable: variableName,
      path: resolvedPath,
      offset: Math.max(0, Number(offset || 0)),
      limit: Math.max(1, Number(limit || COLLECTION_PAGE_SIZE)),
      enqueue: Boolean(enqueue),
    };
    recordPageSubscriptions = {
      ...recordPageSubscriptions,
      [baseKey]: subscription,
    };

    const sendSubscribe = (socket) => {
      socket.send(
        JSON.stringify({
          type: "subscribe",
          mode: "page",
          request: {
            program: subscription.program,
            execution_strategy: "dask",
            node_id: "",
            variable: subscription.variable,
            path: subscription.path,
            offset: subscription.offset,
            limit: subscription.limit,
            enqueue: subscription.enqueue,
          },
        }),
      );
    };

    const existing = recordPageSockets?.[baseKey];
    if (existing && existing.readyState === WebSocket.OPEN) {
      sendSubscribe(existing);
      return true;
    }
    if (existing && existing.readyState === WebSocket.CONNECTING) {
      return true;
    }

    stopRecordPageSocket(baseKey, { dropSubscription: false, logFinal: false });
    const socket = new WebSocket(`${wsBaseUrl()}/ws/playground/value`);
    recordPageSockets = {
      ...recordPageSockets,
      [baseKey]: socket,
    };
    socket.onopen = () => {
      if (destroyed) return;
      recordPageSocketAttempts = {
        ...recordPageSocketAttempts,
        [baseKey]: 0,
      };
      logPageWatchActivity({
        phase: "start",
        variable: subscription.variable,
        path: subscription.path || "/",
        offset: subscription.offset,
        limit: subscription.limit,
        status: "running",
        detail: "page websocket subscribed",
      });
      sendSubscribe(socket);
    };
    socket.onmessage = (event) => {
      if (destroyed) return;
      try {
        const message = JSON.parse(String(event.data || "{}"));
        const messageType = String(message?.type || "").toLowerCase();
        const messageMode = String(message?.mode || "page").toLowerCase();
        if (messageMode !== "page") return;
        if (messageType !== "page" && messageType !== "terminal") return;
        applyRecordPagePayload(record, {
          path: resolvedPath,
          offset: subscription.offset,
          limit: subscription.limit,
          payload: message?.payload || null,
        });
        const payloadStatus = String(message?.payload?.compute_status || message?.payload?.materialization || "").toLowerCase();
        const itemCount = Array.isArray(message?.payload?.page?.items) ? message.payload.page.items.length : 0;
        const terminal =
          messageType === "terminal" ||
          ["failed", "computed", "cached"].includes(String(message?.payload?.materialization || "").toLowerCase()) ||
          ["completed", "failed", "killed"].includes(payloadStatus);
        logPageWatchActivity({
          phase: terminal ? "finish" : "update",
          final: terminal,
          variable: subscription.variable,
          path: subscription.path || "/",
          offset: subscription.offset,
          limit: subscription.limit,
          status: payloadStatus || messageType,
          detail: `items=${itemCount}`,
        });
        logComputeActivity({
          type: "page.ws",
          phase: "event",
          summary: terminal
            ? `Live page update completed for ${activityTargetLabel({
                variable: subscription.variable,
                path: subscription.path || "/",
              })}`
            : `Live page update for ${activityTargetLabel({
                variable: subscription.variable,
                path: subscription.path || "/",
              })}`,
          variable: subscription.variable,
          path: subscription.path,
          status: messageType,
          materialization: String(message?.payload?.materialization || ""),
          detail: `items=${Array.isArray(message?.payload?.page?.items) ? message.payload.page.items.length : 0}`,
          source: "record-page",
        });
        if (messageType === "terminal") {
          stopRecordPageSocket(baseKey);
        }
      } catch {
        // ignore malformed ws messages
      }
    };
    socket.onclose = () => {
      if (destroyed) return;
      const nextSockets = { ...recordPageSockets };
      delete nextSockets[baseKey];
      recordPageSockets = nextSockets;
      if (!recordPageSubscriptions?.[baseKey]) return;
      const nextAttempt = Math.max(0, Number(recordPageSocketAttempts?.[baseKey] || 0)) + 1;
      recordPageSocketAttempts = {
        ...recordPageSocketAttempts,
        [baseKey]: nextAttempt,
      };
      const delay = wsReconnectDelayMs(nextAttempt);
      const timer = setTimeout(() => {
        const current = recordPageSubscriptions?.[baseKey];
        if (!current) return;
        ensureRecordPageSocket(record, current);
      }, delay);
      recordPageSocketReconnectTimers = {
        ...recordPageSocketReconnectTimers,
        [baseKey]: timer,
      };
    };
    socket.onerror = () => {
      if (destroyed) return;
      try {
        socket.close();
      } catch {
        // ignore
      }
    };
    return true;
  };

  const cacheRecordPage = (record, path = "", page = null, sourceVariable = "") => {
    if (!record || !page || typeof page !== "object") return null;
    const rawItems = Array.isArray(page?.items) ? page.items : null;
    if (!rawItems) return null;
    const resolvedPath = String(path || recordPath(record) || "/");
    const safeOffset = Math.max(0, Number(page?.offset || 0));
    const safeLimit = Math.max(1, Number(page?.limit || COLLECTION_PAGE_SIZE));
    const variableName = String(sourceVariable || sourceVariableForRecord(record, 0) || "").trim();
    const priorPage = pageForRecord(record, resolvedPath);
    const priorItemsByPath = new Map(
      Array.isArray(priorPage?.items) ? priorPage.items.map((item) => [String(item?.path || ""), item]) : [],
    );
    const normalizedPage = {
      ...page,
      offset: safeOffset,
      limit: safeLimit,
      items: rawItems.map((item) => {
        const itemPath = String(item?.path || "");
        const merged = mergeCollectionItemRecords(priorItemsByPath.get(itemPath) || null, item);
        return mergePathRecordIntoPageItem(merged, variableName);
      }),
    };
    const baseKey = pageKey(record, resolvedPath);
    const cacheKey = pageCacheKey(record, resolvedPath, safeOffset, safeLimit);
    recordPages = {
      ...recordPages,
      [cacheKey]: normalizedPage,
    };
    recordPagePointers = {
      ...recordPagePointers,
      [baseKey]: cacheKey,
    };
    recordPageSources = {
      ...recordPageSources,
      [cacheKey]: variableName,
    };
    return normalizedPage;
  };

  const syncPathRecordIntoCachedPages = (sourceVariable = "", path = "", payload = null) => {
    const variableName = String(sourceVariable || "").trim();
    const targetPath = String(path || "").trim();
    if (!variableName || !targetPath || !payload || typeof payload !== "object") return;
    const nextPages = {};
    let changed = false;
    for (const [cacheKey, page] of Object.entries(recordPages || {})) {
      if (String(recordPageSources?.[cacheKey] || "").trim() !== variableName) {
        nextPages[cacheKey] = page;
        continue;
      }
      const items = Array.isArray(page?.items) ? page.items : null;
      if (!items?.length) {
        nextPages[cacheKey] = page;
        continue;
      }
      let pageChanged = false;
      const nextItems = items.map((item) => {
        if (String(item?.path || "") !== targetPath) return item;
        pageChanged = true;
        const nextState = collectionItemStateFromPayload(payload);
        return mergeCollectionItemRecords(item, {
          ...item,
          descriptor: payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : item?.descriptor,
          state: nextState,
          status: collectionItemStatusFromState(nextState),
          error: String(payload?.error || item?.error || ""),
          blocked_on: String(payload?.blocked_on || item?.blocked_on || ""),
          state_reason: String(payload?.state_reason || item?.state_reason || ""),
        });
      });
      nextPages[cacheKey] = pageChanged ? { ...page, items: nextItems } : page;
      changed = changed || pageChanged;
    }
    if (changed) {
      recordPages = nextPages;
    }
  };

  const syncSelectionForRecordPage = (record, resolvedPath = "", page = null) => {
    const items = collectionItemsForPage(page, recordType(record));
    const currentSelection = collectionSelectionFor(record, resolvedPath);
    let selectedIndex = Math.max(0, Number(currentSelection?.selectedIndex || 0));
    let selectedAbsoluteIndex = Math.max(0, Number(currentSelection?.selectedAbsoluteIndex || 0));
    let selectedPath = String(currentSelection?.selectedPath || "");
    const resolvedOffset = Math.max(0, Number(page?.offset || 0));
    if (!items.length) {
      selectedIndex = 0;
      selectedAbsoluteIndex = 0;
      selectedPath = "";
    } else {
      const byAbsolute = selectedAbsoluteIndex >= resolvedOffset ? selectedAbsoluteIndex - resolvedOffset : -1;
      if (byAbsolute >= 0 && byAbsolute < items.length) {
        selectedIndex = byAbsolute;
        selectedAbsoluteIndex = resolvedOffset + byAbsolute;
      } else {
        const byPathIndex = selectedPath ? items.findIndex((item) => String(item?.path || "") === selectedPath) : -1;
        if (byPathIndex >= 0) {
          selectedIndex = byPathIndex;
          selectedAbsoluteIndex = resolvedOffset + byPathIndex;
        } else if (selectedIndex >= items.length) {
          selectedIndex = 0;
          selectedAbsoluteIndex = resolvedOffset;
        } else {
          selectedAbsoluteIndex = resolvedOffset + selectedIndex;
        }
      }
      if (selectedIndex >= items.length) {
        const byAbsoluteIndex = selectedAbsoluteIndex >= resolvedOffset ? selectedAbsoluteIndex - resolvedOffset : -1;
        selectedIndex = byAbsoluteIndex >= 0 && byAbsoluteIndex < items.length ? byAbsoluteIndex : 0;
      }
      selectedPath = String(items[selectedIndex]?.path || "");
    }
    setCollectionSelection(record, resolvedPath, { selectedIndex, selectedAbsoluteIndex, selectedPath });
  };

  const applyRecordPagePayload = (
    record,
    { path = "", offset = 0, limit = COLLECTION_PAGE_SIZE, payload = null, sourceVariable = "" } = {},
  ) => {
    if (!record || !payload) return null;
    const resolvedPath = String(path || recordPath(record) || "");
    const baseKey = pageKey(record, resolvedPath);
    const cacheKey = pageCacheKey(record, resolvedPath, offset, limit);
    const descriptor = recordDescriptor(record);
    const variableName = String(sourceVariable || sourceVariableForRecord(record, 0) || "").trim();
    const payloadFailed =
      String(payload?.materialization || "").toLowerCase() === "failed" ||
      ["failed", "killed"].includes(String(payload?.compute_status || "").toLowerCase());
    if (payloadFailed) {
      const details = buildFailureDetailsText(payload, {
        nodeId: String(payload?.node_id || record?.node_id || ""),
        path: resolvedPath,
      });
      statusValue = "failed";
      statusText = String(payload?.error || "Value resolution failed.");
      errorText = details;
      stopRecordPageSocket(baseKey);
      const fallbackPage =
        recordPages?.[cacheKey] || fallbackCollectionPage(descriptor, resolvedPath, offset, limit, "failed");
      if (fallbackPage) {
        cacheRecordPage(record, resolvedPath, fallbackPage, variableName);
        syncSelectionForRecordPage(record, resolvedPath, fallbackPage);
        const nextErrors = { ...recordPagesErrors };
        delete nextErrors[baseKey];
        recordPagesErrors = nextErrors;
      } else {
        recordPagesErrors = {
          ...recordPagesErrors,
          [baseKey]: String(details || payload?.error || "Unable to load collection values."),
        };
      }
      return fallbackPage;
    }
    const page =
      payload?.page && typeof payload.page === "object"
        ? payload.page
        : { offset, limit, items: [], has_more: false, next_offset: null };
    const incomingItems = Array.isArray(page?.items) ? page.items : [];
    const payloadMaterialization = String(payload?.materialization || "").toLowerCase();
    const payloadStatus = String(payload?.compute_status || "").toLowerCase();
    const keepPrevious =
      Boolean(recordPages?.[cacheKey]) &&
      ["pending", "missing", "queued", "running", "persisting"].includes(payloadMaterialization || payloadStatus) &&
      incomingItems.length === 0;
    const effectivePage = keepPrevious ? recordPages[cacheKey] : page;
    const cachedPage = cacheRecordPage(record, resolvedPath, effectivePage, variableName);
    const nextErrors = { ...recordPagesErrors };
    delete nextErrors[baseKey];
    recordPagesErrors = nextErrors;
    syncSelectionForRecordPage(record, resolvedPath, cachedPage);
    const hasPendingItems = collectionItemsForPage(cachedPage, recordType(record)).some((item) =>
      ACTIVE_COLLECTION_ITEM_STATES.has(normalizeCollectionItemState(item)),
    );
    const pagePending =
      ["pending", "missing"].includes(payloadMaterialization) ||
      ["queued", "running", "persisting"].includes(payloadStatus);
    if (!hasPendingItems && !pagePending) {
      clearRecordPagePoll(baseKey);
      stopRecordPageSocket(baseKey);
    }
    return cachedPage;
  };

  const collectionSelectionFor = (record, path = "") => {
    const key = pageKey(record, path);
    const selection = collectionSelections?.[key];
    if (selection && typeof selection === "object") {
      return {
        selectedIndex: Math.max(0, Number(selection.selectedIndex || 0)),
        selectedAbsoluteIndex: Math.max(0, Number(selection.selectedAbsoluteIndex || 0)),
        selectedPath: String(selection.selectedPath || ""),
      };
    }
    return { selectedIndex: 0, selectedAbsoluteIndex: 0, selectedPath: "" };
  };

  const setCollectionSelection = (record, path = "", selection = {}) => {
    const key = pageKey(record, path);
    const nextIndex = Math.max(0, Number(selection?.selectedIndex || 0));
    const nextAbsoluteIndex = Math.max(0, Number(selection?.selectedAbsoluteIndex || selection?.selectedIndex || 0));
    const nextPath = String(selection?.selectedPath || "");
    const current = collectionSelections?.[key];
    if (
      current &&
      Number(current.selectedIndex || 0) === nextIndex &&
      Number(current.selectedAbsoluteIndex || 0) === nextAbsoluteIndex &&
      String(current.selectedPath || "") === nextPath
    ) {
      return;
    }
    collectionSelections = {
      ...collectionSelections,
      [key]: {
        selectedIndex: nextIndex,
        selectedAbsoluteIndex: nextAbsoluteIndex,
        selectedPath: nextPath,
      },
    };
  };

  const collectionItemsForPage = (page, voxType = "sequence") => {
    const items = Array.isArray(page?.items) ? page.items : [];
    if (String(voxType || "").toLowerCase() === "mapping") {
      return [...items].sort((left, right) => String(left?.label || "").localeCompare(String(right?.label || "")));
    }
    return items;
  };

  const nestedRecordFromItem = (parentRecord, item) => ({
    ...parentRecord,
    node_id: String(item?.node_id || parentRecord?.node_id || ""),
    path: String(item?.path || parentRecord?.path || ""),
    status: String(item?.status || parentRecord?.status || ""),
    descriptor:
      item?.descriptor && typeof item.descriptor === "object"
        ? item.descriptor
        : {
            vox_type: "unavailable",
            summary: {
              reason: "Value preview unavailable.",
            },
            navigation: {
              path: String(item?.path || parentRecord?.path || ""),
              pageable: false,
              can_descend: false,
              default_page_size: COLLECTION_PAGE_SIZE,
              max_page_size: 256,
            },
          },
  });

  const sourceVariableForRecord = (record, index = 0) => {
    const nodeId = String(record?.node_id || "");
    if (nodeId) {
      const names = symbolNamesByNodeId()[nodeId] || [];
      const selected = names.find((name) => selectedVisualSymbols.includes(name));
      if (selected) return selected;
      if (names[0]) return names[0];
    }
    if (selectedVisualSymbols[index]) return selectedVisualSymbols[index];
    return primaryVariable || "";
  };

  const recordCardState = (record, index = 0) => {
    const sourceName = sourceVariableForRecord(record, index);
    const symbolState = statusLabel(sourceName);
    if (["queued", "running", "persisting"].includes(symbolState)) return "computing";
    const recordState = String(record?.status || "").toLowerCase();
    if (["queued", "running", "persisting", "pending", "missing"].includes(recordState)) return "computing";
    if (collectionRecord(record)) {
      const path = recordPath(record);
      if (pageLoadingForRecord(record, path) || pagePollingForRecord(record, path)) {
        return "computing";
      }
    }
    if (symbolState === "failed") return "failed";
    if (symbolState === "computed") return "computed";
    return "idle";
  };

  const recordJustMaterialized = (record) => Boolean(recentlyMaterialized?.[String(record?.node_id || "")]);

  const pathRecordKey = (sourceVariable = "", path = "") => `${String(sourceVariable || "")}:${String(path || "/")}`;

  const clearPathRecordCacheForVariable = (sourceVariable = "") => {
    const variableName = String(sourceVariable || "").trim();
    if (!variableName) return;
    const prefix = `${variableName}:`;

    for (const [key, timer] of Object.entries(pathRecordPollTimers || {})) {
      if (!String(key || "").startsWith(prefix)) continue;
      clearTimeout(timer);
    }

    pathRecords = Object.fromEntries(
      Object.entries(pathRecords || {}).filter(([key]) => !String(key || "").startsWith(prefix)),
    );
    pathRecordsLoading = Object.fromEntries(
      Object.entries(pathRecordsLoading || {}).filter(([key]) => !String(key || "").startsWith(prefix)),
    );
    pathRecordsErrors = Object.fromEntries(
      Object.entries(pathRecordsErrors || {}).filter(([key]) => !String(key || "").startsWith(prefix)),
    );
    pathRecordPollTimers = Object.fromEntries(
      Object.entries(pathRecordPollTimers || {}).filter(([key]) => !String(key || "").startsWith(prefix)),
    );
  };

  const clearRecordPageCacheForVariable = (sourceVariable = "") => {
    const variableName = String(sourceVariable || "").trim();
    if (!variableName) return;

    const cacheKeysToDrop = new Set(
      Object.entries(recordPageSources || {})
        .filter(([, source]) => String(source || "").trim() === variableName)
        .map(([cacheKey]) => cacheKey),
    );
    const baseKeysToDrop = new Set(
      [...cacheKeysToDrop]
        .map((cacheKey) => {
          const marker = String(cacheKey || "").indexOf("@");
          return marker >= 0 ? String(cacheKey || "").slice(0, marker) : "";
        })
        .filter(Boolean),
    );

    for (const [baseKey, subscription] of Object.entries(recordPageSubscriptions || {})) {
      if (String(subscription?.variable || "").trim() === variableName) {
        baseKeysToDrop.add(String(baseKey || ""));
      }
    }

    for (const baseKey of baseKeysToDrop) {
      clearRecordPagePoll(baseKey);
      stopRecordPageSocket(baseKey, { logFinal: false });
    }

    recordPages = Object.fromEntries(
      Object.entries(recordPages || {}).filter(([cacheKey]) => !cacheKeysToDrop.has(String(cacheKey || ""))),
    );
    recordPagesLoading = Object.fromEntries(
      Object.entries(recordPagesLoading || {}).filter(([cacheKey]) => !cacheKeysToDrop.has(String(cacheKey || ""))),
    );
    recordPageSources = Object.fromEntries(
      Object.entries(recordPageSources || {}).filter(([cacheKey]) => !cacheKeysToDrop.has(String(cacheKey || ""))),
    );
    recordPagePointers = Object.fromEntries(
      Object.entries(recordPagePointers || {}).filter(([baseKey]) => !baseKeysToDrop.has(String(baseKey || ""))),
    );
    recordPagesErrors = Object.fromEntries(
      Object.entries(recordPagesErrors || {}).filter(([baseKey]) => !baseKeysToDrop.has(String(baseKey || ""))),
    );
    collectionSelections = Object.fromEntries(
      Object.entries(collectionSelections || {}).filter(([baseKey]) => !baseKeysToDrop.has(String(baseKey || ""))),
    );
  };

  const clearNestedCachesForVariable = (sourceVariable = "") => {
    const variableName = String(sourceVariable || "").trim();
    if (!variableName) return;
    clearPathRecordCacheForVariable(variableName);
    clearRecordPageCacheForVariable(variableName);
    expandedCollectionStages = Object.fromEntries(
      Object.entries(expandedCollectionStages || {}).filter(
        ([key]) => !String(key || "").startsWith(`${variableName}:`),
      ),
    );
  };

  const refreshVariableNestedCaches = (sourceVariable = "", nextRecord = null) => {
    const variableName = String(sourceVariable || "").trim();
    if (!variableName || !nextRecord || typeof nextRecord !== "object") return;
    const previousNodeId = String(materializedRecords?.[variableName]?.node_id || "").trim();
    const nextNodeId = String(nextRecord?.node_id || "").trim();
    if (!previousNodeId || !nextNodeId || previousNodeId === nextNodeId) return;
    clearNestedCachesForVariable(variableName);
  };

  const pathRecordFor = (sourceVariable = "", path = "") => pathRecords?.[pathRecordKey(sourceVariable, path)] || null;

  const pathRecordLoadingFor = (sourceVariable = "", path = "") => Boolean(pathRecordsLoading?.[pathRecordKey(sourceVariable, path)]);

  const pathRecordErrorFor = (sourceVariable = "", path = "") => String(pathRecordsErrors?.[pathRecordKey(sourceVariable, path)] || "");

  const pathRecordPollingFor = (sourceVariable = "", path = "") => Boolean(pathRecordPollTimers?.[pathRecordKey(sourceVariable, path)]);

  const shouldReuseCachedPathRecord = (payload = null, { enqueueFallback = true } = {}) => {
    if (!payload || typeof payload !== "object") return false;
    const failed =
      String(payload?.materialization || "").trim().toLowerCase() === "failed" ||
      ["failed", "killed"].includes(String(payload?.compute_status || "").trim().toLowerCase());
    if (failed) return true;
    if (hasConcreteDescriptor(payload?.descriptor)) return true;
    if (!enqueueFallback) return true;
    return false;
  };

  const cachePathRecord = (sourceVariable = "", path = "", payload = null) => {
    if (!sourceVariable || !payload) return;
    const key = pathRecordKey(sourceVariable, path);
    pathRecords = {
      ...pathRecords,
      [key]: payload,
    };
    syncPathRecordIntoCachedPages(sourceVariable, String(payload?.path || path || "/"), payload);
    const inlinePage =
      payload?.runtime_preview_page && typeof payload.runtime_preview_page === "object"
        ? payload.runtime_preview_page
        : payload?.page && typeof payload.page === "object"
          ? payload.page
          : null;
    if (inlinePage) {
      cacheRecordPage(payload, String(payload?.path || path || "/"), inlinePage, sourceVariable);
    }
  };

  const cacheInlinePreviewPageForRecord = (record = null, sourceVariable = "", path = "") => {
    if (!record || typeof record !== "object") return;
    const inlinePage =
      record?.runtime_preview_page && typeof record.runtime_preview_page === "object"
        ? record.runtime_preview_page
        : record?.page && typeof record.page === "object"
          ? record.page
          : null;
    if (!inlinePage) return;
    cacheRecordPage(record, String(path || record?.path || "/"), inlinePage, sourceVariable);
  };

  const clearPathRecordPoll = (key = "") => {
    const pollKey = String(key || "");
    const timer = pathRecordPollTimers?.[pollKey];
    if (timer) {
      clearTimeout(timer);
    }
    const next = { ...pathRecordPollTimers };
    delete next[pollKey];
    pathRecordPollTimers = next;
  };

  const schedulePathRecordPoll = ({ sourceVariable = "", path = "", delayMs = 850 } = {}) => {
    if (destroyed) return;
    const variableName = String(sourceVariable || "").trim();
    const targetPath = String(path || "");
    if (!variableName) return;
    const key = pathRecordKey(variableName, targetPath);
    if (pathRecordPollTimers?.[key]) return;
    const timer = setTimeout(() => {
      if (destroyed) return;
      clearPathRecordPoll(key);
      void loadPathRecord({
        sourceVariable: variableName,
        path: targetPath,
        enqueueFallback: false,
        force: true,
      });
    }, Math.max(250, Number(delayMs || 850)));
    pathRecordPollTimers = {
      ...pathRecordPollTimers,
      [key]: timer,
    };
  };

  const loadPathRecord = async ({ sourceVariable = "", path = "", enqueueFallback = true, force = false } = {}) => {
    if (destroyed) return null;
    const variableName = String(sourceVariable || "").trim();
    const targetPath = String(path || "");
    if (!variableName) return null;
    const key = pathRecordKey(variableName, targetPath);
    if (!force && pathRecords?.[key] && shouldReuseCachedPathRecord(pathRecords[key], { enqueueFallback })) {
      logPathLoadActivity({
        phase: "finish",
        final: true,
        variable: variableName,
        path: targetPath || "/",
        status: "completed",
        detail: "cache hit",
      });
      logComputeActivity({
        type: "value.cache",
        phase: "event",
        summary: `Cached value ${activityTargetLabel({ variable: variableName, path: targetPath || "/" })}`,
        variable: variableName,
        path: targetPath,
        status: String(pathRecords?.[key]?.compute_status || ""),
        materialization: String(pathRecords?.[key]?.materialization || ""),
        source: "path-record",
      });
      return pathRecords[key];
    }
    if (pathRecordsLoading?.[key]) return null;
    clearPathRecordPoll(key);

    pathRecordsLoading = { ...pathRecordsLoading, [key]: true };
    logPathLoadActivity({
      phase: "start",
      variable: variableName,
      path: targetPath || "/",
      status: "running",
      detail: enqueueFallback ? "loading nested value" : "refresh nested value",
    });
    const nextErrors = { ...pathRecordsErrors };
    delete nextErrors[key];
    pathRecordsErrors = nextErrors;

    const tryResolve = async (enqueueFlag) =>
      resolvePlaygroundValue({
        program: programText,
        variable: variableName,
        path: targetPath,
        enqueue: enqueueFlag,
        interaction: buildInteractionContext({
          intent: enqueueFlag ? "path-open" : "live-watch",
          source: enqueueFlag ? "viewer" : "poll",
          variable: variableName,
          path: targetPath,
          direct: Boolean(enqueueFlag),
          visible: true,
        }),
      });

    try {
      const first = await tryResolve(false);
      if (destroyed) return null;
      const firstMaterialization = String(first?.materialization || "").toLowerCase();
      const firstStatus = String(first?.compute_status || "").toLowerCase();
      const firstFailed = firstMaterialization === "failed" || ["failed", "killed"].includes(firstStatus);
      const firstConcrete = hasConcreteDescriptor(first?.descriptor);
      const firstMaterialized = (firstMaterialization === "computed" || firstMaterialization === "cached") && Boolean(first?.descriptor);
      const firstPending = ["pending", "missing", "queued", "running", "persisting"].includes(firstMaterialization) ||
        ["queued", "running", "persisting"].includes(firstStatus);
      if (firstFailed) {
        cachePathRecord(variableName, targetPath, first);
        logPathLoadActivity({
          phase: "finish",
          final: true,
          variable: variableName,
          path: targetPath || "/",
          status: "failed",
          detail: String(first?.error || "nested value failed"),
        });
        pathRecordsErrors = {
          ...pathRecordsErrors,
          [key]: buildFailureDetailsText(first, {
            nodeId: variableName,
            path: targetPath,
          }),
        };
        clearPathRecordPoll(key);
        return first;
      }
      if (firstConcrete) {
        cachePathRecord(variableName, targetPath, first);
        if (firstPending && ["sequence", "mapping"].includes(String(first?.descriptor?.vox_type || "").toLowerCase())) {
          logPathLoadActivity({
            phase: "update",
            variable: variableName,
            path: targetPath || "/",
            status: firstStatus || firstMaterialization || "running",
            detail: "descriptor ready; waiting on nested items",
          });
          schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
        } else {
          logPathLoadActivity({
            phase: "finish",
            final: true,
            variable: variableName,
            path: targetPath || "/",
            status: "completed",
            detail: `materialization=${firstMaterialization || "computed"}`,
          });
          clearPathRecordPoll(key);
        }
        return first;
      }
      if (firstMaterialized) {
        cachePathRecord(variableName, targetPath, first);
        logPathLoadActivity({
          phase: "finish",
          final: true,
          variable: variableName,
          path: targetPath || "/",
          status: "completed",
          detail: `materialization=${firstMaterialization || "computed"}`,
        });
        clearPathRecordPoll(key);
        return first;
      }

      if (enqueueFallback && ["pending", "missing"].includes(firstMaterialization) && !["failed", "killed"].includes(firstStatus)) {
        const second = await tryResolve(true);
        if (destroyed) return null;
        const secondMaterialization = String(second?.materialization || "").toLowerCase();
        const secondStatus = String(second?.compute_status || "").toLowerCase();
        const secondFailed = secondMaterialization === "failed" || ["failed", "killed"].includes(secondStatus);
        const secondConcrete = hasConcreteDescriptor(second?.descriptor);
        const secondMaterialized = (secondMaterialization === "computed" || secondMaterialization === "cached") && Boolean(second?.descriptor);
        const secondPending = ["pending", "missing", "queued", "running", "persisting"].includes(secondMaterialization) ||
          ["queued", "running", "persisting"].includes(secondStatus);
        if (secondFailed) {
          cachePathRecord(variableName, targetPath, second);
          logPathLoadActivity({
            phase: "finish",
            final: true,
            variable: variableName,
            path: targetPath || "/",
            status: "failed",
            detail: String(second?.error || "nested value failed"),
          });
          pathRecordsErrors = {
            ...pathRecordsErrors,
            [key]: buildFailureDetailsText(second, {
              nodeId: variableName,
              path: targetPath,
            }),
          };
          clearPathRecordPoll(key);
          return second;
        }
        if (secondConcrete) {
          cachePathRecord(variableName, targetPath, second);
          if (
            secondPending &&
            ["sequence", "mapping"].includes(String(second?.descriptor?.vox_type || "").toLowerCase())
          ) {
            logPathLoadActivity({
              phase: "update",
              variable: variableName,
              path: targetPath || "/",
              status: secondStatus || secondMaterialization || "running",
              detail: "descriptor ready; waiting on nested items",
            });
            schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
          } else {
            logPathLoadActivity({
              phase: "finish",
              final: true,
              variable: variableName,
              path: targetPath || "/",
              status: "completed",
              detail: `materialization=${secondMaterialization || "computed"}`,
            });
            clearPathRecordPoll(key);
          }
          return second;
        }
        if (secondMaterialized) {
          cachePathRecord(variableName, targetPath, second);
          logPathLoadActivity({
            phase: "finish",
            final: true,
            variable: variableName,
            path: targetPath || "/",
            status: "completed",
            detail: `materialization=${secondMaterialization || "computed"}`,
          });
          clearPathRecordPoll(key);
          return second;
        }
        if (secondPending) {
          logPathLoadActivity({
            phase: "update",
            variable: variableName,
            path: targetPath || "/",
            status: secondStatus || secondMaterialization || "running",
            detail: "awaiting nested value",
          });
          schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
          return null;
        }
      }

      if (firstPending) {
        logPathLoadActivity({
          phase: "update",
          variable: variableName,
          path: targetPath || "/",
          status: firstStatus || firstMaterialization || "running",
          detail: "awaiting nested value",
        });
        schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
        return null;
      }

      if (first?.descriptor && !["pending", "missing"].includes(firstMaterialization)) {
        cachePathRecord(variableName, targetPath, first);
        logPathLoadActivity({
          phase: "finish",
          final: true,
          variable: variableName,
          path: targetPath || "/",
          status: "completed",
          detail: `materialization=${firstMaterialization || "computed"}`,
        });
        clearPathRecordPoll(key);
        return first;
      }
      logPathLoadActivity({
        phase: "update",
        variable: variableName,
        path: targetPath || "/",
        status: "running",
        detail: "waiting for nested value",
      });
      return null;
    } catch (error) {
      if (destroyed) return null;
      if (isTimeoutError(error)) {
        logPathLoadActivity({
          phase: "update",
          variable: variableName,
          path: targetPath || "/",
          status: "running",
          detail: "request timed out; waiting for retry",
        });
        schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 1100 });
        return pathRecords?.[key] || null;
      }
      logPathLoadActivity({
        phase: "finish",
        final: true,
        variable: variableName,
        path: targetPath || "/",
        status: "failed",
        detail: String(error?.message || error || "Unable to load value."),
      });
      pathRecordsErrors = {
        ...pathRecordsErrors,
        [key]: String(error?.message || error || "Unable to load value."),
      };
      return null;
    } finally {
      if (destroyed) return;
      const nextLoading = { ...pathRecordsLoading };
      delete nextLoading[key];
      pathRecordsLoading = nextLoading;
    }
  };

  const resolveViewerRecordsTypes = () => {
    const next = { ...symbolTypeHints };
    for (const record of viewerRecords) {
      const nodeId = String(record?.node_id || "");
      const type = String(record?.descriptor?.vox_type || "").trim();
      if (!nodeId || !type) continue;
      const names = symbolNamesByNodeId()[nodeId] || [];
      for (const name of names) {
        next[name] = type;
      }
    }
    symbolTypeHints = next;
  };

  const clearRecordPagePoll = (baseKey = "") => {
    const key = String(baseKey || "");
    const timer = recordPagePollTimers?.[key];
    if (timer) {
      clearTimeout(timer);
    }
    const next = { ...recordPagePollTimers };
    delete next[key];
    recordPagePollTimers = next;
  };

  const scheduleRecordPagePoll = (
    record,
    { path = "", offset = 0, limit = COLLECTION_PAGE_SIZE, delayMs = 900, sourceVariable = "" } = {},
  ) => {
    if (destroyed) return;
    const resolvedPath = String(path || recordPath(record) || "");
    const baseKey = pageKey(record, resolvedPath);
    if (recordPagePollTimers?.[baseKey]) return;
    const timer = setTimeout(() => {
      if (destroyed) return;
      clearRecordPagePoll(baseKey);
      void loadRecordPage(record, {
        path: resolvedPath,
        offset,
        limit,
        sourceVariable,
        enqueueFallback: false,
        force: true,
      });
    }, Math.max(250, Number(delayMs || 900)));
    recordPagePollTimers = {
      ...recordPagePollTimers,
      [baseKey]: timer,
    };
  };

  const loadRecordPage = async (
    record,
    { path = "", offset = 0, limit = COLLECTION_PAGE_SIZE, enqueueFallback = true, force = false, sourceVariable = "" } = {},
  ) => {
    if (destroyed) return null;
    if (!record || !collectionRecord(record)) return null;
    const descriptor = recordDescriptor(record);
    const navigation = descriptor?.navigation && typeof descriptor.navigation === "object" ? descriptor.navigation : {};
    if (!navigation.pageable) return null;
    const resolvedPath = String(path || navigation.path || record.path || "");
    const baseKey = pageKey(record, resolvedPath);
    const resolvedLimit = Math.max(1, Number(limit || navigation.default_page_size || COLLECTION_PAGE_SIZE));
    const resolvedOffset = Math.max(0, Number(offset || 0));
    const variableName = String(sourceVariable || sourceVariableForRecord(record, 0) || "").trim();
    const cacheKey = pageCacheKey(record, resolvedPath, resolvedOffset, resolvedLimit);
    if (!force && recordPages?.[cacheKey]) {
      recordPagePointers = {
        ...recordPagePointers,
        [baseKey]: cacheKey,
      };
      logPageLoadActivity({
        phase: "finish",
        final: true,
        variable: variableName,
        path: resolvedPath || "/",
        offset: resolvedOffset,
        limit: resolvedLimit,
        status: "completed",
        detail: "cache hit",
      });
      logComputeActivity({
        type: "page.cache",
        phase: "event",
        summary: `Cached page ${activityTargetLabel({ variable: variableName, path: resolvedPath || "/" })}`,
        variable: variableName,
        path: resolvedPath,
        status: "cached",
        source: "record-page",
      });
      return recordPages[cacheKey];
    }
    if (recordPagesLoading?.[cacheKey]) return null;
    recordPagesLoading = { ...recordPagesLoading, [cacheKey]: true };
    logPageLoadActivity({
      phase: "start",
      variable: variableName,
      path: resolvedPath || "/",
      offset: resolvedOffset,
      limit: resolvedLimit,
      status: "running",
      detail: enqueueFallback ? "loading collection page" : "refreshing collection page",
    });
    const nextErrors = { ...recordPagesErrors };
    delete nextErrors[baseKey];
    recordPagesErrors = nextErrors;
    try {
      const requestPage = async (enqueueFlag) =>
        resolvePlaygroundValuePage({
          program: programText,
          nodeId: variableName ? "" : String(record?.node_id || ""),
          variable: variableName,
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          enqueue: enqueueFlag,
          interaction: buildInteractionContext({
            intent: enqueueFlag ? "page-nav" : "live-watch",
            source: enqueueFlag ? "viewer" : "poll",
            variable: variableName,
            path: resolvedPath,
            direct: Boolean(enqueueFlag),
            visible: true,
          }),
        });

      let payload = await requestPage(false);
      if (destroyed) return null;
      const pendingStatuses = new Set(["queued", "running", "persisting", "pending", "missing"]);
      const payloadMaterialization = String(payload?.materialization || "").toLowerCase();
      const payloadStatus = String(payload?.compute_status || "").toLowerCase();
      const expectedLength = Number(descriptor?.summary?.length || 0);
      const firstPage =
        payload?.page && typeof payload.page === "object"
          ? payload.page
          : { offset: resolvedOffset, limit: resolvedLimit, items: [], has_more: false, next_offset: null };
      const likelyPending = pendingStatuses.has(payloadMaterialization) || pendingStatuses.has(payloadStatus);
      const needsFallback = !Array.isArray(firstPage?.items) || firstPage.items.length === 0;

      if (enqueueFallback && needsFallback && (likelyPending || expectedLength > 0)) {
        payload = await requestPage(true);
        if (destroyed) return null;
      }

      const effectivePage = applyRecordPagePayload(record, {
        path: resolvedPath,
        offset: resolvedOffset,
        limit: resolvedLimit,
        payload,
        sourceVariable: variableName,
      });
      const effectiveItems = collectionItemsForPage(effectivePage, recordType(record));
      const pagePending =
        pendingStatuses.has(String(payload?.materialization || "").toLowerCase()) ||
        pendingStatuses.has(String(payload?.compute_status || "").toLowerCase());
      const hasPendingItems = effectiveItems.some((item) => ACTIVE_COLLECTION_ITEM_STATES.has(normalizeCollectionItemState(item)));
      if (hasPendingItems || (pagePending && (!Array.isArray(effectivePage?.items) || effectivePage.items.length === 0))) {
        logPageLoadActivity({
          phase: "update",
          variable: variableName,
          path: resolvedPath || "/",
          offset: resolvedOffset,
          limit: resolvedLimit,
          status: payloadStatus || payloadMaterialization || "running",
          detail: `items=${effectiveItems.length} · pending-items=${effectiveItems.filter((item) => ACTIVE_COLLECTION_ITEM_STATES.has(normalizeCollectionItemState(item))).length}`,
        });
        ensureRecordPageSocket(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          sourceVariable: variableName,
          enqueue: false,
        });
        scheduleRecordPagePoll(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          sourceVariable: variableName,
          delayMs: 950,
        });
      } else {
        logPageLoadActivity({
          phase: "finish",
          final: true,
          variable: variableName,
          path: resolvedPath || "/",
          offset: resolvedOffset,
          limit: resolvedLimit,
          status: "completed",
          detail: `items=${effectiveItems.length}`,
        });
        clearRecordPagePoll(baseKey);
        stopRecordPageSocket(baseKey);
      }
      return effectivePage;
    } catch (error) {
      if (destroyed) return null;
      if (isTimeoutError(error)) {
        logPageLoadActivity({
          phase: "update",
          variable: variableName,
          path: resolvedPath || "/",
          offset: resolvedOffset,
          limit: resolvedLimit,
          status: "running",
          detail: "page request timed out; waiting for retry",
        });
        ensureRecordPageSocket(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          sourceVariable: variableName,
          enqueue: false,
        });
        scheduleRecordPagePoll(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          sourceVariable: variableName,
          delayMs: 1100,
        });
        const cached = recordPages?.[cacheKey] || null;
        if (cached) {
          recordPagePointers = { ...recordPagePointers, [baseKey]: cacheKey };
        }
        return cached;
      }
      if (isUnknownNodeSelectionError(error) && variableName) {
        logPageLoadActivity({
          phase: "update",
          variable: variableName,
          path: resolvedPath || "/",
          offset: resolvedOffset,
          limit: resolvedLimit,
          status: "running",
          detail: "selection not ready yet; retry scheduled",
        });
        scheduleRecordPagePoll(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          sourceVariable: variableName,
          delayMs: 520,
        });
        return recordPages?.[cacheKey] || null;
      }
      logPageLoadActivity({
        phase: "finish",
        final: true,
        variable: variableName,
        path: resolvedPath || "/",
        offset: resolvedOffset,
        limit: resolvedLimit,
        status: "failed",
        detail: String(error?.message || error || "Unable to load collection values."),
      });
      recordPagesErrors = {
        ...recordPagesErrors,
        [baseKey]: String(error?.message || error || "Unable to load collection values."),
      };
      return null;
    } finally {
      if (destroyed) return;
      const nextLoading = { ...recordPagesLoading };
      delete nextLoading[cacheKey];
      recordPagesLoading = nextLoading;
    }
  };

  const loadCollectionPrev = async (record, path = "", sourceVariable = "") => {
    const page = pageForRecord(record, path);
    if (!page) return null;
    const offset = Math.max(0, Number(page.offset || 0));
    const limit = Math.max(1, Number(page.limit || COLLECTION_PAGE_SIZE));
    if (offset <= 0) return page;
    return loadRecordPage(record, {
      path,
      offset: Math.max(0, offset - limit),
      limit,
      sourceVariable,
    });
  };

  const loadCollectionNext = async (record, path = "", sourceVariable = "") => {
    const page = pageForRecord(record, path);
    if (!page || page.next_offset === null || page.next_offset === undefined) return page;
    const limit = Math.max(1, Number(page.limit || COLLECTION_PAGE_SIZE));
    return loadRecordPage(record, {
      path,
      offset: Math.max(0, Number(page.next_offset || 0)),
      limit,
      sourceVariable,
    });
  };

  const ensureRecordPages = () => {
    for (const [index, record] of viewerRecords.entries()) {
      if (!collectionRecord(record)) continue;
      const path = recordPath(record);
      if (pageForRecord(record, path) || pageLoadingForRecord(record, path)) continue;
      void loadRecordPage(record, {
        path,
        offset: 0,
        limit: COLLECTION_PAGE_SIZE,
        sourceVariable: sourceVariableForRecord(record, index),
      });
    }
  };

  const previewText = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "value").toLowerCase();
    const summary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
    if (["integer", "number", "boolean", "null"].includes(voxType)) return `${summary.value ?? ""}`;
    if (voxType === "string") return String(summary.value || "");
    if (voxType === "bytes") return `${Number(summary.length || 0)} bytes`;
    if (voxType === "mapping" || voxType === "sequence") {
      const rawLength = summary.length;
      if (rawLength === null || rawLength === undefined || rawLength === "") {
        return "collection";
      }
      const length = Number(rawLength);
      if (Number.isFinite(length) && length >= 0) {
        return `${length} values`;
      }
      return "collection";
    }
    if (voxType === "overlay") return `${Number(summary.layer_count || 0)} layers`;
    if (voxType === "ndarray") return Array.isArray(summary.shape) ? summary.shape.join(" x ") : "array";
    if (voxType === "image2d" || voxType === "volume3d") return Array.isArray(summary.size) ? summary.size.join(" x ") : "image";
    if (summary && typeof summary.reason === "string") return summary.reason;
    return TYPE_LABELS[voxType] || voxType || "value";
  };

  const fallbackCollectionPage = (descriptor, basePath = "", offset = 0, limit = COLLECTION_PAGE_SIZE, status = "pending") => {
    const summary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
    const rawLength = summary.length;
    const totalLength = Number(rawLength);
    if (!Number.isFinite(totalLength) || totalLength < 0) return null;
    const safeOffset = Math.max(0, Number(offset || 0));
    const safeLimit = Math.max(1, Number(limit || COLLECTION_PAGE_SIZE));
    const end = Math.min(totalLength, safeOffset + safeLimit);
    const items = [];
    for (let index = safeOffset; index < end; index += 1) {
      const itemPath = basePath && basePath !== "/" ? `${String(basePath).replace(/\/+$/, "")}/${index}` : `/${index}`;
      items.push({
        index,
        label: `[${index}]`,
        path: itemPath,
        status,
        descriptor: {
          vox_type: "unavailable",
          summary: {
            reason: status === "failed" ? "resolve error" : "not loaded yet",
          },
          navigation: {
            path: itemPath,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      });
    }
    return {
      offset: safeOffset,
      limit: safeLimit,
      items,
      has_more: end < totalLength,
      next_offset: end < totalLength ? end : null,
    };
  };

  const collectDreamIds = (payload = {}) => {
    const ids = [];
    const seen = new Set();
    const add = (value) => {
      const text = String(value || "").trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      ids.push(text);
    };
    add(symbolTable?.[primaryVariable]);
    add(payload?.node_id);
    add(payload?.job_id);
    const arrays = [payload?.ancestor_ids, payload?.ancestor_node_ids, payload?.pending_node_ids, payload?.node_ids];
    for (const list of arrays) {
      if (!Array.isArray(list)) continue;
      for (const item of list) add(item);
    }
    const rows = Array.isArray(pendingLogRows) ? pendingLogRows : [];
    for (const row of rows) add(row?.node_id);
    const logTail = String(payload?.log_tail || "");
    for (const match of logTail.matchAll(/"node_id"\s*:\s*"([^"]+)"/g)) {
      add(match?.[1]);
    }
    return ids.slice(0, 18);
  };

  const activateDream = (payload = {}) => {
    if (pendingDreamCleanup) {
      clearTimeout(pendingDreamCleanup);
      pendingDreamCleanup = null;
    }
    const ids = collectDreamIds(payload);
    showDream(ids.length ? ids : [String(symbolTable?.[primaryVariable] || primaryVariable || "node")]);
  };

  const dissolveDream = () => {
    if (!$dreamState.visible) return;
    storeDissolveDream();
    if (pendingDreamCleanup) clearTimeout(pendingDreamCleanup);
    pendingDreamCleanup = setTimeout(() => {
      if (destroyed) return;
      clearDream();
      pendingDreamCleanup = null;
    }, 1200);
  };

  const ensureViewer = () => {
    if (viewer) return;
    viewer = {
      setLoading: (message) => {
        viewerMode = "loading";
        viewerMessage = String(message || "Loading...");
        viewerErrorMessage = "";
      },
      setError: (message) => {
        viewerMode = "error";
        viewerErrorMessage = String(message || "Viewer error");
        viewerMessage = "";
      },
      renderRecord: (record) => {
        viewerRecords = record ? [record] : [];
        viewerMode = record ? "value" : "empty";
        viewerErrorMessage = "";
        viewerMessage = "";
        resolveViewerRecordsTypes();
        ensureRecordPages();
      },
      renderRecords: (records) => {
        viewerRecords = (Array.isArray(records) ? records : []).filter((item) => !!item);
        viewerMode = viewerRecords.length ? "value" : "empty";
        viewerErrorMessage = "";
        viewerMessage = "";
        resolveViewerRecordsTypes();
        ensureRecordPages();
      },
      destroy: () => {},
    };
    viewerSupportsMultiValue = true;
  };

  const resetViewer = () => {
    for (const timer of Object.values(recordPagePollTimers || {})) {
      clearTimeout(timer);
    }
    stopAllRecordPageSockets();
    for (const timer of Object.values(pathRecordPollTimers || {})) {
      clearTimeout(timer);
    }
    viewerRecords = [];
    viewerMode = "empty";
    viewerMessage = "";
    viewerErrorMessage = "";
    clearStaleValue();
    recordPages = {};
    recordPagePointers = {};
    recordPageSources = {};
    recordPagesLoading = {};
    recordPagesErrors = {};
    collectionSelections = {};
    expandedCollectionStages = {};
    recordPagePollTimers = {};
    recordPageSockets = {};
    recordPageSocketReconnectTimers = {};
    recordPageSocketAttempts = {};
    recordPageSubscriptions = {};
    pathRecords = {};
    pathRecordsLoading = {};
    pathRecordsErrors = {};
    pathRecordPollTimers = {};
    maximizedViewerIndex = -1;
    activeValueSubscription = null;
    stopValueSocket();
  };

  const renderSelectedRecords = ({ fallbackRecord = null } = {}) => {
    ensureViewer();
    const selectedRecords = selectedVisualSymbols.map((name) => materializedRecords?.[name]).filter((row) => !!row);
    if (selectedRecords.length > 1 && viewerSupportsMultiValue && typeof viewer?.renderRecords === "function") {
      viewer.renderRecords(selectedRecords);
      return true;
    }
    const fallback =
      materializedRecords?.[primaryVariable] ||
      selectedRecords[selectedRecords.length - 1] ||
      fallbackRecord ||
      null;
    if (fallback && typeof viewer?.renderRecord === "function") {
      viewer.renderRecord(fallback);
      return true;
    }
    return false;
  };

  const toggleMaximizedViewer = (index = -1) => {
    const next = Number(index);
    if (!Number.isInteger(next) || next < 0) {
      maximizedViewerIndex = -1;
      return;
    }
    maximizedViewerIndex = maximizedViewerIndex === next ? -1 : next;
  };

  const setCollectionStageExpanded = (key, expanded) => {
    const normalizedKey = String(key || "").trim();
    if (!normalizedKey) return;
    if (expanded) {
      expandedCollectionStages = {
        ...expandedCollectionStages,
        [normalizedKey]: true,
      };
      return;
    }
    if (!expandedCollectionStages?.[normalizedKey]) return;
    const next = { ...(expandedCollectionStages || {}) };
    delete next[normalizedKey];
    expandedCollectionStages = next;
  };

  const resetStartStateToDefaults = async () => {
    resolveRequestSeq += 1;
    resolveInFlight = false;
    stopPoll();
    stopProbe();
    clearPendingLogs();
    clearResolutionActivity();
    clearComputeActivity();
    clearPersistedStartState();
    programText = DEFAULT_PROGRAM;
    editorViewState = null;
    showCodePanel = true;
    showResultsPanel = true;
    showOperationsPanel = true;
    showOperationsHelp = false;
    splitRatio = 0.48;
    primaryVariable = "";
    captionVariable = "-";
    currentPath = "";
    selectedVisualSymbols = [];
    maximizedViewerIndex = -1;
    collectionSelections = {};
    recordPagePointers = {};
    expandedCollectionStages = {};
    symbolStatuses = {};
    symbolMaterializations = {};
    materializedRecords = {};
    symbolTypeHints = {};
    editorSymbolTypes = {};
    errorText = "";
    statusValue = "idle";
    statusText = "Write code and run to compute a value.";
    clearStaleValue();
    resetViewer();
    await tick();
    if (startEditorRef && typeof startEditorRef.restoreViewState === "function") {
      startEditorRef.restoreViewState(null);
    }
    await refreshSymbols();
    ensureSelectedVisualSymbols();
  };

  const handlePanicReset = async () => {
    if (typeof window !== "undefined" && typeof window.confirm === "function") {
      const confirmed = window.confirm("Reset the Start tab state and clear its persisted state?");
      if (!confirmed) return;
    }
    await resetStartStateToDefaults();
  };

  const resolveContextMatches = (request = {}) =>
    String(request?.variable || "") === String(primaryVariable || "") &&
    String(request?.path || "") === String(currentPath || "") &&
    String(request?.program || "") === String(programText || "");

  const applyFailure = (payload, variableName) => {
    clearStaleValue();
    const executionErrors = normalizedExecutionErrors(payload);
    const executionErrorEntries = Object.entries(executionErrors || {});
    const primaryExecutionMessage = executionErrorEntries.length ? String(executionErrorEntries[0]?.[1] || "").trim() : "";
    const fallbackMessage = String(payload?.error || "Unable to inspect value.").trim();
    const summaryMessage = primaryExecutionMessage || fallbackMessage || "Unable to inspect value.";
    const executionCount = executionErrorEntries.length;
    const headline =
      executionCount > 0
        ? `Execution failed (${executionCount} ${executionCount === 1 ? "error" : "errors"}): ${summaryMessage}`
        : summaryMessage;
    const detailText = buildFailureDetailsText(payload, {
      nodeId: String(payload?.node_id || symbolTable?.[String(variableName || "")] || ""),
      path: String(payload?.path || currentPath || "/"),
    });
    statusValue = "failed";
    statusText = headline;
    captionVariable = variableName || "-";
    errorText = detailText || headline;
    if (!hasActiveComputation()) {
      dissolveDream();
    }
    setSymbolMaterialization(variableName, "failed");
    if (materializedRecords?.[variableName]) {
      const next = { ...materializedRecords };
      delete next[variableName];
      materializedRecords = next;
    }
    const failureRecord = {
      available: false,
      node_id: payload?.node_id || "",
      status: "failed",
      path: payload?.path || "/",
      error: headline,
      descriptor: {
        vox_type: "string",
        format_version: "voxpod/1",
        summary: { value: headline, length: headline.length, truncated: false },
        navigation: {
          path: payload?.path || "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    };
    const renderedFromSelection = renderSelectedRecords({
      fallbackRecord: selectedVisualSymbols.includes(variableName) ? failureRecord : null,
    });
    if (!renderedFromSelection) {
      viewer.renderRecord(failureRecord);
    }
  };

  const clearPendingLogs = () => {
    pendingLogSummary = "No execution log yet.";
    pendingLogRows = [];
    pendingLogRaw = "";
    pendingLogJobId = "";
  };

  const applyPendingLogs = (payload, state) => {
    const cacheSummary =
      payload?.cache_summary && typeof payload.cache_summary === "object"
        ? payload.cache_summary
        : payload?.diagnostics?.cache_summary && typeof payload.diagnostics.cache_summary === "object"
          ? payload.diagnostics.cache_summary
          : {};
    const { raw, summaryText, rows } = buildExecutionLogRows({
      log_tail: payload?.log_tail || "",
      result: { execution: { cache_summary: cacheSummary } },
    });
    if (raw) {
      pendingLogSummary = summaryText;
    } else if (state === "queued") {
      pendingLogSummary = "Queued: waiting for a value worker slot.";
    } else if (state === "persisting") {
      pendingLogSummary = "Computed: waiting for persistence to finish.";
    } else {
      pendingLogSummary = "Execution started. Waiting for log events...";
    }
    pendingLogRows = rows;
    pendingLogRaw = raw;
    pendingLogJobId = payload?.job_id ? String(payload.job_id) : "";

    const namesByNode = symbolNamesByNodeId();
    for (const entry of [...rows].reverse()) {
      if (!entry || entry.event !== "playground.node") continue;
      const eventNodeId = String(entry.node_id || "");
      const mappedNames = namesByNode[eventNodeId] || [];
      const eventStatus = normalizeStatus(entry.status || state);
      if (mappedNames.length) {
        for (const name of mappedNames) {
          setSymbolStatus(name, eventStatus);
          pushResolutionActivity({
            variableName: name,
            nodeId: eventNodeId,
            operator: String(entry.operator || ""),
            status: eventStatus,
            cacheSource: String(entry.cache_source || ""),
            durationS: Number(entry.duration_s || 0),
          });
        }
      } else {
        pushResolutionActivity({
          variableName: "",
          nodeId: eventNodeId,
          operator: String(entry.operator || ""),
          status: eventStatus,
          cacheSource: String(entry.cache_source || ""),
          durationS: Number(entry.duration_s || 0),
        });
      }
    }
  };

  const applyPending = (payload, variableName) => {
    clearStaleValue();
    const state = String(payload?.compute_status || payload?.materialization || "running");
    traceResolve("pending", {
      variable: variableName,
      state,
      materialization: String(payload?.materialization || ""),
      computeStatus: String(payload?.compute_status || ""),
      jobId: String(payload?.job_id || ""),
      path: String(payload?.path || currentPath || "/"),
    });
    statusValue = "running";
    statusText = `Computing ${variableName} (${state})`;
    captionVariable = variableName || "-";
    errorText = "";
    activateDream(payload);
    setSymbolStatus(variableName, state);
    setSymbolMaterialization(variableName, payload?.materialization || state);
    cacheInlinePreviewPageForRecord(payload, variableName, String(payload?.path || currentPath || "/"));
    applyPendingLogs(payload, state);
    viewer.renderRecord({
      ...payload,
      available: false,
      status: state,
      path: payload?.path || currentPath || "/",
      descriptor:
        payload?.descriptor && typeof payload.descriptor === "object"
          ? payload.descriptor
          : {
              vox_type: "unavailable",
              format_version: "voxpod/1",
              summary: { reason: `status=${state}` },
              navigation: {
                path: payload?.path || currentPath || "/",
                pageable: false,
                can_descend: false,
                default_page_size: 64,
                max_page_size: 512,
              },
            },
    });
  };

  const applyMaterialized = (payload, variableName) => {
    clearStaleValue();
    refreshVariableNestedCaches(variableName, payload);
    cacheInlinePreviewPageForRecord(payload, variableName, String(payload?.path || currentPath || "/"));
    const descriptor = payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : null;
    const materialization = String(payload?.materialization || payload?.status || "materialized");
    traceResolve("materialized", {
      variable: variableName,
      materialization,
      computeStatus: String(payload?.compute_status || ""),
      nodeId: String(payload?.node_id || ""),
      path: String(payload?.path || currentPath || "/"),
      voxType: String(descriptor?.vox_type || ""),
    });
    statusValue = "completed";
    statusText = `Computed ${variableName}`;
    captionVariable = variableName || "-";
    errorText = "";
    setSymbolStatus(variableName, "computed");
    setSymbolMaterialization(variableName, materialization);
    if (descriptor?.vox_type) {
      symbolTypeHints = {
        ...symbolTypeHints,
        [variableName]: String(descriptor.vox_type),
      };
    }
    materializedRecords = {
      ...materializedRecords,
      [variableName]: payload,
    };
    markRecordMaterialized(String(payload?.node_id || ""));
    if (!hasActiveComputation()) {
      dissolveDream();
    }
    if (!selectedVisualSymbols.includes(variableName)) {
      selectedVisualSymbols = [...selectedVisualSymbols, variableName];
    }
    ensureSelectedVisualSymbols();
    renderSelectedRecords({ fallbackRecord: payload });
    pushResolutionActivity({
      variableName,
      nodeId: String(payload?.node_id || ""),
      operator: "result",
      status: "computed",
      cacheSource: materialization === "cached" ? "store" : "runtime",
      durationS: 0,
    });
    clearPendingLogs();
  };

  const resolvePrimaryValue = async ({ enqueue = true, path = "", background = false, interaction = null } = {}) => {
    ensureViewer();
    const traceId = resolveTraceSeq + 1;
    resolveTraceSeq = traceId;
    const rootPathRequest = !String(path || "").trim();
    if (!primaryVariable) {
      traceResolve("skip-no-primary", { traceId, enqueue, path });
      statusValue = "idle";
      statusText = "Define at least one variable to inspect.";
      captionVariable = "-";
      dissolveDream();
      viewer.renderRecord(null);
      return { state: "idle", reason: "no-primary" };
    }
    if (symbolDiagnostics.length) {
      const summary = diagnosticsSummaryText() || "Fix static diagnostics before computing.";
      traceResolve("skip-diagnostics", {
        traceId,
        enqueue,
        path,
        variable: primaryVariable,
        diagnostics: symbolDiagnostics.length,
      });
      markStaleValue(summary);
      statusValue = "idle";
      statusText = staleValueVisible ? "Results are stale until editor diagnostics are fixed." : summary;
      captionVariable = primaryVariable;
      dissolveDream();
      errorText = "";
      return { state: "failed", reason: "diagnostics" };
    }

    if (enqueue) {
      // User-driven resolve takes priority over passive symbol probes.
      probeToken += 1;
      stopProbe();
    }

    currentPath = String(path || "");
    if (enqueue && !background && rootPathRequest) {
      clearNestedCachesForVariable(primaryVariable);
    }
    const request = {
      seq: resolveRequestSeq + 1,
      variable: String(primaryVariable || ""),
      path: String(currentPath || ""),
      program: String(programText || ""),
    };
    resolveRequestSeq = request.seq;
    resolveInFlight = true;
    traceResolve("start", {
      traceId,
      enqueue,
      background,
      variable: request.variable,
      path: currentPath || "/",
      statusBefore: statusValue,
    });
    statusValue = "running";
    statusText = `Computing ${primaryVariable}...`;
    captionVariable = primaryVariable;
    errorText = "";
    setSymbolStatus(primaryVariable, "running");
    const preserveCurrentView = Boolean(background && viewerMode === "value" && viewerRecords.length);
    if (!preserveCurrentView) {
      viewer.setLoading(`Computing ${primaryVariable}${currentPath ? ` @ ${currentPath}` : ""}...`);
    }

    try {
      const requestStarted = performance?.now ? performance.now() : Date.now();
      traceResolve("request-dispatch", {
        traceId,
        enqueue,
        background,
        variable: request.variable,
        path: currentPath || "/",
      });
      const payload = await resolvePlaygroundValue({
        program: request.program,
        variable: request.variable,
        path: currentPath,
        enqueue,
        interaction:
          interaction && typeof interaction === "object"
            ? interaction
            : buildInteractionContext({
                intent: background ? "live-watch" : currentPath ? "path-open" : "primary-refresh",
                source: background ? "poll" : currentPath ? "viewer" : "run",
                variable: request.variable,
                path: currentPath,
                direct: Boolean(enqueue && !background),
                visible: true,
              }),
      });
      if (destroyed) {
        return { state: "stale", reason: "destroyed" };
      }
      if (request.seq !== resolveRequestSeq || !resolveContextMatches(request)) {
        traceResolve("response-stale", {
          traceId,
          enqueue,
          background,
          variable: request.variable,
          path: request.path || "/",
        });
        return { state: "stale", reason: "superseded" };
      }
      const requestElapsedMs = (performance?.now ? performance.now() : Date.now()) - requestStarted;
      traceResolve("response", {
        traceId,
        enqueue,
        background,
        variable: request.variable,
        path: currentPath || "/",
        elapsedMs: Number(requestElapsedMs.toFixed(1)),
        materialization: String(payload?.materialization || ""),
        computeStatus: String(payload?.compute_status || ""),
        nodeId: String(payload?.node_id || ""),
        jobId: String(payload?.job_id || ""),
      });

      const materialization = String(payload?.materialization || "");
      const computeStatus = String(payload?.compute_status || "");
      const descriptor = payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : null;
      const isMaterialized = (materialization === "cached" || materialization === "computed") && !!descriptor;
      const isPending = isPendingLikePayload(payload);

      if (isMaterialized) {
        traceResolve("branch-materialized", {
          traceId,
          variable: request.variable,
          path: currentPath || "/",
          materialization,
          computeStatus,
        });
        activeValueSubscription = null;
        stopValueSocket();
        stopPoll();
        applyMaterialized(payload, request.variable);
        return { state: "computed", reason: "materialized" };
      }

      if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") {
        traceResolve("branch-failed", {
          traceId,
          variable: request.variable,
          path: currentPath || "/",
          materialization,
          computeStatus,
          error: String(payload?.error || ""),
        });
        activeValueSubscription = null;
        stopValueSocket();
        stopPoll();
        setSymbolStatus(request.variable, "failed");
        applyFailure(payload, request.variable);
        return { state: "failed", reason: "materialization-failed" };
      }

      if (isPending) {
        const hasProgressSignal = hasLiveProgressSignal(payload);
        traceResolve("branch-pending", {
          traceId,
          variable: request.variable,
          path: currentPath || "/",
          materialization,
          computeStatus,
          hasProgressSignal,
          pollActive: Boolean(pendingPoll),
        });
        if (!hasProgressSignal) {
          activeValueSubscription = null;
          stopValueSocket();
          stopPoll();
          if (!preserveCurrentView) {
            viewer.setLoading(`Loading ${request.variable}${currentPath ? ` @ ${currentPath}` : ""}...`);
          }
          statusValue = "idle";
          statusText = `${request.variable} is not ready yet. Click Run or click the tag again to refresh.`;
          setSymbolStatus(request.variable, "idle");
          setSymbolMaterialization(
            request.variable,
            materialization === "missing" ? "unresolved" : materialization || "unresolved",
          );
          dissolveDream();
          return { state: "idle", reason: "no-progress" };
        }
        applyPending(payload, request.variable);
        const subscribed = subscribeValueSocket({ variable: request.variable, path: currentPath || "/", enqueue });
        ensurePendingPoll({ traceId, variable: request.variable, path: currentPath || "/" });
        if (!subscribed) {
          traceResolve("pending-no-ws", {
            traceId,
            variable: request.variable,
            path: currentPath || "/",
          });
        }
        return { state: "pending", reason: "in-progress" };
      }

      traceResolve("branch-unexpected", {
        traceId,
        variable: request.variable,
        path: currentPath || "/",
        materialization,
        computeStatus,
      });
      stopPoll();
      activeValueSubscription = null;
      stopValueSocket();
      setSymbolStatus(request.variable, "failed");
      applyFailure(
        {
          ...payload,
          error: payload?.error || "Unexpected materialization state.",
        },
        request.variable,
      );
      return { state: "failed", reason: "unexpected-state" };
    } catch (error) {
      if (destroyed) {
        return { state: "stale", reason: "destroyed" };
      }
      if (request.seq !== resolveRequestSeq || !resolveContextMatches(request)) {
        traceResolve("request-error-stale", {
          traceId,
          enqueue,
          variable: request.variable,
          path: request.path || "/",
          message: String(error?.message || error || "unknown"),
        });
        return { state: "stale", reason: "superseded" };
      }
      traceResolve("request-error", {
        traceId,
        enqueue,
        background,
        variable: request.variable,
        path: currentPath || "/",
        message: String(error?.message || error || "unknown"),
      });
      if (isTimeoutError(error)) {
        applyPending(
          {
            materialization: "pending",
            compute_status: "running",
            path: currentPath || "/",
            log_tail: pendingLogRaw || "",
            job_id: pendingLogJobId || "",
          },
          request.variable,
        );
        subscribeValueSocket({ variable: request.variable, path: currentPath || "/", enqueue });
        ensurePendingPoll({ traceId, variable: request.variable, path: currentPath || "/" });
        return { state: "pending", reason: "request-timeout" };
      }
      const diagnostics = diagnosticsFromError(error);
      if (diagnostics.length) {
        stopPoll();
        activeValueSubscription = null;
        stopValueSocket();
        setSymbolStatus(request.variable, "idle");
        captionVariable = request.variable || "-";
        applyStaticDiagnosticsState(diagnostics, error?.message || "Static diagnostics detected.");
        return { state: "failed", reason: "diagnostics" };
      }
      stopPoll();
      activeValueSubscription = null;
      stopValueSocket();
      setSymbolStatus(request.variable, "failed");
      applyFailure({ error: error.message || "Request failed." }, request.variable);
      return { state: "failed", reason: "request-error" };
    } finally {
      if (request.seq === resolveRequestSeq) {
        resolveInFlight = false;
      }
    }
  };

  const refreshSymbols = async () => {
    if (destroyed) return;
    if (capabilities.playground_symbols === false) {
      probeToken += 1;
      stopProbe();
      symbolTable = {};
      symbolDiagnostics = [];
      symbolStatuses = {};
      symbolMaterializations = {};
      materializedRecords = {};
      symbolTypeHints = {};
      selectedVisualSymbols = [];
      primaryVariable = "";
      captionVariable = "-";
      clearStaleValue();
      statusValue = "failed";
      statusText = "Program symbol API unavailable on this backend.";
      errorText = statusText;
      resetViewer();
      return;
    }

    const token = loadToken + 1;
    loadToken = token;

    const source = String(programText || "").trim();
    if (!source) {
      probeToken += 1;
      stopProbe();
      symbolTable = {};
      symbolDiagnostics = [];
      symbolStatuses = {};
      symbolMaterializations = {};
      materializedRecords = {};
      symbolTypeHints = {};
      selectedVisualSymbols = [];
      primaryVariable = "";
      captionVariable = "-";
      clearStaleValue();
      statusValue = "idle";
      statusText = "Write code and run to compute a value.";
      errorText = "";
      resetViewer();
      return;
    }

    try {
      const previousPrimary = String(primaryVariable || "");
      const previousSelected = normalizeSelectionNames(selectedVisualSymbols);
      const previousSymbolTable = { ...(symbolTable || {}) };
      const previousTypeHints = { ...(symbolTypeHints || {}) };
      const payload = await getProgramSymbols(programText);
      if (destroyed) return;
      if (token !== loadToken) return;
      const nextDiagnostics = Array.isArray(payload?.diagnostics) ? payload.diagnostics : [];
      const nextSymbolTable = payload?.symbol_table && typeof payload.symbol_table === "object" ? payload.symbol_table : {};
      const available = payload?.available !== false;
      const preservePreviousSymbols =
        !available &&
        nextDiagnostics.length > 0 &&
        !Object.keys(nextSymbolTable).length &&
        Object.keys(previousSymbolTable).length > 0;
      symbolTable = available ? nextSymbolTable : preservePreviousSymbols ? previousSymbolTable : {};
      symbolDiagnostics = nextDiagnostics;
      const staticTypeHints =
        available
          ? payload?.symbol_output_kinds || {}
          : preservePreviousSymbols
            ? previousTypeHints
            : {};
      const preservedViewNames = normalizeSelectionNames([...previousSelected, previousPrimary]);
      syncSymbolStatuses(symbolTable);
      syncSymbolMaterializations(symbolTable);
      syncMaterializedRecords(symbolTable, preservedViewNames);
      syncSymbolTypeHints(symbolTable, staticTypeHints, preservedViewNames);

      const nextNames = Object.keys(symbolTable || {});
      const retainedSelected = previousSelected.filter((name) => nextNames.includes(name));
      const preservedSelected = previousSelected.filter((name) => materializedRecords?.[name]);
      selectedVisualSymbols = retainedSelected.length ? retainedSelected : preservedSelected;
      primaryVariable = nextNames.includes(previousPrimary)
        ? previousPrimary
        : previousPrimary && materializedRecords?.[previousPrimary]
          ? previousPrimary
        : inferPrimaryVariable(programText, symbolTable);
      ensureSelectedVisualSymbols({ preserveRenderedSelection: Boolean(preservedSelected.length || materializedRecords?.[previousPrimary]) });
      captionVariable = primaryVariable || "-";
      renderSelectedRecords();
      if (symbolDiagnostics.length) {
        const summary = diagnosticsSummaryText() || "Static diagnostics detected.";
        markStaleValue(summary);
        statusValue = "idle";
        statusText = staleValueVisible ? "Results are stale until editor diagnostics are fixed." : summary;
        errorText = "";
      } else if (primaryVariable && !nextNames.includes(primaryVariable) && materializedRecords?.[primaryVariable]) {
        markStaleValue("Showing the last computed value while the selected symbol settles.");
        statusValue = "idle";
        statusText = staleValueVisible ? "Stale while editing." : `Ready to compute ${primaryVariable}.`;
        errorText = "";
      } else if (primaryVariable) {
        clearStaleValue();
        statusValue = "idle";
        statusText = `Ready to compute ${primaryVariable}.`;
        errorText = "";
        scheduleSymbolProbe();
      }
    } catch (error) {
      if (destroyed) return;
      if (token !== loadToken) return;
      probeToken += 1;
      stopProbe();
      const diagnostics = diagnosticsFromError(error);
      if (diagnostics.length) {
        symbolTable = {};
        symbolStatuses = {};
        symbolMaterializations = {};
        materializedRecords = {};
        symbolTypeHints = {};
        selectedVisualSymbols = [];
        primaryVariable = "";
        captionVariable = "-";
        resetViewer();
        applyStaticDiagnosticsState(diagnostics, error?.message || "Unable to refresh symbols.");
        return;
      }
      symbolTable = {};
      symbolDiagnostics = [{ code: "E_SYMBOLS", message: error.message || "Unable to refresh symbols." }];
      symbolStatuses = {};
      symbolMaterializations = {};
      materializedRecords = {};
      symbolTypeHints = {};
      selectedVisualSymbols = [];
      primaryVariable = "";
      captionVariable = "-";
      clearStaleValue();
      statusValue = "failed";
      statusText = "Unable to refresh symbols.";
      errorText = statusText;
      resetViewer();
    }
  };

  const handleEditorChange = async (event) => {
    programText = String(event?.detail?.value ?? programText ?? "");
    schedulePersist();
    stopEditRefresh();
    editRefreshSeq += 1;
    resolveRequestSeq += 1;
    resolveInFlight = false;
    probeToken += 1;
    stopProbe();
    stopPoll();
    activeValueSubscription = null;
    stopValueSocket();
    const token = editRefreshSeq;
    pendingEditRefresh = setTimeout(() => {
      if (destroyed) return;
      pendingEditRefresh = null;
      void (async () => {
        await refreshSymbols();
        if (destroyed) return;
        if (token !== editRefreshSeq) return;
        void refreshDisplayedValuesAfterEdit(token);
      })();
    }, EDIT_REQUEST_DEBOUNCE_MS);
  };

  const resolveCurrentPreferCache = async (interaction = null) => {
    const variableName = String(primaryVariable || "");
    const directRequest = Boolean(interaction?.direct);
    if (!variableName) return { state: "idle", reason: "no-primary" };
    if (materializedRecords?.[variableName]) {
      renderSelectedRecords();
      if (!currentPath) {
        return { state: "computed", reason: "local-cache" };
      }
    }
    if (hasKnownPendingState(variableName) && !directRequest) {
      subscribeValueSocket({ variable: variableName, path: currentPath || "", enqueue: false });
      ensurePendingPoll({ variable: variableName, path: currentPath || "/" });
      return { state: "pending", reason: "known-pending" };
    }
    const cachedAttempt = await resolvePrimaryValue({ enqueue: false, path: "", background: true, interaction });
    if (cachedAttempt?.state === "computed" || cachedAttempt?.state === "pending" || cachedAttempt?.state === "failed") {
      return cachedAttempt;
    }
    if (cachedAttempt?.state === "stale") {
      return cachedAttempt;
    }
    if (materializedRecords?.[variableName]) {
      return { state: "computed", reason: "local-cache" };
    }
    return resolvePrimaryValue({ enqueue: true, path: "", interaction });
  };

  const activatePrimarySymbol = async (token) => {
    const symbolName = String(token || "");
    if (!symbolName || !symbolTable[symbolName]) {
      return {
        ok: false,
        error: `Unknown symbol: ${symbolName || "<empty>"}`,
      };
    }
    const interaction = noteUiInteraction({
      intent: "symbol-click",
      source: "editor",
      variable: symbolName,
      path: "",
      direct: true,
      visible: true,
    });
    traceResolve("symbol-click", {
      token: symbolName,
      from: primaryVariable,
      currentStatus: statusValue,
      knownStatus: normalizeStatus(symbolStatuses?.[symbolName] || "idle"),
    });
    primaryVariable = symbolName;
    captionVariable = symbolName;
    selectedVisualSymbols = [symbolName];
    stopPoll();
    resolveRequestSeq += 1;
    const rendered = renderSelectedRecords();
    currentPath = "";
    if (!rendered) viewer.setLoading(`Loading ${symbolName}...`);
    const resolution = await resolveCurrentPreferCache(buildInteractionContext(interaction));
    return {
      ok: true,
      symbol: symbolName,
      resolution,
      state: getAutomationState(),
    };
  };

  const handleEditorSymbolClick = async (event) => {
    await activatePrimarySymbol(String(event?.detail?.token || ""));
  };

  const runPrimary = async () => {
    const interaction = noteUiInteraction({
      intent: "run-primary",
      source: "run-button",
      variable: primaryVariable,
      path: currentPath,
      direct: true,
      visible: true,
    });
    traceResolve("run-primary", {
      variable: primaryVariable,
      status: statusValue,
      path: currentPath || "/",
    });
    stopEditRefresh();
    resolveRequestSeq += 1;
    resolveInFlight = false;
    stopPoll();
    await refreshSymbols();
    ensureSelectedVisualSymbols();
    await resolvePrimaryValue({ enqueue: true, path: "", interaction: buildInteractionContext(interaction) });
  };

  export async function loadProgram(code, runAfterLoad = false) {
    programText = String(code || "");
    resolveRequestSeq += 1;
    resolveInFlight = false;
    stopPoll();
    stopProbe();
    clearPendingLogs();
    errorText = "";
    await refreshSymbols();
    ensureSelectedVisualSymbols();
    if (runAfterLoad) {
      await resolvePrimaryValue({ enqueue: true, path: "" });
    }
  }

  export function getProgramText() {
    return String(programText || "");
  }

  export function getAutomationState() {
    return {
      primaryVariable: String(primaryVariable || ""),
      captionVariable: String(captionVariable || ""),
      statusValue: String(statusValue || "idle"),
      statusText: String(statusText || ""),
      currentPath: String(currentPath || "/"),
      selectedVisualSymbols: Array.isArray(selectedVisualSymbols) ? [...selectedVisualSymbols] : [],
      symbolTable: { ...(symbolTable || {}) },
      symbolStatuses: { ...(symbolStatuses || {}) },
      symbolMaterializations: { ...(symbolMaterializations || {}) },
      programLength: String(programText || "").length,
    };
  }

  export async function selectSymbol(token) {
    return await activatePrimarySymbol(token);
  }

  const handleVisualTagClick = async (symbolName, event) => {
    const name = String(symbolName || "");
    if (!name || !symbolTable?.[name]) return;
    const interaction = noteUiInteraction({
      intent: "symbol-click",
      source: "results-tag",
      variable: name,
      path: "",
      direct: true,
      visible: true,
    });
    const additive = Boolean(event?.metaKey || event?.ctrlKey || event?.shiftKey);
    if (additive) {
      if (selectedVisualSymbols.includes(name)) {
        if (selectedVisualSymbols.length > 1) {
          selectedVisualSymbols = selectedVisualSymbols.filter((entry) => entry !== name);
          ensureSelectedVisualSymbols();
          renderSelectedRecords();
          return;
        }
      } else {
        selectedVisualSymbols = [...selectedVisualSymbols, name];
      }
    } else {
      selectedVisualSymbols = [name];
    }
    primaryVariable = name;
    captionVariable = name;
    currentPath = "";
    stopPoll();
    resolveRequestSeq += 1;
    ensureSelectedVisualSymbols();
    const rendered = renderSelectedRecords();
    if (!rendered) viewer.renderRecord(null);
    await resolveCurrentPreferCache(buildInteractionContext(interaction));
  };

  $: editorSymbolTypes = Object.fromEntries(
    Object.keys(symbolTable || {}).map((name) => [name, symbolTypeLabel(name)]),
  );

  $: if (maximizedViewerIndex >= viewerRecords.length || viewerMode !== "value") {
    maximizedViewerIndex = -1;
  }

  $: if (viewerMode === "value" && viewerRecords.length) {
    ensureRecordPages();
  }

  $: startPersistenceSignature = JSON.stringify({
    programText,
    editorViewState,
    showCodePanel,
    showResultsPanel,
    showOperationsPanel,
    showOperationsHelp,
    splitRatio,
    primaryVariable,
    currentPath,
    selectedVisualSymbols,
    maximizedViewerIndex,
    collectionSelections,
    recordPagePointers,
    expandedCollectionStages,
  });

  $: if (persistenceReady) {
    startPersistenceSignature;
    schedulePersist();
  }

  onMount(() => {
    let cancelled = false;

    ensureViewer();

    void (async () => {
      await restorePersistedStartState();
      await refreshSymbols();
      if (cancelled) return;
      if (startEditorRef && typeof startEditorRef.restoreViewState === "function" && editorViewState) {
        await tick();
        if (!cancelled && startEditorRef && typeof startEditorRef.restoreViewState === "function") {
          startEditorRef.restoreViewState(editorViewState);
        }
      }
      if (hasPersistedViewerRestore && primaryVariable) {
        void resolvePrimaryValue({ enqueue: false, path: currentPath, background: true });
      }
      persistenceReady = true;
    })();

    return () => {
      cancelled = true;
    };
  });

  onDestroy(() => {
    destroyed = true;
    stopSplitDrag();
    stopPoll();
    activeValueSubscription = null;
    stopValueSocket();
    stopProbe();
    stopEditRefresh();
    probeToken += 1;
    if (pendingSave) clearTimeout(pendingSave);
    if (pendingDreamCleanup) clearTimeout(pendingDreamCleanup);
    for (const timer of Object.values(recentMaterializeTimers || {})) {
      clearTimeout(timer);
    }
    for (const timer of Object.values(recordPagePollTimers || {})) {
      clearTimeout(timer);
    }
    stopAllRecordPageSockets();
    for (const timer of Object.values(pathRecordPollTimers || {})) {
      clearTimeout(timer);
    }
    for (const timer of Object.values(recentMaterializeTimers || {})) {
      clearTimeout(timer);
    }
    if (viewer && typeof viewer.destroy === "function") {
      viewer.destroy();
    }
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-start">
  <article class="card start-prime-shell">
    <div
      class={`start-prime-grid ${splitDragActive ? "is-resizing" : ""} ${showCodePanel ? "" : "is-code-hidden"} ${showCodePanel && !showResultsPanel && !showOperationsPanel ? "is-observation-hidden" : ""}`.trim()}
      bind:this={startPrimeGridEl}
      style={`--start-editor-width:${(splitRatio * 100).toFixed(1)}%`}
    >
      {#if showCodePanel}
        <section class="start-prime-editor">
          <div class="start-prime-editor-frame">
            <VoxCodeEditor
              bind:this={startEditorRef}
              ariaLabel="Start tab code editor"
              bind:value={programText}
              symbols={symbolTable}
              symbolStatuses={symbolStatuses}
              selectedSymbols={selectedVisualSymbols}
              symbolTypes={editorSymbolTypes}
              diagnostics={symbolDiagnostics}
              autocompleteEnabled={true}
              completionProvider={provideEditorCompletions}
              completionBuiltins={COMPLETION_BUILTINS}
              on:change={handleEditorChange}
              on:viewstate={handleEditorViewState}
              on:symbolclick={handleEditorSymbolClick}
            />
          </div>
        </section>
      {/if}

      {#if showCodePanel && (showResultsPanel || showOperationsPanel)}
        <button
          class="start-prime-splitter"
          type="button"
          aria-label="Resize code and observation panels"
          on:pointerdown={handleSplitPointerDown}
          on:keydown={handleSplitKeyDown}
        >
          <span></span>
        </button>
      {/if}

      <section class="start-prime-visual">
        <div class={`start-prime-stage ${showOperationsPanel ? "has-operations" : ""} ${!showResultsPanel && showOperationsPanel ? "is-operations-only" : ""}`.trim()}>
          {#if showResultsPanel}
            <div class={`start-prime-results ${staleValueVisible ? "is-stale" : ""}`.trim()}>
              <div class="start-viewer-wrap start-prime-viewer-wrap">
                <div class="start-pure-viewer">
                  {#if viewerMode === "error"}
                    <div class="viewer-error">{viewerErrorMessage || "Unable to visualize value."}</div>
                  {:else if viewerMode === "loading"}
                    <div class="start-viewer-message">{viewerMessage || "Computing..."}</div>
                  {:else if viewerMode === "value" && viewerRecords.length}
                    {#if staleValueVisible}
                      <div class="start-stale-banner" role="status" aria-live="polite">
                        <span class="start-stale-banner-label">Stale</span>
                        <span class="start-stale-banner-text">{staleValueReason || "Showing the last computed value while you edit."}</span>
                      </div>
                    {/if}
                    <div
                      class={`start-value-grid ${viewerRecords.length > 1 ? "is-multi" : "is-single"} ${maximizedViewerIndex >= 0 ? "has-maximized" : ""}`.trim()}
                    >
                      <!-- Keep stable slot keys so viewer hosts can morph in place across value updates. -->
                      {#each viewerRecords as record, index (index)}
                        {#if maximizedViewerIndex < 0 || maximizedViewerIndex === index}
                          {@const descriptor = recordDescriptor(record)}
                          <article
                            class={`start-value-card ${["integer", "number", "boolean", "null", "string", "bytes"].includes(recordType(record)) ? "is-centered-value" : ""} ${maximizedViewerIndex === index ? "is-maximized" : ""} is-${recordCardState(record, index)} ${recordJustMaterialized(record) ? "is-just-materialized" : ""} ${staleValueVisible ? "is-stale" : ""}`.trim()}
                            title={`${recordLabel(record, index)} (${typeLabelFromDescriptor(descriptor)})`}
                          >
                            <header class="start-value-card-head">
                              {#if viewerRecords.length > 1 || maximizedViewerIndex === index}
                                <span class="start-value-card-label">{recordLabel(record, index)}</span>
                              {:else}
                                <span class="start-value-card-label"></span>
                              {/if}
                              {#if staleValueVisible}
                                <span class="start-value-card-stale">stale</span>
                              {/if}
                              <button
                                class="btn btn-ghost btn-small start-value-card-expand"
                                type="button"
                                on:click={() => toggleMaximizedViewer(index)}
                              >
                                {maximizedViewerIndex === index ? "Restore" : "Maximize"}
                              </button>
                            </header>
                            <StartValueCanvas
                              {record}
                              label={recordLabel(record, index)}
                              sourceVariable={sourceVariableForRecord(record, index)}
                              level={0}
                              {collectionRecord}
                              {recordDescriptor}
                              {recordType}
                              {recordPath}
                              {previewText}
                              {typeLabelFromDescriptor}
                              {pageForRecord}
                              {pageErrorForRecord}
                              {pageLoadingForRecord}
                              {pagePollingForRecord}
                              {loadRecordPage}
                              {collectionItemsForPage}
                              {collectionSelectionFor}
                              {setCollectionSelection}
                              {loadCollectionPrev}
                              {loadCollectionNext}
                              {nestedRecordFromItem}
                              {pathRecordFor}
                              {pathRecordLoadingFor}
                              {pathRecordErrorFor}
                              {pathRecordPollingFor}
                              {loadPathRecord}
                              {recordPages}
                              {recordPagePointers}
                              {recordPagesLoading}
                              {recordPagesErrors}
                              {collectionSelections}
                              {expandedCollectionStages}
                              {pathRecords}
                              {pathRecordsLoading}
                              {pathRecordsErrors}
                              {setCollectionStageExpanded}
                            />
                          </article>
                        {/if}
                      {/each}
                    </div>
                  {:else}
                    <div class="start-viewer-message">Run or click a variable</div>
                  {/if}
                </div>
              </div>
            </div>
          {/if}

          {#if showOperationsPanel}
            <aside class="start-prime-operations">
              <section class="start-operations-panel" aria-live="polite">
                <div class="start-operations-panel-head">
                  <div class="start-operations-panel-title">
                    <span class="start-operations-panel-label">Operations</span>
                    <span class="start-operations-panel-state">
                      {$ongoingComputeActivity.length ? `${$ongoingComputeActivity.length} live` : "idle"}
                    </span>
                  </div>
                  <div class="start-operations-panel-actions">
                    <button
                      class={`btn btn-ghost btn-small start-operations-info ${showOperationsHelp ? "is-open" : ""}`.trim()}
                      type="button"
                      aria-expanded={showOperationsHelp}
                      aria-label="Explain operations labels"
                      title="Explain operations labels"
                      on:click={() => {
                        showOperationsHelp = !showOperationsHelp;
                      }}
                    >
                      i
                    </button>
                    <button class="btn btn-ghost btn-small" type="button" on:click={clearComputeActivity}>Clear</button>
                  </div>
                </div>

                {#if showOperationsHelp}
                  <div class="operations-help-card" role="note" aria-label="Operations help">
                    <div class="operations-help-title">What these labels mean</div>
                    <div class="operations-help-list">
                      {#each OPERATIONS_HELP_ROWS as row}
                        <div class="operations-help-row">
                          <span class="operations-help-label">{row.label}</span>
                          <span class="operations-help-detail">{row.detail}</span>
                        </div>
                      {/each}
                    </div>
                  </div>
                {/if}

                <div class="start-operations-panel-grid">
                  <div class="start-operations-section">
                    <div class="start-operations-section-head">Live now</div>
                    {#if !$ongoingComputeActivity.length}
                      <div class="start-operations-empty">No ongoing work right now.</div>
                    {:else}
                      <div class="start-operations-list">
                        {#each $ongoingComputeActivity as entry (entry.operationKey)}
                          {@const liveStatus = activeOperationStatus(entry)}
                          <article class={`start-operations-item is-live is-${liveStatus}`.trim()}>
                            <div class="start-operations-item-row">
                              <span class="start-operations-item-summary">{entry.summary}</span>
                              <span class={`start-operations-item-status is-${liveStatus}`.trim()}>{liveStatus}</span>
                            </div>
                            <div class="start-operations-item-meta">
                              {#if entry.detail}{entry.detail}{:else}{entry.variable ? `${entry.variable} ${entry.path || "/"}`.trim() : entry.path || "-"}{/if}
                            </div>
                          </article>
                        {/each}
                      </div>
                    {/if}
                  </div>

                  <div class="start-operations-section">
                    <div class="start-operations-section-head">Recent</div>
                    {#if !$computeActivity.length}
                      <div class="start-operations-empty">No activity yet.</div>
                    {:else}
                      <div class="start-operations-list is-history">
                        {#each $computeActivity.slice(0, 10) as entry (entry.id)}
                          {@const historyStatus = activeOperationStatus(entry)}
                          <article class={`start-operations-item is-history is-${historyStatus}`.trim()}>
                            <div class="start-operations-item-row">
                              <span class="start-operations-item-summary">{entry.summary}</span>
                              <span class="start-operations-item-time">{new Date(entry.ts).toLocaleTimeString()}</span>
                            </div>
                            <div class="start-operations-item-meta">
                              {#if entry.detail}{entry.detail}{:else if entry.variable}{`${entry.variable} ${entry.path || "/"}`.trim()}{:else}{entry.path || entry.type}{/if}
                            </div>
                          </article>
                        {/each}
                      </div>
                    {/if}
                  </div>
                </div>
              </section>
            </aside>
          {/if}

          {#if !showResultsPanel && !showOperationsPanel}
            <div class="start-prime-empty-state">Enable Results or Operations to observe the current run.</div>
          {/if}
        </div>

        <div class="start-prime-controls">
          {#if captionVariable !== "-" || staleValueVisible}
            <footer class="start-caption">
              <span class="start-caption-main">{captionVariable}</span>
              {#if staleValueVisible}
                <span class="start-caption-status">Stale while editing</span>
              {/if}
            </footer>
          {/if}
          <div class="start-value-tag-row">
            {#if !Object.keys(symbolTable || {}).length}
              <span class="start-value-tag start-value-tag--empty">No visualizable symbols yet</span>
            {:else}
              {#each Object.keys(symbolTable || {}) as symbolName}
                {@const symbolState = statusLabel(symbolName)}
                <button
                  class={`start-value-tag start-value-tag--${symbolState} ${selectedVisualSymbols.includes(symbolName) ? "is-selected" : ""}`.trim()}
                  type="button"
                  title={`${symbolTypeTitle(symbolName)} · ${symbolState}`}
                  on:click={(event) => void handleVisualTagClick(symbolName, event)}
                >
                  <span class="start-value-tag-name">{symbolName}</span>
                  <span class="start-value-tag-dot" aria-hidden="true"></span>
                </button>
              {/each}
            {/if}
          </div>
          <div class="start-prime-action-row">
            <button
              class={`btn btn-ghost btn-small start-pane-toggle ${showCodePanel ? "is-open" : ""}`.trim()}
              type="button"
              aria-pressed={showCodePanel}
              on:click={() => {
                showCodePanel = !showCodePanel;
              }}
            >
              <span>Code</span>
            </button>
            <button
              class={`btn btn-ghost btn-small start-pane-toggle ${showResultsPanel ? "is-open" : ""}`.trim()}
              type="button"
              aria-pressed={showResultsPanel}
              on:click={() => {
                showResultsPanel = !showResultsPanel;
              }}
            >
              <span>Results</span>
            </button>
            <button
              class={`btn btn-ghost btn-small start-pane-toggle start-operations-toggle ${showOperationsPanel ? "is-open" : ""}`.trim()}
              type="button"
              aria-pressed={showOperationsPanel}
              on:click={() => {
                showOperationsPanel = !showOperationsPanel;
              }}
            >
              <span>Operations</span>
              {#if $ongoingComputeActivity.length}
                <span class="start-operations-toggle-count">{$ongoingComputeActivity.length}</span>
              {/if}
            </button>
            <span class={`start-run-state start-run-state--${statusValue}`} aria-hidden="true"></span>
            <button class="btn btn-primary btn-small" type="button" on:click={runPrimary}>Run</button>
            <button class="btn btn-ghost btn-small start-panic-reset" type="button" on:click={handlePanicReset}>Panic Reset</button>
          </div>
        </div>
      </section>
    </div>

    {#if errorText && !symbolDiagnostics.length}
      <div class="inline-error">{errorText}</div>
    {/if}
  </article>
</section>
