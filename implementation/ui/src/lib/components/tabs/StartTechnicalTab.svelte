<script>
  import { onDestroy, onMount } from "svelte";
  import { getProgramSymbols, resolvePlaygroundValue, resolvePlaygroundValuePage } from "$lib/api/client.js";
  import { buildExecutionLogRows } from "$lib/utils/logs.js";
  import { summarizeDescriptor } from "$lib/utils/playground-value.js";
  import VoxCodeEditor from "$lib/components/editor/VoxCodeEditor.svelte";
  import StatusChip from "$lib/components/shared/StatusChip.svelte";

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

  let splitPercent = 62;
  let resizeActive = false;

  let viewerContainer;
  let viewer = null;

  let captionVariable = "-";
  let captionType = "-";
  let statusValue = "idle";
  let statusText = "Write code and run to materialize a result.";
  let errorText = "";
  let pendingLogSummary = "No execution log yet.";
  let pendingLogRows = [];
  let pendingLogRaw = "";
  let pendingLogJobId = "";
  let symbolStatuses = {};
  let resolutionActivityRows = [];
  let resolutionActivitySummary = "No resolution activity yet.";
  let activitySeenKeys = new Set();
  let currentPath = "";
  let pendingPoll = null;
  let pendingSave = null;
  let pendingProbe = null;
  let probeToken = 0;
  let resolveTraceSeq = 0;

  let initialized = false;
  let loadToken = 0;

  const diagnosticsText = () => {
    const rows = Array.isArray(symbolDiagnostics) ? symbolDiagnostics : [];
    if (!rows.length) return "";
    return rows
      .map((diag) => {
        const code = diag?.code ? `[${diag.code}] ` : "";
        const message = diag?.message ? String(diag.message) : "Static error";
        const location = diag?.location ? ` @ ${diag.location}` : "";
        return `${code}${message}${location}`;
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

  const syncSymbolStatuses = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      next[name] = normalizeStatus(symbolStatuses?.[name] || "idle");
    }
    symbolStatuses = next;
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
    if (!pendingPoll) return;
    clearInterval(pendingPoll);
    pendingPoll = null;
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
        return;
      }
      if (["queued", "running", "persisting"].includes(computeStatus)) {
        setSymbolStatus(symbolName, computeStatus);
        return;
      }
      setSymbolStatus(symbolName, "idle");
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

  const ensureViewer = () => {
    if (viewer || !viewerContainer) return;

    const ctor = window.VoxResultViewer?.ResultViewer;
    if (typeof ctor === "function") {
      viewer = new ctor(viewerContainer, {
        onNavigate: (path) => {
          currentPath = String(path || "");
          void resolvePrimaryValue({ enqueue: false, path: currentPath });
        },
        fetchPage: ({ nodeId, path, offset, limit }) =>
          resolvePlaygroundValuePage({
            program: programText,
            nodeId: nodeId || "",
            variable: primaryVariable,
            path: path || "",
            offset: Number(offset || 0),
            limit: Number(limit || 64),
            enqueue: false,
          }),
      });
      return;
    }

    viewer = {
      setLoading: (message) => {
        viewerContainer.textContent = message || "Loading...";
      },
      setError: (message) => {
        viewerContainer.textContent = message || "Viewer error";
      },
      renderRecord: (record) => {
        viewerContainer.textContent = JSON.stringify(record || {}, null, 2);
      },
    };
  };

  const applyFailure = (payload, variableName) => {
    const message = String(payload?.error || "Unable to inspect value.");
    statusValue = "failed";
    statusText = message;
    captionVariable = variableName || "-";
    captionType = "error";
    errorText = message;
    viewer.renderRecord({
      available: false,
      node_id: payload?.node_id || "",
      status: "failed",
      path: payload?.path || "/",
      error: message,
      descriptor: {
        vox_type: "string",
        format_version: "voxpod/1",
        summary: { value: message, length: message.length, truncated: false },
        navigation: {
          path: payload?.path || "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
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
    captionType = payload?.descriptor?.vox_type || "in-progress";
    errorText = "";
    setSymbolStatus(variableName, state);
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
    viewer.renderRecord(payload);
    statusValue = "completed";
    statusText = descriptor ? summarizeDescriptor(descriptor) : `Computed ${variableName}`;
    captionVariable = variableName || "-";
    captionType = descriptor?.vox_type || "value";
    errorText = "";
    if (materialization === "cached") {
      statusText = `${statusText} (cached)`;
    }
    setSymbolStatus(variableName, "computed");
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

  const resolvePrimaryValue = async ({ enqueue = true, path = "" } = {}) => {
    ensureViewer();
    const traceId = resolveTraceSeq + 1;
    resolveTraceSeq = traceId;
    if (!primaryVariable) {
      traceResolve("skip-no-primary", { traceId, enqueue, path });
      statusValue = "idle";
      statusText = "Define at least one variable to inspect.";
      captionVariable = "-";
      captionType = "-";
      viewer.renderRecord(null);
      return;
    }
    if (symbolDiagnostics.length) {
      traceResolve("skip-diagnostics", {
        traceId,
        enqueue,
        path,
        variable: primaryVariable,
        diagnostics: symbolDiagnostics.length,
      });
      statusValue = "failed";
      statusText = "Fix static diagnostics before execution.";
      captionVariable = primaryVariable;
      captionType = "error";
      viewer.setError(statusText);
      errorText = statusText;
      setSymbolStatus(primaryVariable, "failed");
      return;
    }

    if (enqueue) {
      // User-driven resolve takes priority over passive symbol probes.
      probeToken += 1;
      stopProbe();
    }

    currentPath = String(path || "");
    traceResolve("start", {
      traceId,
      enqueue,
      variable: primaryVariable,
      path: currentPath || "/",
      statusBefore: statusValue,
    });
    statusValue = "running";
    statusText = `Resolving ${primaryVariable}...`;
    captionVariable = primaryVariable;
    captionType = "in-progress";
    errorText = "";
    setSymbolStatus(primaryVariable, "running");
    viewer.setLoading(`Resolving ${primaryVariable}${currentPath ? ` @ ${currentPath}` : ""}...`);

    try {
      const requestStarted = performance?.now ? performance.now() : Date.now();
      traceResolve("request-dispatch", {
        traceId,
        enqueue,
        variable: primaryVariable,
        path: currentPath || "/",
      });
      const payload = await resolvePlaygroundValue({
        program: programText,
        variable: primaryVariable,
        path: currentPath,
        enqueue,
      });
      const requestElapsedMs = (performance?.now ? performance.now() : Date.now()) - requestStarted;
      traceResolve("response", {
        traceId,
        enqueue,
        variable: primaryVariable,
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
          variable: primaryVariable,
          path: currentPath || "/",
          materialization,
          computeStatus,
        });
        stopPoll();
        applyMaterialized(payload, primaryVariable);
        return;
      }

      if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") {
        traceResolve("branch-failed", {
          traceId,
          variable: primaryVariable,
          path: currentPath || "/",
          materialization,
          computeStatus,
          error: String(payload?.error || ""),
        });
        stopPoll();
        setSymbolStatus(primaryVariable, "failed");
        applyFailure(payload, primaryVariable);
        return;
      }

      if (isPending) {
        traceResolve("branch-pending", {
          traceId,
          variable: primaryVariable,
          path: currentPath || "/",
          materialization,
          computeStatus,
          pollActive: Boolean(pendingPoll),
        });
        applyPending(payload, primaryVariable);
        if (!pendingPoll) {
          traceResolve("poll-start", {
            traceId,
            variable: primaryVariable,
            path: currentPath || "/",
          });
          pendingPoll = setInterval(() => {
            traceResolve("poll-tick", {
              traceId,
              variable: primaryVariable,
              path: currentPath || "/",
            });
            void resolvePrimaryValue({ enqueue: false, path: currentPath });
          }, 1000);
        }
        return;
      }

      traceResolve("branch-unexpected", {
        traceId,
        variable: primaryVariable,
        path: currentPath || "/",
        materialization,
        computeStatus,
      });
      stopPoll();
      setSymbolStatus(primaryVariable, "failed");
      applyFailure(
        {
          ...payload,
          error: payload?.error || "Unexpected materialization state.",
        },
        primaryVariable,
      );
    } catch (error) {
      traceResolve("request-error", {
        traceId,
        enqueue,
        variable: primaryVariable,
        path: currentPath || "/",
        message: String(error?.message || error || "unknown"),
      });
      stopPoll();
      setSymbolStatus(primaryVariable, "failed");
      applyFailure({ error: error.message || "Request failed." }, primaryVariable);
    }
  };

  const refreshSymbols = async () => {
    if (capabilities.playground_symbols === false) {
      probeToken += 1;
      stopProbe();
      symbolTable = {};
      symbolDiagnostics = [];
      symbolStatuses = {};
      primaryVariable = "";
      captionVariable = "-";
      captionType = "-";
      statusValue = "failed";
      statusText = "Program symbol API unavailable on this backend.";
      errorText = statusText;
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
      primaryVariable = "";
      captionVariable = "-";
      captionType = "-";
      statusValue = "idle";
      statusText = "Write code and run to materialize a result.";
      errorText = "";
      return;
    }

    try {
      const payload = await getProgramSymbols(programText);
      if (token !== loadToken) return;
      const available = payload?.available !== false;
      symbolTable = available ? payload.symbol_table || {} : {};
      symbolDiagnostics = payload?.diagnostics || [];
      syncSymbolStatuses(symbolTable);
      primaryVariable = inferPrimaryVariable(programText, symbolTable);
      captionVariable = primaryVariable || "-";
      if (symbolDiagnostics.length) {
        statusValue = "failed";
        statusText = "Static diagnostics detected.";
        errorText = statusText;
      } else if (primaryVariable) {
        statusValue = "idle";
        statusText = `Ready to resolve ${primaryVariable}.`;
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
      primaryVariable = "";
      captionVariable = "-";
      statusValue = "failed";
      statusText = "Unable to refresh symbols.";
      errorText = statusText;
    }
  };

  const handleEditorChange = async (event) => {
    programText = String(event?.detail?.value ?? programText ?? "");
    schedulePersist();
    probeToken += 1;
    stopProbe();
    clearResolutionActivity();
    await refreshSymbols();
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
    currentPath = "";
    await resolvePrimaryValue({ enqueue: true, path: "" });
  };

  const runPrimary = async () => {
    traceResolve("run-primary", {
      variable: primaryVariable,
      status: statusValue,
      path: currentPath || "/",
    });
    stopPoll();
    await refreshSymbols();
    await resolvePrimaryValue({ enqueue: true, path: "" });
  };

  const startResize = (event) => {
    event.preventDefault();
    const splitRoot = event.currentTarget?.parentElement;
    if (!(splitRoot instanceof HTMLElement)) return;

    const rect = splitRoot.getBoundingClientRect();
    resizeActive = true;

    const onPointerMove = (moveEvent) => {
      const y = moveEvent.clientY - rect.top;
      const next = Math.max(28, Math.min(82, (y / rect.height) * 100));
      splitPercent = Number(next.toFixed(2));
    };

    const onPointerUp = () => {
      resizeActive = false;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  };

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

    initialized = true;
    await refreshSymbols();
  });

  onDestroy(() => {
    stopPoll();
    stopProbe();
    probeToken += 1;
    if (pendingSave) clearTimeout(pendingSave);
    if (viewer && typeof viewer.destroy === "function") {
      viewer.destroy();
    }
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-start-tech">
  <article class="card start-shell">
    <div class="start-split" style={`grid-template-rows: minmax(220px, ${splitPercent}fr) 10px minmax(220px, ${100 - splitPercent}fr);`}>
      <section class="start-result">
        <header class="start-head">
          <h2>Start Technical</h2>
          <div class="row gap-s">
            <StatusChip value={statusValue} />
            <button class="btn btn-primary btn-small" type="button" on:click={runPrimary}>Run</button>
          </div>
        </header>
        <div class="start-viewer-wrap">
          <div class="result-inspector start-viewer" bind:this={viewerContainer}></div>
        </div>
        <footer class="start-caption">
          <span class="start-caption-main">{captionVariable}</span>
          <span class="start-caption-type">{captionType}</span>
        </footer>
      </section>

      <div
        class={`start-divider ${resizeActive ? "is-active" : ""}`}
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize result and code panes"
        on:pointerdown={startResize}
      >
        <span></span>
      </div>

      <section class="start-editor">
        <VoxCodeEditor
          ariaLabel="Start tab code editor"
          bind:value={programText}
          symbols={symbolTable}
          symbolStatuses={symbolStatuses}
          diagnostics={symbolDiagnostics}
          autocompleteEnabled={true}
          completionProvider={provideEditorCompletions}
          completionBuiltins={COMPLETION_BUILTINS}
          on:change={handleEditorChange}
          on:symbolclick={handleEditorSymbolClick}
        />
      </section>
    </div>

    <p class="start-status muted">{statusText}</p>

    <section class="start-activity">
      <header class="viewer-header">
        <h3>Resolution activity</h3>
        <span class="muted">{resolutionActivitySummary}</span>
      </header>
      <div class="start-status-grid">
        {#if !Object.keys(symbolTable || {}).length}
          <span class="start-status-pill start-status-pill--idle">No symbols</span>
        {:else}
          {#each Object.keys(symbolTable || {}) as symbolName}
            <span class={`start-status-pill start-status-pill--${normalizeStatus(symbolStatuses?.[symbolName] || "idle")}`}>
              {symbolName}: {normalizeStatus(symbolStatuses?.[symbolName] || "idle")}
            </span>
          {/each}
        {/if}
      </div>
      <div class="table-like muted">
        {#if !resolutionActivityRows.length}
          <article class="table-like-row">
            <div class="name">No events yet</div>
            <div class="detail">Click a variable to start resolution and watch status transitions.</div>
          </article>
        {:else}
          {#each resolutionActivityRows as row}
            <article class="table-like-row">
              <div class="name">{row.variableName || "(internal)"} · {row.status}</div>
              <div class="detail">{row.operator || "node"} · cache={row.cacheSource || "-"} · {row.durationS.toFixed(3)}s · {row.nodeId ? row.nodeId.slice(0, 12) : "-"}</div>
            </article>
          {/each}
        {/if}
      </div>
    </section>

    {#if statusValue === "running"}
      <section class="start-logs">
        <header class="viewer-header">
          <h3>Resolution log</h3>
          <span class="muted">{pendingLogJobId ? `job ${pendingLogJobId.slice(0, 12)}` : "job pending"}</span>
        </header>
        <p class="muted">{pendingLogSummary}</p>
        <div class="table-like muted">
          {#if !pendingLogRows.length}
            <article class="table-like-row">
              <div class="name">No structured events yet</div>
              <div class="detail">Raw log tail below updates as the worker emits lines.</div>
            </article>
          {:else}
            {#each pendingLogRows as entry}
              {#if entry.event === "playground.node"}
                <article class="table-like-row">
                  <div class="name">{entry.operator || "node"} · {entry.status || "running"}</div>
                  <div class="detail">cache={entry.cache_source || "-"} · {Number(entry.duration_s || 0).toFixed(3)}s · {String(entry.node_id || "").slice(0, 12)}</div>
                </article>
              {:else}
                <article class="table-like-row">
                  <div class="name">{entry.event || "event"}</div>
                  <div class="detail">{entry.message || JSON.stringify(entry)}</div>
                </article>
              {/if}
            {/each}
          {/if}
        </div>
        <pre class="mono-scroll compact-log">{pendingLogRaw || "Waiting for log output..."}</pre>
      </section>
    {/if}

    {#if diagnosticsText()}
      <div class="inline-error">{diagnosticsText()}</div>
    {:else if errorText}
      <div class="inline-error">{errorText}</div>
    {/if}
  </article>
</section>
