<script>
  import { onDestroy, onMount } from "svelte";
  import {
    createPlaygroundJob,
    getPlaygroundJob,
    killPlaygroundJob,
    listPlaygroundJobs,
    listProgramFiles,
    loadProgramFile,
    getProgramSymbols,
    resolvePlaygroundValue,
    resolvePlaygroundValuePage,
  } from "$lib/api/client.js";
  import { fmtBytes, fmtPercent, fmtSeconds } from "$lib/utils/format.js";
  import { buildExecutionLogRows, buildQueueSnapshot } from "$lib/utils/logs.js";
  import {
    buildFailureDetailsText,
    buildPlayTargets,
    formatExecutionErrors,
    normalizedExecutionErrors,
    summarizeDescriptor,
  } from "$lib/utils/playground-value.js";
  import {
    expressionContextAt,
    extractTokenAt,
    extractTokenInfoAt,
    isOperatorToken,
    renderTokenOverlayHtml,
    textIndexFromPoint,
  } from "$lib/utils/token-editor.js";
  import { sanitizeAttr, sanitizeText } from "$lib/utils/sanitize.js";
  import StatusChip from "$lib/components/shared/StatusChip.svelte";

  export let active = false;
  export let capabilities = {};
  const DEFAULT_LIBRARY_PROGRAM_NAME = "threshold_sweep.imgql";

  const INITIAL_PROGRAM = `// Paste or load an example.
import "simpleitk"
x = 2 + 3
y = x * 4`;

  let programText = INITIAL_PROGRAM;

  let programFiles = [];
  let selectedProgramPath = "";
  let programLibraryMeta = "";
  let autoLoadedLibraryProgram = false;

  let symbolDiagnostics = [];
  let precomputedSymbolTable = {};
  let precomputedPrintTargets = [];
  let latestGoalResults = [];
  let latestSymbolTable = {};
  let symbolRefreshToken = 0;
  let symbolRefreshTimer = null;

  let runError = "";
  let jobStatus = "idle";
  let currentJobId = "";
  let currentPlayJob = null;
  let latestPlayJobs = [];

  let metricWall = "-";
  let metricCpu = "-";
  let metricCpuUtil = "-";
  let metricHeapPeak = "-";
  let metricRssDelta = "-";
  let metricJobId = "-";

  let executionOutput = "";
  let taskGraphOutput = "";

  let playResultMeta = "Click inspect on a variable to resolve it on demand.";
  let playTargets = [];
  let selectedTargetIndex = -1;

  let playExecSummary = "No execution trace yet.";
  let playExecRaw = "";
  let playExecRows = [];

  let recentJobsError = "";

  let hoverPreviewMessage = "Hover a variable to see cached value status, or hover an operator to inspect expression context.";
  let hoverActiveToken = "";
  let hoverPreviewToken = "";
  let hoverPreviewSeq = 0;
  let lastHoverToken = "";
  let hoverTimer = null;

  let programInputEl;
  let programOverlayEl;

  let playResultInspectorEl;
  let daskQueueVizEl;
  let playResultViewer = null;
  let queueVisualizer = null;

  let pollTimer = null;
  let valuePollTimer = null;
  let pendingValueRequest = null;
  let activeRefreshTimer = null;

  const formatDiagnostics = (diagnostics) => {
    const rows = Array.isArray(diagnostics) ? diagnostics : [];
    if (!rows.length) return "";
    return rows
      .map((diag) => {
        const code = diag?.code ? `[${diag.code}] ` : "";
        const message = diag?.message ? String(diag.message) : "Static error";
        const symbol = diag?.symbol ? ` (symbol: ${diag.symbol})` : "";
        const location = diag?.location ? ` @ ${diag.location}` : "";
        return `${code}${message}${symbol}${location}`;
      })
      .join("\n");
  };

  $: diagnosticsText = formatDiagnostics(symbolDiagnostics);

  const applyRuntimeMetrics = (job) => {
    const metrics = job?.metrics || {};
    metricWall = fmtSeconds(Number(metrics.wall_time_s));
    metricCpu = fmtSeconds(Number(metrics.cpu_time_s));
    metricCpuUtil = fmtPercent(Number(metrics.cpu_utilization));
    metricHeapPeak = fmtBytes(Number(metrics.python_heap_peak_bytes));
    metricRssDelta = fmtBytes(Number(metrics.ru_maxrss_delta_bytes));
    metricJobId = job?.job_id ? String(job.job_id).slice(0, 12) : "-";
  };

  const setJobStatus = (status) => {
    jobStatus = String(status || "idle").toLowerCase();
  };

  const clearRunError = () => {
    runError = "";
  };

  const stopJobPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const stopValuePolling = () => {
    if (valuePollTimer) {
      clearInterval(valuePollTimer);
      valuePollTimer = null;
    }
    pendingValueRequest = null;
  };

  const parseSelectableTargets = () => {
    const previous = playTargets[selectedTargetIndex];
    playTargets = buildPlayTargets({
      precomputedPrintTargets,
      latestGoalResults,
      precomputedSymbolTable,
      latestSymbolTable,
    });

    if (!playTargets.length) {
      selectedTargetIndex = -1;
      return;
    }

    if (previous) {
      const idx = playTargets.findIndex(
        (target) =>
          target.kind === previous.kind &&
          target.label === previous.label &&
          target.nodeId === previous.nodeId,
      );
      selectedTargetIndex = idx >= 0 ? idx : 0;
      return;
    }

    selectedTargetIndex = 0;
  };

  const ensurePlayResultViewer = () => {
    if (playResultViewer || !playResultInspectorEl) return;

    const ctor = window.VoxResultViewer?.ResultViewer;
    if (typeof ctor === "function") {
      playResultViewer = new ctor(playResultInspectorEl, {
        onNavigate: (path) => {
          const selected = playTargets[selectedTargetIndex];
          if (!selected?.nodeId) return;
          inspectPlayTarget(selected.nodeId, path || "", {
            variable: selected.kind === "variable" ? selected.label : "",
          });
        },
        fetchPage: ({ nodeId, path, offset, limit }) => {
          const selected = playTargets[selectedTargetIndex];
          const variable = selected?.kind === "variable" ? selected.label : "";
          return resolvePlaygroundValuePage({
            program: programText,
            nodeId: nodeId || selected?.nodeId || "",
            variable,
            path: path || "",
            offset: Number(offset || 0),
            limit: Number(limit || 64),
            enqueue: true,
          });
        },
        onStatusClick: (record) => {
          if (!record || (record.status !== "failed" && record.status !== "killed")) return;
          renderPlayFailureDiagnostics(record, {
            nodeId: record.node_id || "",
            path: record.path || "",
          });
        },
      });
      return;
    }

    playResultViewer = {
      setLoading: (message) => {
        playResultInspectorEl.textContent = message || "Loading...";
      },
      setError: (message) => {
        playResultInspectorEl.textContent = message || "Viewer error";
      },
      renderRecord: (record) => {
        playResultInspectorEl.textContent = JSON.stringify(record || {}, null, 2);
      },
    };
  };

  const ensureQueueVisualizer = () => {
    if (queueVisualizer || !daskQueueVizEl) return;

    const ctor = window.VoxDaskQueueViz?.DaskQueueVisualizer;
    if (typeof ctor === "function") {
      queueVisualizer = new ctor(daskQueueVizEl);
      return;
    }

    queueVisualizer = {
      render: (snapshot) => {
        const counts = snapshot?.counts || {};
        daskQueueVizEl.textContent =
          `queue: queued ${Number(counts.queued || 0)} | running ${Number(counts.running || 0)} | ` +
          `completed ${Number(counts.completed || 0)} | failed ${Number(counts.failed || 0)}`;
      },
    };
  };

  const renderQueueVisualizer = () => {
    ensureQueueVisualizer();
    if (!queueVisualizer) return;
    queueVisualizer.render(buildQueueSnapshot(latestPlayJobs, currentPlayJob));
  };

  const applyExecutionPayload = async (job) => {
    const result = job?.result || {};
    executionOutput = JSON.stringify(result, null, 2);
    taskGraphOutput = result.task_graph || "(no task graph in payload)";

    if (Array.isArray(result.goal_results)) {
      latestGoalResults = result.goal_results;
    }
    if (result.symbol_table && typeof result.symbol_table === "object") {
      latestSymbolTable = result.symbol_table;
    }

    const { raw, summaryText, rows } = buildExecutionLogRows(job);
    playExecRaw = raw;
    playExecSummary = summaryText;
    playExecRows = rows;
    parseSelectableTargets();

    if (playTargets.length && selectedTargetIndex >= 0) {
      const target = playTargets[selectedTargetIndex];
      await inspectPlayTarget(target.nodeId, "", {
        variable: target.kind === "variable" ? target.label : "",
      });
    }

    renderQueueVisualizer();
  };

  const refreshProgramLibrary = async () => {
    if (capabilities.playground_program_library === false) {
      programFiles = [];
      selectedProgramPath = "";
      programLibraryMeta = "Program library endpoint unavailable on this backend.";
      return;
    }

    try {
      const payload = await listProgramFiles();
      if (!payload.available) {
        programFiles = [];
        selectedProgramPath = "";
        programLibraryMeta = payload.error || "Program library unavailable.";
        return;
      }

      programFiles = payload.files || [];
      if (!programFiles.length) {
        selectedProgramPath = "";
        programLibraryMeta = `${payload.load_dir} | 0 files`;
        return;
      }

      if (!selectedProgramPath || !programFiles.some((entry) => entry.path === selectedProgramPath)) {
        const preferred = programFiles.find((entry) => {
          const candidate = String(entry?.path || "");
          return candidate === DEFAULT_LIBRARY_PROGRAM_NAME || candidate.endsWith(`/${DEFAULT_LIBRARY_PROGRAM_NAME}`);
        });
        selectedProgramPath = preferred?.path || programFiles[0].path;
      }
      programLibraryMeta = `${payload.load_dir} | ${programFiles.length} file(s)`;
    } catch (error) {
      programFiles = [];
      selectedProgramPath = "";
      programLibraryMeta = `Unable to load library: ${error.message}`;
    }
  };

  const loadFromLibrary = async () => {
    if (!selectedProgramPath) return;

    try {
      const payload = await loadProgramFile(selectedProgramPath);
      programText = payload.content || "";
      programLibraryMeta = `${payload.path} | ${fmtBytes(Number(payload.bytes || 0))}`;
      latestGoalResults = [];
      latestSymbolTable = {};
      renderEditorTokenOverlay();
      await refreshProgramSymbols();
      clearRunError();
    } catch (error) {
      programLibraryMeta = `Unable to load ${selectedProgramPath}: ${error.message}`;
    }
  };

  const refreshProgramSymbols = async () => {
    if (capabilities.playground_symbols === false) return;

    const token = symbolRefreshToken + 1;
    symbolRefreshToken = token;

    if (!String(programText || "").trim()) {
      precomputedSymbolTable = {};
      precomputedPrintTargets = [];
      symbolDiagnostics = [];
      parseSelectableTargets();
      renderEditorTokenOverlay();
      return;
    }

    try {
      const payload = await getProgramSymbols(programText);
      if (token !== symbolRefreshToken) return;

      const available = payload?.available !== false;
      precomputedSymbolTable = available ? payload.symbol_table || {} : {};
      precomputedPrintTargets = available ? payload.print_targets || [] : [];
      symbolDiagnostics = payload?.diagnostics || [];
      parseSelectableTargets();
      renderEditorTokenOverlay();
    } catch (error) {
      if (token !== symbolRefreshToken) return;
      precomputedSymbolTable = {};
      precomputedPrintTargets = [];
      symbolDiagnostics = [{ code: "E_SYMBOLS", message: `Unable to refresh symbols: ${error.message}` }];
      parseSelectableTargets();
      renderEditorTokenOverlay();
    }
  };

  const scheduleSymbolRefresh = () => {
    if (symbolRefreshTimer) {
      clearTimeout(symbolRefreshTimer);
    }
    symbolRefreshTimer = setTimeout(() => {
      refreshProgramSymbols();
    }, 260);
  };

  const requestPlayValue = ({ nodeId = "", variable = "", path = "", enqueue = true }) =>
    resolvePlaygroundValue({
      program: programText,
      nodeId,
      variable,
      path,
      enqueue,
    });

  const renderPlayFailureDiagnostics = (payload, requestState) => {
    const nodeId = String(payload.node_id || requestState.nodeId || "");
    const path = payload.path || requestState.path || "";
    const computeStatus = String(payload.compute_status || payload.status || "failed").toLowerCase();
    const badgeStatus = computeStatus === "killed" ? "killed" : "failed";
    const detailsText = buildFailureDetailsText(payload, requestState);
    const primaryError = String(payload.error || "Value inspection failed.");

    playResultViewer.renderRecord({
      available: false,
      node_id: nodeId,
      status: badgeStatus,
      runtime_version: payload.runtime_version || "runtime",
      updated_at: payload.updated_at || new Date().toISOString(),
      path,
      error: primaryError,
      log_tail: payload.log_tail || "",
      execution_errors: normalizedExecutionErrors(payload),
      descriptor: {
        vox_type: "string",
        format_version: "voxpod/1",
        summary: { value: detailsText, length: detailsText.length, truncated: false },
        navigation: {
          path: path || "",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
      diagnostics: payload.diagnostics || {},
    });

    playResultMeta = `Unable to inspect ${nodeId.slice(0, 12)}${path ? ` @ ${path}` : ""}: ${primaryError}`;

    if (payload.log_tail) {
      const { raw, summaryText, rows } = buildExecutionLogRows({
        log_tail: payload.log_tail,
        result: { execution: { cache_summary: payload?.cache_summary || payload?.diagnostics?.cache_summary || {} } },
      });
      playExecRaw = raw;
      playExecSummary = summaryText;
      playExecRows = rows;
      return;
    }

    playExecRaw = "";
    playExecSummary = "No execution log available for this failure.";
    playExecRows = [];
  };

  const applyPlayValuePayload = (payload, requestState) => {
    const materialization = String(payload.materialization || "");
    const computeStatus = String(payload.compute_status || "");
    const statusName = String(payload.status || "");
    const nodeId = String(payload.node_id || requestState.nodeId || "");
    const path = payload.path || requestState.path || "";
    const jobId = payload.job_id ? String(payload.job_id) : "";

    const descriptorKind = payload?.descriptor && typeof payload.descriptor === "object" ? String(payload.descriptor.vox_type || "") : "";
    const hasRenderableDescriptor = payload?.descriptor && descriptorKind && descriptorKind !== "unavailable" && descriptorKind !== "error";
    const isMaterialized = materialization === "cached" || materialization === "computed" || statusName === "materialized";

    if (isMaterialized && hasRenderableDescriptor) {
      stopValuePolling();
      playResultViewer.renderRecord(payload);
      playResultMeta = `Inspecting ${nodeId.slice(0, 12)}${path ? ` @ ${path}` : ""} | ${materialization || "materialized"} | status=${computeStatus || "materialized"}`;
      return;
    }

    if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed" || statusName === "failed") {
      stopValuePolling();
      renderPlayFailureDiagnostics(payload, requestState);
      return;
    }

    if (materialization === "pending" || computeStatus === "queued" || computeStatus === "running" || computeStatus === "persisting") {
      playResultViewer.setLoading(`Value ${nodeId.slice(0, 12)} ${path || ""} is ${computeStatus || "pending"}...`);
      playResultMeta = `Waiting for ${nodeId.slice(0, 12)}${path ? ` @ ${path}` : ""} | ${computeStatus || "pending"}${jobId ? ` | job ${jobId.slice(0, 12)}` : ""}`;

      if (!valuePollTimer) {
        pendingValueRequest = { nodeId, variable: requestState.variable || "", path };
        valuePollTimer = setInterval(async () => {
          if (!pendingValueRequest) return;
          try {
            const polled = await requestPlayValue({
              nodeId: pendingValueRequest.nodeId,
              variable: pendingValueRequest.variable,
              path: pendingValueRequest.path,
              enqueue: false,
            });
            applyPlayValuePayload(polled, pendingValueRequest);
          } catch (error) {
            playResultViewer.setError(`Polling value failed: ${error.message}`);
            stopValuePolling();
          }
        }, 900);
      }
      return;
    }

    stopValuePolling();
    const fallbackError =
      payload.error ||
      (materialization === "missing"
        ? "Value is not materialized yet."
        : `Unable to inspect value (${materialization || computeStatus || "unknown"}).`);
    renderPlayFailureDiagnostics(
      {
        ...payload,
        error: fallbackError,
        compute_status: computeStatus || "failed",
        materialization: materialization || "failed",
        status: payload.status || "failed",
      },
      requestState,
    );
  };

  const inspectPlayTarget = async (nodeId, path = "", options = {}) => {
    ensurePlayResultViewer();
    stopValuePolling();

    const variable = String(options.variable || "");
    playResultViewer.setLoading(`Loading value for ${nodeId} ...`);

    try {
      const payload = await requestPlayValue({
        nodeId,
        variable,
        path,
        enqueue: options.enqueue !== false,
      });
      applyPlayValuePayload(payload, { nodeId, variable, path });
    } catch (error) {
      stopValuePolling();
      playResultViewer.setError(`Value lookup failed: ${error.message}`);
      playResultMeta = `Unable to inspect ${nodeId.slice(0, 12)}: ${error.message}`;
    }
  };

  const refreshJobList = async () => {
    try {
      const payload = await listPlaygroundJobs();
      latestPlayJobs = payload.jobs || [];
      recentJobsError = "";

      if (!currentPlayJob && latestPlayJobs.length) {
        currentPlayJob = latestPlayJobs[0];
      }

      renderQueueVisualizer();
    } catch (error) {
      latestPlayJobs = [];
      recentJobsError = `Failed to load jobs: ${error.message}`;
      renderQueueVisualizer();
    }
  };

  const onJobTerminal = async (job) => {
    currentPlayJob = job || null;
    setJobStatus(job?.status);
    applyRuntimeMetrics(job);
    await applyExecutionPayload(job);
    runError = job?.error || "";
    await refreshJobList();
  };

  const pollCurrentJob = async () => {
    if (!currentJobId) return;

    try {
      const payload = await getPlaygroundJob(currentJobId);
      currentPlayJob = payload || null;
      setJobStatus(payload.status);
      applyRuntimeMetrics(payload);

      const { raw, summaryText, rows } = buildExecutionLogRows(payload);
      playExecRaw = raw;
      playExecSummary = summaryText;
      playExecRows = rows;
      renderQueueVisualizer();

      if (payload.status === "running") {
        return;
      }

      stopJobPolling();
      await onJobTerminal(payload);
    } catch (error) {
      stopJobPolling();
      setJobStatus("failed");
      runError = `Polling failed: ${error.message}`;
    }
  };

  const runProgram = async () => {
    clearRunError();
    await refreshProgramSymbols();

    if (symbolDiagnostics.length) {
      runError = "Static diagnostics must be fixed before execution can start.";
      return;
    }

    try {
      setJobStatus("running");
      stopValuePolling();
      executionOutput = "";
      taskGraphOutput = "";
      playExecRaw = "";
      playExecRows = [];
      playExecSummary = "Execution started. Waiting for log events...";
      applyRuntimeMetrics(null);

      const payload = await createPlaygroundJob(programText);
      currentJobId = payload.job_id;
      metricJobId = String(payload.job_id || "").slice(0, 12);

      stopJobPolling();
      pollTimer = setInterval(pollCurrentJob, 800);
      await pollCurrentJob();
    } catch (error) {
      setJobStatus("failed");
      runError = error.message;
    }
  };

  const clearOutput = () => {
    executionOutput = "";
    taskGraphOutput = "";
    runError = "";
    applyRuntimeMetrics(null);

    playExecSummary = "No execution log yet.";
    playExecRaw = "";
    playExecRows = [];

    latestGoalResults = [];
    latestSymbolTable = {};
    currentPlayJob = null;

    stopValuePolling();
    playResultMeta = "Click inspect on a variable to resolve it on demand.";
    ensurePlayResultViewer();
    playResultViewer.renderRecord(null);

    parseSelectableTargets();
    selectedTargetIndex = playTargets.length ? 0 : -1;
    renderQueueVisualizer();
  };

  const killCurrentRun = async () => {
    if (!currentJobId) return;

    try {
      const payload = await killPlaygroundJob(currentJobId);
      stopJobPolling();
      await onJobTerminal(payload);
    } catch (error) {
      runError = `Kill failed: ${error.message}`;
    }
  };

  const openPlayJob = async (jobId) => {
    if (!jobId) return;

    try {
      const payload = await getPlaygroundJob(jobId);
      currentJobId = jobId;
      currentPlayJob = payload || null;
      setJobStatus(payload.status);
      applyRuntimeMetrics(payload);
      await applyExecutionPayload(payload);

      if (payload.status === "running") {
        stopJobPolling();
        pollTimer = setInterval(pollCurrentJob, 800);
      }

      const executionErrors = payload?.result?.execution?.errors;
      if ((payload.status === "failed" || payload.status === "killed") && executionErrors && typeof executionErrors === "object") {
        runError = [payload.error || "Execution failed", formatExecutionErrors(executionErrors)].filter(Boolean).join("\n");
      } else {
        runError = payload.error || "";
      }
    } catch (error) {
      runError = `Unable to open ${jobId}: ${error.message}`;
    }
  };

  const killJob = async (jobId) => {
    try {
      await killPlaygroundJob(jobId);
      if (currentJobId === jobId) {
        stopJobPolling();
        setJobStatus("killed");
      }
      await refreshJobList();
    } catch (error) {
      runError = `Unable to kill ${jobId}: ${error.message}`;
    }
  };

  const onSelectedTargetChange = async () => {
    const normalized = Number(selectedTargetIndex);
    if (!Number.isFinite(normalized)) return;
    selectedTargetIndex = normalized;
    if (selectedTargetIndex < 0 || selectedTargetIndex >= playTargets.length) return;
    const target = playTargets[selectedTargetIndex];
    await inspectPlayTarget(target.nodeId, "", {
      variable: target.kind === "variable" ? target.label : "",
    });
  };

  const resolveSymbolNode = (token) => precomputedSymbolTable[token] || latestSymbolTable[token] || "";

  const syncOverlayScroll = () => {
    if (!programOverlayEl || !programInputEl) return;
    programOverlayEl.scrollTop = programInputEl.scrollTop;
    programOverlayEl.scrollLeft = programInputEl.scrollLeft;
  };

  const setOverlayActiveToken = (token) => {
    if (!programOverlayEl) return;
    const encoded = encodeURIComponent(String(token || ""));
    programOverlayEl.querySelectorAll(".editor-token-hit").forEach((element) => {
      if (!(element instanceof HTMLElement)) return;
      if (token && element.dataset.token === encoded) {
        element.classList.add("is-active");
      } else {
        element.classList.remove("is-active");
      }
    });
  };

  const setHoverPreview = (message) => {
    hoverPreviewMessage = message || "";
  };

  const clearHoverState = () => {
    hoverActiveToken = "";
    hoverPreviewToken = "";
    setOverlayActiveToken("");
    setHoverPreview("Hover a variable to see cached value status, or hover an operator to inspect expression context.");
  };

  const setHoverState = (token) => {
    hoverActiveToken = token || "";
    setOverlayActiveToken(token || "");
  };

  const renderEditorTokenOverlay = () => {
    if (!programOverlayEl) return;
    programOverlayEl.innerHTML = renderTokenOverlayHtml(
      String(programText || ""),
      resolveSymbolNode,
      sanitizeText,
      sanitizeAttr,
    );
    syncOverlayScroll();
    if (hoverActiveToken) {
      setOverlayActiveToken(hoverActiveToken);
    }
  };

  const inspectSymbolToken = async (token) => {
    const nodeId = resolveSymbolNode(token);
    if (!nodeId) return;

    clearHoverState();
    const idx = playTargets.findIndex((target) => target.kind === "variable" && target.label === token);
    if (idx >= 0) {
      selectedTargetIndex = idx;
    }

    await inspectPlayTarget(nodeId, "", { variable: token });
  };

  const previewVariableHover = async (token) => {
    const seq = hoverPreviewSeq + 1;
    hoverPreviewSeq = seq;

    setHoverPreview(`var ${token}: checking cache...`);
    try {
      const payload = await requestPlayValue({ variable: token, enqueue: false });
      if (seq !== hoverPreviewSeq || hoverActiveToken !== token) return;

      const materialization = String(payload.materialization || "");
      if (payload.descriptor && (materialization === "cached" || materialization === "computed")) {
        setHoverPreview(`var ${token}: ${summarizeDescriptor(payload.descriptor)} (${materialization})`);
        return;
      }

      if (
        materialization === "pending" ||
        payload.compute_status === "running" ||
        payload.compute_status === "queued" ||
        payload.compute_status === "persisting"
      ) {
        setHoverPreview(`var ${token}: running (${payload.compute_status}). Click to focus/inspect.`);
        return;
      }

      setHoverPreview(`var ${token}: not materialized yet. Click to compute on demand.`);
    } catch {
      if (seq !== hoverPreviewSeq || hoverActiveToken !== token) return;
      setHoverPreview(`var ${token}: click to compute on demand.`);
    }
  };

  const handleProgramHover = (event) => {
    if (!active) return;

    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }

    hoverTimer = setTimeout(() => {
      const position = textIndexFromPoint(programInputEl, event.clientX, event.clientY);
      if (!Number.isInteger(position)) {
        clearHoverState();
        return;
      }

      const info = extractTokenInfoAt(programText || "", position);
      if (!info.token) {
        clearHoverState();
        return;
      }

      const nodeId = resolveSymbolNode(info.token);
      if (!nodeId && !isOperatorToken(info.token)) {
        clearHoverState();
        return;
      }

      lastHoverToken = info.token;
      if (nodeId) {
        setHoverState(info.token);
        if (hoverPreviewToken !== info.token) {
          hoverPreviewToken = info.token;
          previewVariableHover(info.token);
        }
        return;
      }

      setHoverState("");
      hoverPreviewToken = info.token;
      const context = expressionContextAt(programText || "", info.start);
      setHoverPreview(context ? `operator '${info.token}' in: ${context}` : `operator '${info.token}'`);
    }, 140);
  };

  const inspectTokenWithRefresh = async (token) => {
    if (!token) return false;

    let resolved = resolveSymbolNode(token) ? token : "";
    if (!resolved) {
      await refreshProgramSymbols();
      const selectedToken = extractTokenAt(programText || "", Number(programInputEl?.selectionStart || 0));
      resolved = resolveSymbolNode(token) ? token : resolveSymbolNode(selectedToken) ? selectedToken : "";
      if (!resolved && lastHoverToken && resolveSymbolNode(lastHoverToken)) {
        resolved = lastHoverToken;
      }
    }

    if (!resolved) return false;
    lastHoverToken = resolved;
    await inspectSymbolToken(resolved);
    return true;
  };

  const handleProgramInput = () => {
    latestSymbolTable = {};
    latestGoalResults = [];
    renderEditorTokenOverlay();
    scheduleSymbolRefresh();
    clearHoverState();
  };

  const setupOverlayEvents = () => {
    if (!programOverlayEl) return;

    const onMouseDown = (event) => {
      const tokenEl = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!tokenEl) return;
      event.preventDefault();
    };

    const onMouseOver = (event) => {
      const tokenEl = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!tokenEl) return;

      const token = decodeURIComponent(String(tokenEl.getAttribute("data-token") || ""));
      if (!token) return;

      lastHoverToken = token;
      setHoverState(token);
      if (hoverPreviewToken !== token) {
        hoverPreviewToken = token;
        previewVariableHover(token);
      }
    };

    const onMouseOut = (event) => {
      const fromToken = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!fromToken) return;
      const toToken = event.relatedTarget instanceof Element ? event.relatedTarget.closest(".editor-token-hit") : null;
      if (toToken) return;

      lastHoverToken = "";
      clearHoverState();
    };

    const onClick = async (event) => {
      const tokenEl = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!tokenEl) return;
      event.preventDefault();

      const token = decodeURIComponent(String(tokenEl.getAttribute("data-token") || ""));
      if (!token) return;
      await inspectTokenWithRefresh(token);
      programInputEl?.focus({ preventScroll: true });
    };

    programOverlayEl.addEventListener("mousedown", onMouseDown);
    programOverlayEl.addEventListener("mouseover", onMouseOver);
    programOverlayEl.addEventListener("mouseout", onMouseOut);
    programOverlayEl.addEventListener("click", onClick);

    return () => {
      programOverlayEl.removeEventListener("mousedown", onMouseDown);
      programOverlayEl.removeEventListener("mouseover", onMouseOver);
      programOverlayEl.removeEventListener("mouseout", onMouseOut);
      programOverlayEl.removeEventListener("click", onClick);
    };
  };

  const selectJobFromChip = async (jobId) => {
    await openPlayJob(jobId);
  };

  const startActiveRefresh = () => {
    if (activeRefreshTimer) return;
    activeRefreshTimer = setInterval(() => {
      if (!active) return;
      refreshJobList();
      refreshProgramSymbols();
      pollCurrentJob();
    }, 15000);
  };

  const stopActiveRefresh = () => {
    if (!activeRefreshTimer) return;
    clearInterval(activeRefreshTimer);
    activeRefreshTimer = null;
  };

  export async function loadProgram(code, runAfterLoad = false) {
    programText = String(code || "");
    latestSymbolTable = {};
    latestGoalResults = [];
    renderEditorTokenOverlay();
    await refreshProgramSymbols();
    if (runAfterLoad) {
      await runProgram();
    }
  }

  export async function runCurrentProgram() {
    await runProgram();
  }

  $: if (active) {
    refreshJobList();
    refreshProgramSymbols();
    startActiveRefresh();
  } else {
    stopActiveRefresh();
  }

  $: if (capabilities.playground_program_library === false) {
    programLibraryMeta = "Program library endpoint unavailable on this backend.";
  }

  onMount(() => {
    ensurePlayResultViewer();
    ensureQueueVisualizer();
    renderQueueVisualizer();

    const cleanupOverlay = setupOverlayEvents();
    renderEditorTokenOverlay();

    void (async () => {
      await refreshProgramLibrary();
      if (!autoLoadedLibraryProgram && selectedProgramPath) {
        autoLoadedLibraryProgram = true;
        await loadFromLibrary();
      }
      await Promise.all([refreshJobList(), refreshProgramSymbols()]);
    })();

    return () => {
      cleanupOverlay?.();
    };
  });

  onDestroy(() => {
    stopJobPolling();
    stopValuePolling();
    stopActiveRefresh();

    if (symbolRefreshTimer) {
      clearTimeout(symbolRefreshTimer);
    }
    if (hoverTimer) {
      clearTimeout(hoverTimer);
    }

    if (queueVisualizer && typeof queueVisualizer.destroy === "function") {
      queueVisualizer.destroy();
    }
    if (playResultViewer && typeof playResultViewer.destroy === "function") {
      playResultViewer.destroy();
    }
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-playground">
  <div class="grid-two">
    <article class="card editor-card">
      <div class="card-header">
        <h2>Interactive Playground</h2>
        <div class="row gap-s">
          <label class="inline-control" for="executionStrategy">Strategy</label>
          <select id="executionStrategy" class="select" disabled>
            <option value="dask" selected>dask</option>
          </select>
        </div>
      </div>

      <div id="editorShell" class="editor-shell">
        <textarea
          id="programInput"
          class="code-input"
          spellcheck="false"
          bind:this={programInputEl}
          bind:value={programText}
          on:input={handleProgramInput}
          on:mousemove={handleProgramHover}
          on:mouseleave={() => {
            lastHoverToken = "";
            clearHoverState();
            if (hoverTimer) {
              clearTimeout(hoverTimer);
              hoverTimer = null;
            }
          }}
          on:scroll={syncOverlayScroll}
        ></textarea>
        <pre class="editor-token-overlay" bind:this={programOverlayEl} aria-hidden="true"></pre>
      </div>

      <div class="row controls">
        <label class="inline-control" for="programLibrarySelect">Program Library</label>
        <select id="programLibrarySelect" class="select" bind:value={selectedProgramPath} disabled={!programFiles.length}>
          {#if !programFiles.length}
            <option value="">{capabilities.playground_program_library === false ? "unavailable" : "no .imgql files found"}</option>
          {:else}
            {#each programFiles as entry}
              <option value={entry.path}>{entry.path}</option>
            {/each}
          {/if}
        </select>
        <button id="loadProgramFileBtn" class="btn btn-ghost btn-small" type="button" on:click={loadFromLibrary} disabled={!programFiles.length}>Load from Library</button>
      </div>

      <p id="programLibraryMeta" class="muted">{programLibraryMeta}</p>
      <p class="muted">
        Variables are directly clickable in the editor. Hover changes appearance, click resolves on demand with
        priority even when no full run has been launched.
      </p>

      <div id="editorHoverPreview" class="editor-hover-preview muted">{hoverPreviewMessage}</div>

      {#if diagnosticsText}
        <div id="programDiagnostics" class="inline-error">{diagnosticsText}</div>
      {/if}

      <div class="row controls">
        <button id="runProgramBtn" class="btn btn-primary hidden" type="button" on:click={runProgram}>Run Program</button>
        <button id="killProgramBtn" class="btn btn-danger" type="button" disabled={jobStatus !== "running"} on:click={killCurrentRun}>Kill Running Job</button>
        <button id="clearOutputBtn" class="btn btn-ghost" type="button" on:click={clearOutput}>Clear Output</button>
        <StatusChip value={jobStatus} />
      </div>

      {#if runError}
        <div id="runError" class="inline-error">{runError}</div>
      {/if}
    </article>

    <article class="card telemetry-card">
      <div class="card-header">
        <h2>Runtime Telemetry</h2>
        <button id="refreshJobsBtn" class="btn btn-ghost btn-small" type="button" on:click={refreshJobList}>Refresh</button>
      </div>

      <div class="stats-grid">
        <div class="stat"><span class="label">Wall Time</span><strong id="metricWall">{metricWall}</strong></div>
        <div class="stat"><span class="label">CPU Time</span><strong id="metricCpu">{metricCpu}</strong></div>
        <div class="stat"><span class="label">CPU Util.</span><strong id="metricCpuUtil">{metricCpuUtil}</strong></div>
        <div class="stat"><span class="label">Peak Python Heap</span><strong id="metricHeapPeak">{metricHeapPeak}</strong></div>
        <div class="stat"><span class="label">RSS Delta</span><strong id="metricRssDelta">{metricRssDelta}</strong></div>
        <div class="stat"><span class="label">Current Job</span><strong id="metricJobId">{metricJobId}</strong></div>
      </div>

      <h3 class="subheading">Recent Jobs</h3>
      <div id="recentJobs" class="job-list">
        {#if recentJobsError}
          <div class="muted">{recentJobsError}</div>
        {:else if !latestPlayJobs.length}
          <div class="muted">No jobs executed yet.</div>
        {:else}
          {#each latestPlayJobs as job}
            {@const status = String(job.status || "unknown")}
            {@const failedBadge = status === "failed" || status === "killed"}
            <article class="job-item">
              <div class="job-row">
                <strong>{String(job.job_id || "").slice(0, 12)}</strong>
                {#if failedBadge}
                  <button
                    type="button"
                    class={`chip ${status} chip-clickable`}
                    on:click={() => selectJobFromChip(job.job_id)}
                  >
                    {status}
                  </button>
                {:else}
                  <span class={`chip ${status}`}>{status}</span>
                {/if}
              </div>
              <div class="job-row">
                <span>{job?.request?.execution_strategy || "dask"}</span>
                <span>{fmtSeconds(Number(job?.metrics?.wall_time_s))}</span>
              </div>
              <div class="job-row">
                <span>{job.created_at || "-"}</span>
                <div class="row gap-s">
                  <button class="btn btn-ghost btn-small" type="button" on:click={() => openPlayJob(job.job_id)}>Open</button>
                  {#if status === "running"}
                    <button class="btn btn-danger btn-small" type="button" on:click={() => killJob(job.job_id)}>Kill</button>
                  {/if}
                </div>
              </div>
            </article>
          {/each}
        {/if}
      </div>

      <h3 class="subheading">Dask Queue Flux</h3>
      <div id="daskQueueViz" class="dask-queue-viz" bind:this={daskQueueVizEl}></div>

      <h3 class="subheading">Focused Result</h3>
      <div class="row gap-s controls">
        <label class="inline-control" for="playResultSelector">Target</label>
        <select id="playResultSelector" class="select" bind:value={selectedTargetIndex} disabled={!playTargets.length} on:change={onSelectedTargetChange}>
          {#if !playTargets.length}
            <option value="">No print labels or variables available</option>
          {:else}
            {#each playTargets as target, idx}
              <option value={idx}>{target.kind === "print" ? `print: ${target.label}` : `var: ${target.label}`}</option>
            {/each}
          {/if}
        </select>
      </div>

      <p id="playResultMeta" class="muted">{playResultMeta}</p>
      <div id="playResultInspector" class="result-inspector compact" bind:this={playResultInspectorEl}></div>

      <h3 class="subheading">Execution Log</h3>
      <p id="playExecSummary" class="muted">{playExecSummary}</p>
      <div id="playExecLog" class="table-like muted">
        {#if !playExecRows.length}
          <div class="muted">No node events available yet. Raw log is shown below.</div>
        {:else}
          {#each playExecRows as entry}
            {#if entry.event === "playground.node"}
              <article class="table-like-row">
                <div class="name"><span class="mini-chip">{entry.status || "unknown"}</span> {entry.operator || "-"}</div>
                <div class="detail">
                  node={String(entry.node_id || "").slice(0, 12)} | source={entry.cache_source || "-"} |
                  duration={(Number(entry.duration_s || 0) * 1000).toFixed(2)} ms
                  {#if entry.error}
                    | {String(entry.error)}
                  {/if}
                </div>
              </article>
            {:else}
              <article class="table-like-row">
                <div class="name">{entry.event || "event"}</div>
                <div class="detail">{entry.message || entry.error || JSON.stringify(entry).slice(0, 180)}</div>
              </article>
            {/if}
          {/each}
        {/if}
      </div>

      <pre id="playExecRaw" class="mono-scroll compact-log">{playExecRaw}</pre>
    </article>
  </div>

  <div class="grid-two">
    <article class="card">
      <h2>Execution Result</h2>
      <pre id="executionOutput" class="mono-scroll">{executionOutput}</pre>
    </article>
    <article class="card">
      <h2>Task Graph (Text)</h2>
      <pre id="taskGraphOutput" class="mono-scroll">{taskGraphOutput}</pre>
    </article>
  </div>
</section>
