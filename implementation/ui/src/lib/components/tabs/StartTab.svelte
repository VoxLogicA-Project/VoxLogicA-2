<script>
  import { onDestroy, onMount } from "svelte";
  import { getProgramSymbols, resolvePlaygroundValue, resolvePlaygroundValuePage } from "$lib/api/client.js";
  import { buildExecutionLogRows } from "$lib/utils/logs.js";
  import { buildFailureDetailsText, normalizedExecutionErrors } from "$lib/utils/playground-value.js";
  import VoxCodeEditor from "$lib/components/editor/VoxCodeEditor.svelte";
  import StartValueCanvas from "$lib/components/tabs/StartValueCanvas.svelte";

  export let active = false;
  export let capabilities = {};

  const STORAGE_KEY = "voxlogica.start.program.v1";
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
  let viewerRecords = [];
  let viewerMode = "empty";
  let viewerMessage = "";
  let viewerErrorMessage = "";
  let recordPages = {};
  let recordPagePointers = {};
  let recordPagesLoading = {};
  let recordPagesErrors = {};
  let collectionSelections = {};
  let recordPagePollTimers = {};
  let pathRecords = {};
  let pathRecordsLoading = {};
  let pathRecordsErrors = {};
  let pathRecordPollTimers = {};
  let resolutionActivityRows = [];
  let resolutionActivitySummary = "No resolution activity yet.";
  let activitySeenKeys = new Set();
  let dreamNodeIds = [];
  let dreamVisible = false;
  let dreamDissolving = false;
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
  let probeToken = 0;
  let resolveTraceSeq = 0;
  let resolveRequestSeq = 0;
  let resolveInFlight = false;
  let maximizedViewerIndex = -1;
  let startPrimeGridEl = null;
  let splitRatio = 0.48;
  let splitDragActive = false;
  let splitDragCleanup = null;
  let recentlyMaterialized = {};
  let recentMaterializeTimers = {};
  const MAX_PENDING_POLL_TICKS = 45;
  const COLLECTION_PAGE_SIZE = 18;
  const SPLIT_MIN = 0.32;
  const SPLIT_MAX = 0.68;

  let loadToken = 0;

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

  const traceResolve = (event, details = {}) => {
    try {
      console.info("[start-tab.resolve]", {
        event,
        ...details,
      });
    } catch {
      // best-effort instrumentation only
    }
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

  const syncSymbolMaterializations = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      next[name] = normalizeMaterialization(symbolMaterializations?.[name] || "unresolved");
    }
    symbolMaterializations = next;
  };

  const syncMaterializedRecords = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      if (materializedRecords?.[name]) {
        next[name] = materializedRecords[name];
      }
    }
    materializedRecords = next;
  };

  const syncSymbolTypeHints = (symbols, staticHints = {}) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
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

  const ensureSelectedVisualSymbols = () => {
    const names = Object.keys(symbolTable || {});
    const retained = selectedVisualSymbols.filter((name) => names.includes(name));
    if (retained.length) {
      selectedVisualSymbols = retained;
      return;
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

  const stopValueSocket = () => {
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
    const isPending =
      materialization === "pending" ||
      materialization === "missing" ||
      computeStatus === "queued" ||
      computeStatus === "running" ||
      computeStatus === "persisting";

    if (isMaterialized) {
      stopPoll();
      applyMaterialized(payload, requestedVariable || String(primaryVariable || ""));
      return;
    }
    if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") {
      stopPoll();
      setSymbolStatus(requestedVariable || String(primaryVariable || ""), "failed");
      applyFailure(payload, requestedVariable || String(primaryVariable || ""));
      return;
    }
    if (isPending) {
      applyPending(payload, requestedVariable || String(primaryVariable || ""));
    }
  };

  const ensureValueSocket = () => {
    if (valueWs && (valueWs.readyState === WebSocket.OPEN || valueWs.readyState === WebSocket.CONNECTING)) {
      return;
    }
    stopValueSocket();
    valueWs = new WebSocket(`${wsBaseUrl()}/ws/playground/value`);
    valueWs.onopen = () => {
      valueWsAttempts = 0;
      if (!activeValueSubscription) return;
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
      try {
        const message = JSON.parse(String(event.data || "{}"));
        if (message?.type === "value" || message?.type === "terminal") {
          applyStreamValuePayload(message?.payload || null);
        }
      } catch {
        // ignore malformed ws messages
      }
    };
    valueWs.onclose = () => {
      valueWs = null;
      if (!activeValueSubscription) return;
      valueWsAttempts += 1;
      const delay = wsReconnectDelayMs(valueWsAttempts);
      if (valueWsReconnectTimer) clearTimeout(valueWsReconnectTimer);
      valueWsReconnectTimer = setTimeout(() => {
        ensureValueSocket();
      }, delay);
    };
    valueWs.onerror = () => {
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

  const schedulePersist = () => {
    if (pendingSave) clearTimeout(pendingSave);
    pendingSave = setTimeout(() => {
      try {
        window.localStorage.setItem(STORAGE_KEY, String(programText || ""));
      } catch {
        // ignore persistence errors in restricted browser contexts
      }
    }, 180);
  };

  const probeOneSymbolStatus = async (symbolName, token) => {
    if (!symbolName || token !== probeToken) return;
    try {
      const payload = await resolvePlaygroundValue({
        program: programText,
        variable: symbolName,
        path: "",
        enqueue: false,
      });
      if (token !== probeToken) return;
      const materialization = normalizeStatus(payload?.materialization || "");
      const computeStatus = normalizeStatus(payload?.compute_status || "");
      if (materialization === "computed") {
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
      if (["queued", "running", "persisting"].includes(computeStatus)) {
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

  const pageErrorForRecord = (record, path = "") => recordPagesErrors?.[pageKey(record, path)] || "";

  const pageLoadingForRecord = (record, path = "") => {
    const baseKey = pageKey(record, path);
    const pointer = recordPagePointers?.[baseKey];
    if (pointer && recordPagesLoading?.[pointer]) return true;
    const prefix = `${baseKey}@`;
    return Object.keys(recordPagesLoading || {}).some((cacheKey) => cacheKey.startsWith(prefix));
  };

  const pagePollingForRecord = (record, path = "") => Boolean(recordPagePollTimers?.[pageKey(record, path)]);

  const cacheRecordPage = (record, path = "", page = null) => {
    if (!record || !page || typeof page !== "object") return null;
    const rawItems = Array.isArray(page?.items) ? page.items : null;
    if (!rawItems) return null;
    const resolvedPath = String(path || recordPath(record) || "/");
    const safeOffset = Math.max(0, Number(page?.offset || 0));
    const safeLimit = Math.max(1, Number(page?.limit || COLLECTION_PAGE_SIZE));
    const normalizedPage = {
      ...page,
      offset: safeOffset,
      limit: safeLimit,
      items: rawItems,
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
    return normalizedPage;
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

  const pathRecordFor = (sourceVariable = "", path = "") => pathRecords?.[pathRecordKey(sourceVariable, path)] || null;

  const pathRecordLoadingFor = (sourceVariable = "", path = "") => Boolean(pathRecordsLoading?.[pathRecordKey(sourceVariable, path)]);

  const pathRecordErrorFor = (sourceVariable = "", path = "") => String(pathRecordsErrors?.[pathRecordKey(sourceVariable, path)] || "");

  const pathRecordPollingFor = (sourceVariable = "", path = "") => Boolean(pathRecordPollTimers?.[pathRecordKey(sourceVariable, path)]);

  const cachePathRecord = (sourceVariable = "", path = "", payload = null) => {
    if (!sourceVariable || !payload) return;
    const key = pathRecordKey(sourceVariable, path);
    pathRecords = {
      ...pathRecords,
      [key]: payload,
    };
    const inlinePage =
      payload?.runtime_preview_page && typeof payload.runtime_preview_page === "object"
        ? payload.runtime_preview_page
        : payload?.page && typeof payload.page === "object"
          ? payload.page
          : null;
    if (inlinePage) {
      cacheRecordPage(payload, String(payload?.path || path || "/"), inlinePage);
    }
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
    const variableName = String(sourceVariable || "").trim();
    const targetPath = String(path || "");
    if (!variableName) return;
    const key = pathRecordKey(variableName, targetPath);
    if (pathRecordPollTimers?.[key]) return;
    const timer = setTimeout(() => {
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
    const variableName = String(sourceVariable || "").trim();
    const targetPath = String(path || "");
    if (!variableName) return null;
    const key = pathRecordKey(variableName, targetPath);
    if (!force && pathRecords?.[key]) return pathRecords[key];
    if (pathRecordsLoading?.[key]) return null;
    clearPathRecordPoll(key);

    pathRecordsLoading = { ...pathRecordsLoading, [key]: true };
    const nextErrors = { ...pathRecordsErrors };
    delete nextErrors[key];
    pathRecordsErrors = nextErrors;

    const tryResolve = async (enqueueFlag) =>
      resolvePlaygroundValue({
        program: programText,
        variable: variableName,
        path: targetPath,
        enqueue: enqueueFlag,
      });

    try {
      const first = await tryResolve(false);
      const firstMaterialization = String(first?.materialization || "").toLowerCase();
      const firstStatus = String(first?.compute_status || "").toLowerCase();
      const firstFailed = firstMaterialization === "failed" || ["failed", "killed"].includes(firstStatus);
      const firstMaterialized = (firstMaterialization === "computed" || firstMaterialization === "cached") && Boolean(first?.descriptor);
      const firstPending = ["pending", "missing", "queued", "running", "persisting"].includes(firstMaterialization) ||
        ["queued", "running", "persisting"].includes(firstStatus);
      if (firstFailed) {
        cachePathRecord(variableName, targetPath, first);
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
      if (firstMaterialized) {
        cachePathRecord(variableName, targetPath, first);
        clearPathRecordPoll(key);
        return first;
      }

      if (enqueueFallback && ["pending", "missing"].includes(firstMaterialization) && !["failed", "killed"].includes(firstStatus)) {
        const second = await tryResolve(true);
        const secondMaterialization = String(second?.materialization || "").toLowerCase();
        const secondStatus = String(second?.compute_status || "").toLowerCase();
        const secondFailed = secondMaterialization === "failed" || ["failed", "killed"].includes(secondStatus);
        const secondMaterialized = (secondMaterialization === "computed" || secondMaterialization === "cached") && Boolean(second?.descriptor);
        if (secondFailed) {
          cachePathRecord(variableName, targetPath, second);
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
        if (secondMaterialized) {
          cachePathRecord(variableName, targetPath, second);
          clearPathRecordPoll(key);
          return second;
        }
        const secondPending = ["pending", "missing", "queued", "running", "persisting"].includes(secondMaterialization) ||
          ["queued", "running", "persisting"].includes(secondStatus);
        if (secondPending) {
          schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
          return null;
        }
      }

      if (firstPending) {
        schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
        return null;
      }

      if (first?.descriptor && !["pending", "missing"].includes(firstMaterialization)) {
        cachePathRecord(variableName, targetPath, first);
        clearPathRecordPoll(key);
        return first;
      }
      return null;
    } catch (error) {
      if (isTimeoutError(error)) {
        schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 1100 });
        return pathRecords?.[key] || null;
      }
      pathRecordsErrors = {
        ...pathRecordsErrors,
        [key]: String(error?.message || error || "Unable to load value."),
      };
      return null;
    } finally {
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
    const resolvedPath = String(path || recordPath(record) || "");
    const baseKey = pageKey(record, resolvedPath);
    if (recordPagePollTimers?.[baseKey]) return;
    const timer = setTimeout(() => {
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
      return recordPages[cacheKey];
    }
    if (recordPagesLoading?.[cacheKey]) return null;
    recordPagesLoading = { ...recordPagesLoading, [cacheKey]: true };
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
        });

      let payload = await requestPage(false);
      const payloadFailed =
        String(payload?.materialization || "").toLowerCase() === "failed" ||
        ["failed", "killed"].includes(String(payload?.compute_status || "").toLowerCase());
      if (payloadFailed) {
        clearRecordPagePoll(baseKey);
        const details = buildFailureDetailsText(payload, {
          nodeId: variableName || String(record?.node_id || ""),
          path: resolvedPath,
        });
        statusValue = "failed";
        statusText = String(payload?.error || "Value resolution failed.");
        errorText = details;
        const fallbackPage =
          recordPages?.[cacheKey] || fallbackCollectionPage(descriptor, resolvedPath, resolvedOffset, resolvedLimit, "failed");
        if (fallbackPage) {
          recordPages = { ...recordPages, [cacheKey]: fallbackPage };
          recordPagePointers = { ...recordPagePointers, [baseKey]: cacheKey };
          return fallbackPage;
        }
        return null;
      }
      let page =
        payload?.page && typeof payload.page === "object"
          ? payload.page
          : { offset: resolvedOffset, limit: resolvedLimit, items: [], has_more: false, next_offset: null };
      const pendingStatuses = new Set(["queued", "running", "persisting", "pending", "missing"]);
      const payloadMaterialization = String(payload?.materialization || "").toLowerCase();
      const payloadStatus = String(payload?.compute_status || "").toLowerCase();
      const expectedLength = Number(descriptor?.summary?.length || 0);
      const likelyPending = pendingStatuses.has(payloadMaterialization) || pendingStatuses.has(payloadStatus);
      const needsFallback = !Array.isArray(page?.items) || page.items.length === 0;

      if (enqueueFallback && needsFallback && (likelyPending || expectedLength > 0)) {
        payload = await requestPage(true);
        const fallbackFailed =
          String(payload?.materialization || "").toLowerCase() === "failed" ||
          ["failed", "killed"].includes(String(payload?.compute_status || "").toLowerCase());
        if (fallbackFailed) {
          clearRecordPagePoll(baseKey);
          const details = buildFailureDetailsText(payload, {
            nodeId: variableName || String(record?.node_id || ""),
            path: resolvedPath,
          });
          statusValue = "failed";
          statusText = String(payload?.error || "Value resolution failed.");
          errorText = details;
          const fallbackPage =
            recordPages?.[cacheKey] || fallbackCollectionPage(descriptor, resolvedPath, resolvedOffset, resolvedLimit, "failed");
          if (fallbackPage) {
            recordPages = { ...recordPages, [cacheKey]: fallbackPage };
            recordPagePointers = { ...recordPagePointers, [baseKey]: cacheKey };
            return fallbackPage;
          }
          return null;
        }
        page =
          payload?.page && typeof payload.page === "object"
            ? payload.page
            : { offset: resolvedOffset, limit: resolvedLimit, items: [], has_more: false, next_offset: null };
      }

      const pageMaterialization = String(payload?.materialization || "").toLowerCase();
      const pageStatus = String(payload?.compute_status || "").toLowerCase();
      const pagePending = pendingStatuses.has(pageMaterialization) || pendingStatuses.has(pageStatus);
      const pageItems = Array.isArray(page?.items) ? page.items : [];
      const hasPendingItems = pageItems.some((item) => {
        const itemStatus = String(item?.status || "").toLowerCase();
        const itemType = String(item?.descriptor?.vox_type || "").toLowerCase();
        return (
          ["pending", "missing", "queued", "running", "persisting"].includes(itemStatus) ||
          itemType === "unavailable" ||
          !itemType
        );
      });
      if (hasPendingItems || (pagePending && (!Array.isArray(page?.items) || page.items.length === 0))) {
        scheduleRecordPagePoll(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          sourceVariable: variableName,
          delayMs: 950,
        });
      } else {
        clearRecordPagePoll(baseKey);
      }

      const previousPage = recordPages?.[cacheKey];
      const incomingItems = Array.isArray(page?.items) ? page.items : [];
      const keepPrevious = Boolean(previousPage) && pagePending && incomingItems.length === 0;
      const effectivePage = keepPrevious ? previousPage : page;
      recordPages = { ...recordPages, [cacheKey]: effectivePage };
      recordPagePointers = { ...recordPagePointers, [baseKey]: cacheKey };
      const items = collectionItemsForPage(effectivePage, recordType(record));
      const currentSelection = collectionSelectionFor(record, resolvedPath);
      let selectedIndex = Math.max(0, Number(currentSelection?.selectedIndex || 0));
      let selectedAbsoluteIndex = Math.max(0, Number(currentSelection?.selectedAbsoluteIndex || 0));
      let selectedPath = String(currentSelection?.selectedPath || "");
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
          const byAbsolute = selectedAbsoluteIndex >= resolvedOffset ? selectedAbsoluteIndex - resolvedOffset : -1;
          selectedIndex = byAbsolute >= 0 && byAbsolute < items.length ? byAbsolute : 0;
        }
        selectedPath = String(items[selectedIndex]?.path || "");
      }
      setCollectionSelection(record, resolvedPath, { selectedIndex, selectedAbsoluteIndex, selectedPath });
      return effectivePage;
    } catch (error) {
      if (isTimeoutError(error)) {
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
        scheduleRecordPagePoll(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          sourceVariable: variableName,
          delayMs: 520,
        });
        return recordPages?.[cacheKey] || null;
      }
      recordPagesErrors = {
        ...recordPagesErrors,
        [baseKey]: String(error?.message || error || "Unable to load collection values."),
      };
      return null;
    } finally {
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
    dreamVisible = true;
    dreamDissolving = false;
    const ids = collectDreamIds(payload);
    dreamNodeIds = ids.length ? ids : [String(symbolTable?.[primaryVariable] || primaryVariable || "node")];
  };

  const dissolveDream = () => {
    if (!dreamVisible) return;
    dreamDissolving = true;
    if (pendingDreamCleanup) clearTimeout(pendingDreamCleanup);
    pendingDreamCleanup = setTimeout(() => {
      dreamVisible = false;
      dreamDissolving = false;
      dreamNodeIds = [];
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
    for (const timer of Object.values(pathRecordPollTimers || {})) {
      clearTimeout(timer);
    }
    viewerRecords = [];
    viewerMode = "empty";
    viewerMessage = "";
    viewerErrorMessage = "";
    recordPages = {};
    recordPagePointers = {};
    recordPagesLoading = {};
    recordPagesErrors = {};
    collectionSelections = {};
    recordPagePollTimers = {};
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

  const resolveContextMatches = (request = {}) =>
    String(request?.variable || "") === String(primaryVariable || "") &&
    String(request?.path || "") === String(currentPath || "") &&
    String(request?.program || "") === String(programText || "");

  const applyFailure = (payload, variableName) => {
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

  const resolvePrimaryValue = async ({ enqueue = true, path = "", background = false } = {}) => {
    ensureViewer();
    const traceId = resolveTraceSeq + 1;
    resolveTraceSeq = traceId;
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
      const details = diagnosticsDetailsText() || summary;
      traceResolve("skip-diagnostics", {
        traceId,
        enqueue,
        path,
        variable: primaryVariable,
        diagnostics: symbolDiagnostics.length,
      });
      statusValue = "failed";
      statusText = summary;
      captionVariable = primaryVariable;
      dissolveDream();
      viewer.setError(summary);
      errorText = details;
      setSymbolStatus(primaryVariable, "failed");
      return { state: "failed", reason: "diagnostics" };
    }

    if (enqueue) {
      // User-driven resolve takes priority over passive symbol probes.
      probeToken += 1;
      stopProbe();
    }

    currentPath = String(path || "");
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
      });
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
      const isPending =
        materialization === "pending" ||
        materialization === "missing" ||
        computeStatus === "queued" ||
        computeStatus === "running" ||
        computeStatus === "persisting";

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
        const hasProgressSignal =
          materialization === "pending" ||
          ["queued", "running", "persisting"].includes(computeStatus) ||
          Boolean(payload?.request_enqueued) ||
          Boolean(payload?.job_id);
        traceResolve("branch-pending", {
          traceId,
          variable: request.variable,
          path: currentPath || "/",
          materialization,
          computeStatus,
          hasProgressSignal,
          pollActive: Boolean(pendingPoll),
        });
        applyPending(payload, request.variable);
        if (!hasProgressSignal) {
          activeValueSubscription = null;
          stopValueSocket();
          stopPoll();
          statusValue = "idle";
          statusText = `${request.variable} is not ready yet. Click Run or click the tag again to refresh.`;
          setSymbolStatus(request.variable, "idle");
          setSymbolMaterialization(request.variable, materialization || "unresolved");
          dissolveDream();
          return { state: "idle", reason: "no-progress" };
        }
        if (!subscribeValueSocket({ variable: request.variable, path: currentPath || "/", enqueue })) {
          ensurePendingPoll({ traceId, variable: request.variable, path: currentPath || "/" });
        } else {
          stopPoll();
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
        if (!subscribeValueSocket({ variable: request.variable, path: currentPath || "/", enqueue })) {
          ensurePendingPoll({ traceId, variable: request.variable, path: currentPath || "/" });
        } else {
          stopPoll();
        }
        return { state: "pending", reason: "request-timeout" };
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
      statusValue = "idle";
      statusText = "Write code and run to compute a value.";
      errorText = "";
      resetViewer();
      return;
    }

    try {
      const payload = await getProgramSymbols(programText);
      if (token !== loadToken) return;
      const available = payload?.available !== false;
      symbolTable = available ? payload.symbol_table || {} : {};
      symbolDiagnostics = payload?.diagnostics || [];
      const staticTypeHints = available ? payload?.symbol_output_kinds || {} : {};
      syncSymbolStatuses(symbolTable);
      syncSymbolMaterializations(symbolTable);
      syncMaterializedRecords(symbolTable);
      syncSymbolTypeHints(symbolTable, staticTypeHints);
      primaryVariable = inferPrimaryVariable(programText, symbolTable);
      ensureSelectedVisualSymbols();
      captionVariable = primaryVariable || "-";
      renderSelectedRecords();
      if (symbolDiagnostics.length) {
        const summary = diagnosticsSummaryText() || "Static diagnostics detected.";
        statusValue = "failed";
        statusText = summary;
        errorText = diagnosticsDetailsText() || summary;
        viewer.setError(summary);
      } else if (primaryVariable) {
        statusValue = "idle";
        statusText = `Ready to compute ${primaryVariable}.`;
        errorText = "";
        scheduleSymbolProbe();
      }
    } catch (error) {
      if (token !== loadToken) return;
      probeToken += 1;
      stopProbe();
      symbolTable = {};
      symbolDiagnostics = [{ code: "E_SYMBOLS", message: error.message || "Unable to refresh symbols." }];
      symbolStatuses = {};
      symbolMaterializations = {};
      materializedRecords = {};
      symbolTypeHints = {};
      selectedVisualSymbols = [];
      primaryVariable = "";
      captionVariable = "-";
      statusValue = "failed";
      statusText = "Unable to refresh symbols.";
      errorText = statusText;
      resetViewer();
    }
  };

  const handleEditorChange = async (event) => {
    programText = String(event?.detail?.value ?? programText ?? "");
    schedulePersist();
    resolveRequestSeq += 1;
    resolveInFlight = false;
    probeToken += 1;
    stopProbe();
    stopPoll();
    activeValueSubscription = null;
    stopValueSocket();
    resetViewer();
    clearResolutionActivity();
    await refreshSymbols();
  };

  const resolveCurrentPreferCache = async () => {
    const variableName = String(primaryVariable || "");
    if (!variableName) return { state: "idle", reason: "no-primary" };
    if (materializedRecords?.[variableName]) {
      renderSelectedRecords();
    }
    const cachedAttempt = await resolvePrimaryValue({ enqueue: false, path: "", background: true });
    if (cachedAttempt?.state === "computed" || cachedAttempt?.state === "pending" || cachedAttempt?.state === "failed") {
      return cachedAttempt;
    }
    if (cachedAttempt?.state === "stale") {
      return cachedAttempt;
    }
    if (materializedRecords?.[variableName]) {
      return { state: "computed", reason: "local-cache" };
    }
    return resolvePrimaryValue({ enqueue: true, path: "" });
  };

  const handleEditorSymbolClick = async (event) => {
    const token = String(event?.detail?.token || "");
    if (!token || !symbolTable[token]) return;
    traceResolve("symbol-click", {
      token,
      from: primaryVariable,
      currentStatus: statusValue,
      knownStatus: normalizeStatus(symbolStatuses?.[token] || "idle"),
    });
    primaryVariable = token;
    captionVariable = token;
    selectedVisualSymbols = [token];
    stopPoll();
    resolveRequestSeq += 1;
    const rendered = renderSelectedRecords();
    if (!rendered) viewer.renderRecord(null);
    currentPath = "";
    await resolveCurrentPreferCache();
  };

  const runPrimary = async () => {
    traceResolve("run-primary", {
      variable: primaryVariable,
      status: statusValue,
      path: currentPath || "/",
    });
    resolveRequestSeq += 1;
    resolveInFlight = false;
    stopPoll();
    await refreshSymbols();
    ensureSelectedVisualSymbols();
    await resolvePrimaryValue({ enqueue: true, path: "" });
  };

  export async function loadProgram(code, runAfterLoad = false) {
    programText = String(code || "");
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, programText);
    }
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

  const handleVisualTagClick = async (symbolName, event) => {
    const name = String(symbolName || "");
    if (!name || !symbolTable?.[name]) return;
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
    await resolveCurrentPreferCache();
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

  onMount(async () => {
    ensureViewer();
    try {
      const persisted = window.localStorage.getItem(STORAGE_KEY);
      if (persisted && String(persisted).trim()) {
        programText = persisted;
      }
    } catch {
      // ignore localStorage errors
    }

    await refreshSymbols();
  });

  onDestroy(() => {
    stopSplitDrag();
    stopPoll();
    activeValueSubscription = null;
    stopValueSocket();
    stopProbe();
    probeToken += 1;
    if (pendingSave) clearTimeout(pendingSave);
    if (pendingDreamCleanup) clearTimeout(pendingDreamCleanup);
    for (const timer of Object.values(recordPagePollTimers || {})) {
      clearTimeout(timer);
    }
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
      class={`start-prime-grid ${splitDragActive ? "is-resizing" : ""}`.trim()}
      bind:this={startPrimeGridEl}
      style={`--start-editor-width:${(splitRatio * 100).toFixed(1)}%`}
    >
      <section class="start-prime-editor">
        <div class="start-prime-editor-frame">
          <VoxCodeEditor
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
            on:symbolclick={handleEditorSymbolClick}
          />
        </div>
      </section>

      <button
        class="start-prime-splitter"
        type="button"
        aria-label="Resize editor and viewer panels"
        on:pointerdown={handleSplitPointerDown}
        on:keydown={handleSplitKeyDown}
      >
        <span></span>
      </button>

      <section class="start-prime-visual">
        <div class="start-viewer-wrap start-prime-viewer-wrap">
          <div class={`start-pure-viewer ${dreamVisible ? "is-under-dream" : ""}`}>
            {#if viewerMode === "error"}
              <div class="viewer-error">{viewerErrorMessage || "Unable to visualize value."}</div>
            {:else if viewerMode === "loading" && !dreamVisible}
              <div class="start-viewer-message">{viewerMessage || "Computing..."}</div>
            {:else if viewerMode === "value" && viewerRecords.length}
              <div
                class={`start-value-grid ${viewerRecords.length > 1 ? "is-multi" : "is-single"} ${maximizedViewerIndex >= 0 ? "has-maximized" : ""} ${dreamVisible ? "is-materializing" : ""}`.trim()}
              >
                {#each viewerRecords as record, index (`${record?.node_id || "value"}-${index}`)}
                  {#if maximizedViewerIndex < 0 || maximizedViewerIndex === index}
                    {@const descriptor = recordDescriptor(record)}
                    <article
                      class={`start-value-card ${["integer", "number", "boolean", "null", "string", "bytes"].includes(recordType(record)) ? "is-centered-value" : ""} ${maximizedViewerIndex === index ? "is-maximized" : ""} is-${recordCardState(record, index)} ${recordJustMaterialized(record) ? "is-just-materialized" : ""}`.trim()}
                      title={`${recordLabel(record, index)} (${typeLabelFromDescriptor(descriptor)})`}
                    >
                      <header class="start-value-card-head">
                        {#if viewerRecords.length > 1 || maximizedViewerIndex === index}
                          <span class="start-value-card-label">{recordLabel(record, index)}</span>
                        {:else}
                          <span class="start-value-card-label"></span>
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
                        {pathRecords}
                        {pathRecordsLoading}
                        {pathRecordsErrors}
                      />
                    </article>
                  {/if}
                {/each}
              </div>
            {:else}
              <div class="start-viewer-message">Run or click a variable</div>
            {/if}
          </div>

          {#if dreamVisible}
            <div class={`start-compute-dream ${dreamDissolving ? "is-dissolving" : ""}`}>
              <div class="start-compute-mist"></div>
              <div class="start-compute-ids">
                {#each dreamNodeIds as nodeId, nodeIndex (`${nodeId}-${nodeIndex}`)}
                  <span class="start-compute-id" style={`--node-index:${nodeIndex}`}>{shortNodeId(nodeId)}</span>
                {/each}
              </div>
            </div>
          {/if}
        </div>

        <div class="start-prime-controls">
          {#if !symbolDiagnostics.length}
            <footer class="start-caption">
              <span class="start-caption-main">{captionVariable}</span>
            </footer>
          {/if}
          <div class="start-value-tag-row">
            {#if symbolDiagnostics.length}
              <span class="start-value-tag start-value-tag--empty">Fix syntax errors to visualize values</span>
            {:else if !Object.keys(symbolTable || {}).length}
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
            <span class={`start-run-state start-run-state--${statusValue}`} aria-hidden="true"></span>
            <button class="btn btn-primary btn-small" type="button" on:click={runPrimary}>Run</button>
          </div>
        </div>
      </section>
    </div>

    {#if errorText}
      <div class="inline-error">{errorText}</div>
    {/if}
  </article>
</section>
