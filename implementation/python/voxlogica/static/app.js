(() => {
  const state = {
    activeTab: "playground",
    currentJobId: null,
    pollTimer: null,
    currentTestJobId: null,
    testPollTimer: null,
    capabilities: {},
    examples: [],
    programFiles: [],
    storeRecords: [],
    currentResultNodeId: null,
    currentResultPath: "",
    resultViewer: null,
    playResultViewer: null,
    playSelectableTargets: [],
    latestGoalResults: [],
    latestSymbolTable: {},
    precomputedSymbolTable: {},
    precomputedPrintTargets: [],
    currentProgramHash: null,
    symbolRefreshToken: 0,
    symbolRefreshTimer: null,
    valuePollTimer: null,
    pendingValueRequest: null,
    hoverTimer: null,
    lastHoverToken: "",
    hoverActiveToken: "",
    hoverPreviewToken: "",
    hoverPreviewSeq: 0,
    symbolDiagnostics: [],
    latestPlayJobs: [],
    currentPlayJob: null,
    queueVisualizer: null,
  };

  const dom = {
    tabs: Array.from(document.querySelectorAll(".tab")),
    panels: Array.from(document.querySelectorAll(".panel")),
    buildStamp: document.getElementById("buildStamp"),
    editorShell: document.getElementById("editorShell"),
    programInput: document.getElementById("programInput"),
    programOverlay: null,
    executionStrategy: document.getElementById("executionStrategy"),
    noCache: document.getElementById("noCache"),
    programLibrarySelect: document.getElementById("programLibrarySelect"),
    loadProgramFileBtn: document.getElementById("loadProgramFileBtn"),
    programLibraryMeta: document.getElementById("programLibraryMeta"),
    editorHoverPreview: document.getElementById("editorHoverPreview"),
    programDiagnostics: document.getElementById("programDiagnostics"),
    runProgramBtn: document.getElementById("runProgramBtn"),
    killProgramBtn: document.getElementById("killProgramBtn"),
    clearOutputBtn: document.getElementById("clearOutputBtn"),
    refreshJobsBtn: document.getElementById("refreshJobsBtn"),
    jobStatus: document.getElementById("jobStatus"),
    runError: document.getElementById("runError"),
    executionOutput: document.getElementById("executionOutput"),
    taskGraphOutput: document.getElementById("taskGraphOutput"),
    recentJobs: document.getElementById("recentJobs"),
    metricWall: document.getElementById("metricWall"),
    metricCpu: document.getElementById("metricCpu"),
    metricCpuUtil: document.getElementById("metricCpuUtil"),
    metricHeapPeak: document.getElementById("metricHeapPeak"),
    metricRssDelta: document.getElementById("metricRssDelta"),
    metricJobId: document.getElementById("metricJobId"),
    playResultSelector: document.getElementById("playResultSelector"),
    playResultMeta: document.getElementById("playResultMeta"),
    playResultInspector: document.getElementById("playResultInspector"),
    playExecSummary: document.getElementById("playExecSummary"),
    playExecLog: document.getElementById("playExecLog"),
    playExecRaw: document.getElementById("playExecRaw"),
    daskQueueViz: document.getElementById("daskQueueViz"),
    moduleFilter: document.getElementById("moduleFilter"),
    galleryCards: document.getElementById("galleryCards"),
    refreshQualityBtn: document.getElementById("refreshQualityBtn"),
    testRunStatus: document.getElementById("testRunStatus"),
    runQuickTestsBtn: document.getElementById("runQuickTestsBtn"),
    runFullTestsBtn: document.getElementById("runFullTestsBtn"),
    runPerfTestsBtn: document.getElementById("runPerfTestsBtn"),
    killTestRunBtn: document.getElementById("killTestRunBtn"),
    testRunLog: document.getElementById("testRunLog"),
    recentTestJobs: document.getElementById("recentTestJobs"),
    testTitles: document.getElementById("testTitles"),
    qTotalTests: document.getElementById("qTotalTests"),
    qPassed: document.getElementById("qPassed"),
    qFailed: document.getElementById("qFailed"),
    qSkipped: document.getElementById("qSkipped"),
    qDuration: document.getElementById("qDuration"),
    qCoverage: document.getElementById("qCoverage"),
    coverageBars: document.getElementById("coverageBars"),
    failedCases: document.getElementById("failedCases"),
    perfChart: document.getElementById("perfChart"),
    perfSummary: document.getElementById("perfSummary"),
    primitivePerfChart: document.getElementById("primitivePerfChart"),
    primitivePerfBars: document.getElementById("primitivePerfBars"),
    refreshStorageBtn: document.getElementById("refreshStorageBtn"),
    sTotal: document.getElementById("sTotal"),
    sMaterialized: document.getElementById("sMaterialized"),
    sFailed: document.getElementById("sFailed"),
    sAvgPayload: document.getElementById("sAvgPayload"),
    sTotalPayload: document.getElementById("sTotalPayload"),
    sDbSize: document.getElementById("sDbSize"),
    storageMeta: document.getElementById("storageMeta"),
    payloadBuckets: document.getElementById("payloadBuckets"),
    runtimeVersions: document.getElementById("runtimeVersions"),
    refreshResultsBtn: document.getElementById("refreshResultsBtn"),
    resultStatusFilter: document.getElementById("resultStatusFilter"),
    resultSearchInput: document.getElementById("resultSearchInput"),
    resultListMeta: document.getElementById("resultListMeta"),
    resultList: document.getElementById("resultList"),
    resultInspector: document.getElementById("resultInspector"),
  };

  const api = async (path, init = {}) => {
    const response = await fetch(path, init);
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    if (!response.ok) {
      const rawDetail = payload.detail;
      const detail =
        rawDetail && typeof rawDetail === "object"
          ? rawDetail.message || rawDetail.code || JSON.stringify(rawDetail)
          : rawDetail || `${response.status} ${response.statusText}`;
      throw new Error(detail);
    }
    return payload;
  };

  const fmtSeconds = (value) => {
    if (!Number.isFinite(value)) return "-";
    if (value < 0.001) return `${(value * 1000).toFixed(2)} ms`;
    return `${value.toFixed(3)} s`;
  };

  const fmtPercent = (value) => {
    if (!Number.isFinite(value)) return "-";
    return `${(value * 100).toFixed(1)}%`;
  };

  const fmtBytes = (value) => {
    if (!Number.isFinite(value) || value < 0) return "-";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value;
    let idx = 0;
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024;
      idx += 1;
    }
    return `${size.toFixed(idx === 0 ? 0 : 2)} ${units[idx]}`;
  };

  const sanitize = (text) =>
    String(text)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  const sanitizeAttr = (text) =>
    String(text)
      .replaceAll("&", "&amp;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");

  const setEditorHoverPreview = (message) => {
    if (!dom.editorHoverPreview) return;
    dom.editorHoverPreview.textContent = message || "";
  };

  const median = (values) => {
    const nums = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
    if (!nums.length) return NaN;
    const mid = Math.floor(nums.length / 2);
    if (nums.length % 2 === 1) return nums[mid];
    return (nums[mid - 1] + nums[mid]) / 2;
  };

  const setJobStatus = (status) => {
    const normalized = String(status || "idle").toLowerCase();
    dom.jobStatus.textContent = normalized;
    dom.jobStatus.classList.remove("neutral", "running", "completed", "failed", "killed");
    if (["running", "completed", "failed", "killed"].includes(normalized)) {
      dom.jobStatus.classList.add(normalized);
    } else {
      dom.jobStatus.classList.add("neutral");
    }
  };

  const setTestRunStatus = (status) => {
    const normalized = String(status || "idle").toLowerCase();
    dom.testRunStatus.textContent = normalized;
    dom.testRunStatus.classList.remove("neutral", "running", "completed", "failed", "killed");
    if (["running", "completed", "failed", "killed"].includes(normalized)) {
      dom.testRunStatus.classList.add(normalized);
    } else {
      dom.testRunStatus.classList.add("neutral");
    }
  };

  const setRunError = (message) => {
    if (!message) {
      dom.runError.textContent = "";
      dom.runError.classList.add("hidden");
      return;
    }
    dom.runError.textContent = message;
    dom.runError.classList.remove("hidden");
  };

  const formatDiagnostics = (diagnostics) => {
    const rows = Array.isArray(diagnostics) ? diagnostics : [];
    if (!rows.length) return "";
    return rows
      .map((diag) => {
        const code = diag && diag.code ? `[${diag.code}] ` : "";
        const message = diag && diag.message ? String(diag.message) : "Static error";
        const symbol = diag && diag.symbol ? ` (symbol: ${diag.symbol})` : "";
        const location = diag && diag.location ? ` @ ${diag.location}` : "";
        return `${code}${message}${symbol}${location}`;
      })
      .join("\n");
  };

  const setProgramDiagnostics = (diagnostics) => {
    state.symbolDiagnostics = Array.isArray(diagnostics) ? diagnostics : [];
    if (!dom.programDiagnostics) return;
    if (!state.symbolDiagnostics.length) {
      dom.programDiagnostics.textContent = "";
      dom.programDiagnostics.classList.add("hidden");
      return;
    }
    dom.programDiagnostics.textContent = formatDiagnostics(state.symbolDiagnostics);
    dom.programDiagnostics.classList.remove("hidden");
  };

  const renderRuntimeMetrics = (job) => {
    const metrics = (job && job.metrics) || {};
    dom.metricWall.textContent = fmtSeconds(Number(metrics.wall_time_s));
    dom.metricCpu.textContent = fmtSeconds(Number(metrics.cpu_time_s));
    dom.metricCpuUtil.textContent = fmtPercent(Number(metrics.cpu_utilization));
    dom.metricHeapPeak.textContent = fmtBytes(Number(metrics.python_heap_peak_bytes));
    dom.metricRssDelta.textContent = fmtBytes(Number(metrics.ru_maxrss_delta_bytes));
    dom.metricJobId.textContent = job && job.job_id ? job.job_id.slice(0, 12) : "-";
  };

  const setBusy = (busy) => {
    if (dom.runProgramBtn) dom.runProgramBtn.disabled = busy;
    if (dom.killProgramBtn) dom.killProgramBtn.disabled = !busy;
    if (dom.executionStrategy) {
      dom.executionStrategy.value = "dask";
      dom.executionStrategy.disabled = true;
    }
    if (dom.noCache) dom.noCache.disabled = busy;
  };

  const renderExecutionPayload = async (job) => {
    const result = (job && job.result) || {};
    dom.executionOutput.textContent = result ? JSON.stringify(result, null, 2) : "";
    dom.taskGraphOutput.textContent = result.task_graph || "(no task graph in payload)";
    renderPlayExecutionLog(job);
    await refreshPlaySelector(result, { autoInspectFirst: true });
  };

  const stopPollingCurrentJob = () => {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  };

  const stopPollingCurrentTestJob = () => {
    if (state.testPollTimer) {
      clearInterval(state.testPollTimer);
      state.testPollTimer = null;
    }
  };

  const stopValuePolling = () => {
    if (state.valuePollTimer) {
      clearInterval(state.valuePollTimer);
      state.valuePollTimer = null;
    }
    state.pendingValueRequest = null;
  };

  const onJobTerminal = async (job) => {
    state.currentPlayJob = job || null;
    setBusy(false);
    setJobStatus(job.status);
    renderRuntimeMetrics(job);
    await renderExecutionPayload(job);
    if (job.error) {
      setRunError(job.error);
    } else {
      setRunError("");
    }
    await refreshJobList();
    if (state.activeTab === "results") {
      await refreshStoreResults();
    }
  };

  const pollCurrentJob = async () => {
    if (!state.currentJobId) return;
    try {
      const job = await api(`/api/v1/playground/jobs/${state.currentJobId}`);
      state.currentPlayJob = job || null;
      setJobStatus(job.status);
      renderPlayExecutionLog(job);
      if (job.status === "running") {
        renderRuntimeMetrics(job);
        renderQueueVisualizer();
        return;
      }
      stopPollingCurrentJob();
      await onJobTerminal(job);
    } catch (err) {
      stopPollingCurrentJob();
      setBusy(false);
      setRunError(`Polling failed: ${err.message}`);
      setJobStatus("failed");
    }
  };

  const createPlaygroundJob = async () => {
    setRunError("");
    await refreshProgramSymbols();
    if (state.symbolDiagnostics.length) {
      setRunError("Static diagnostics must be fixed before execution can start.");
      return;
    }
    const payload = {
      program: dom.programInput.value,
      execute: true,
      execution_strategy: "dask",
    };
    if (dom.noCache) {
      payload.no_cache = Boolean(dom.noCache.checked);
    }
    try {
      setBusy(true);
      setJobStatus("running");
      stopValuePolling();
      dom.executionOutput.textContent = "";
      dom.taskGraphOutput.textContent = "";
      dom.playExecLog.innerHTML = `<div class="muted">Execution started. Waiting for log events...</div>`;
      if (dom.playExecRaw) dom.playExecRaw.textContent = "";
      renderRuntimeMetrics(null);
      const job = await api("/api/v1/playground/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.currentJobId = job.job_id;
      dom.metricJobId.textContent = job.job_id.slice(0, 12);
      stopPollingCurrentJob();
      state.pollTimer = setInterval(pollCurrentJob, 800);
      await pollCurrentJob();
    } catch (err) {
      setBusy(false);
      setJobStatus("failed");
      setRunError(err.message);
    }
  };

  const killCurrentJob = async () => {
    if (!state.currentJobId) return;
    try {
      const job = await api(`/api/v1/playground/jobs/${state.currentJobId}`, { method: "DELETE" });
      stopPollingCurrentJob();
      await onJobTerminal(job);
    } catch (err) {
      setRunError(`Kill failed: ${err.message}`);
    }
  };

  const clearOutput = () => {
    dom.executionOutput.textContent = "";
    dom.taskGraphOutput.textContent = "";
    setRunError("");
    renderRuntimeMetrics(null);
    dom.playExecSummary.textContent = "No execution log yet.";
    dom.playExecLog.innerHTML = `<div class="muted">Run a query to see computed vs cached nodes.</div>`;
    if (dom.playExecRaw) dom.playExecRaw.textContent = "";
    state.latestGoalResults = [];
    state.latestSymbolTable = {};
    state.currentPlayJob = null;
    state.lastHoverToken = "";
    clearHoverTokenState();
    stopValuePolling();
    dom.playResultMeta.textContent = "Click inspect on a variable to resolve it on demand.";
    ensurePlayResultViewer();
    state.playResultViewer.renderRecord(null);
    refreshPlaySelector({}, { keepViewer: true, preserveMeta: true });
    renderQueueVisualizer();
  };

  const encodePath = (path) =>
    String(path || "")
      .split("/")
      .map((token) => encodeURIComponent(token))
      .join("/");

  const ensureResultViewer = () => {
    if (state.resultViewer || !dom.resultInspector) return;
    const ctor = window.VoxResultViewer && window.VoxResultViewer.ResultViewer;
    if (typeof ctor === "function") {
      state.resultViewer = new ctor(dom.resultInspector, {
        onNavigate: (path) => {
          if (!state.currentResultNodeId) return;
          inspectStoreRecord(state.currentResultNodeId, path || "");
        },
        fetchPage: ({ nodeId, path, offset, limit }) =>
          requestStorePage({
            nodeId: nodeId || state.currentResultNodeId || "",
            path: path || "",
            offset: Number(offset || 0),
            limit: Number(limit || 64),
          }),
        onStatusClick: (record) => {
          if (!record || (record.status !== "failed" && record.status !== "killed")) return;
          const details = [
            `node: ${record.node_id || "-"}`,
            `path: ${record.path || "/"}`,
            `status: ${record.status || "failed"}`,
            record.error ? `error: ${record.error}` : "",
          ]
            .filter(Boolean)
            .join("\n");
          state.resultViewer.setError(details);
        },
      });
      return;
    }
    state.resultViewer = {
      setLoading: (message) => {
        dom.resultInspector.innerHTML = `<div class="muted">${sanitize(message || "Loading...")}</div>`;
      },
      setError: (message) => {
        dom.resultInspector.innerHTML = `<div class="inline-error">${sanitize(message || "Viewer error")}</div>`;
      },
      renderRecord: (record) => {
        dom.resultInspector.innerHTML = `<pre class="mono-scroll">${sanitize(
          JSON.stringify(record || {}, null, 2),
        )}</pre>`;
      },
    };
  };

  const loadProgramLibrary = async () => {
    if (!dom.programLibrarySelect) return;
    if (state.capabilities.playground_program_library === false) {
      dom.programLibrarySelect.innerHTML = `<option value="">unavailable</option>`;
      dom.programLibraryMeta.textContent = "Program library endpoint unavailable on this backend.";
      dom.loadProgramFileBtn.disabled = true;
      return;
    }
    try {
      const payload = await api("/api/v1/playground/files");
      state.programFiles = payload.files || [];
      if (!payload.available) {
        dom.programLibrarySelect.innerHTML = `<option value="">library unavailable</option>`;
        dom.programLibraryMeta.textContent = payload.error || "Program library unavailable.";
        dom.loadProgramFileBtn.disabled = true;
        return;
      }
      if (!state.programFiles.length) {
        dom.programLibrarySelect.innerHTML = `<option value="">no .imgql files found</option>`;
        dom.programLibraryMeta.textContent = `${payload.load_dir} | 0 files`;
        dom.loadProgramFileBtn.disabled = true;
        return;
      }
      dom.programLibrarySelect.innerHTML = state.programFiles
        .map((entry) => `<option value="${sanitize(entry.path)}">${sanitize(entry.path)}</option>`)
        .join("");
      dom.programLibraryMeta.textContent = `${payload.load_dir} | ${state.programFiles.length} file(s)`;
      dom.loadProgramFileBtn.disabled = false;
    } catch (err) {
      dom.programLibrarySelect.innerHTML = `<option value="">failed</option>`;
      dom.programLibraryMeta.textContent = `Unable to load library: ${err.message}`;
      dom.loadProgramFileBtn.disabled = true;
    }
  };

  const loadProgramFromLibrary = async () => {
    const rel = dom.programLibrarySelect && dom.programLibrarySelect.value;
    if (!rel) return;
    try {
      const payload = await api(`/api/v1/playground/files/${encodePath(rel)}`);
      dom.programInput.value = payload.content || "";
      dom.programLibraryMeta.textContent = `${payload.path} | ${fmtBytes(Number(payload.bytes || 0))}`;
      await refreshProgramSymbols();
      activateTab("playground");
    } catch (err) {
      dom.programLibraryMeta.textContent = `Unable to load ${rel}: ${err.message}`;
    }
  };

  const renderStoreList = () => {
    if (!dom.resultList) return;
    dom.resultList.innerHTML = "";
    if (!state.storeRecords.length) {
      dom.resultList.innerHTML = `<div class="muted">No stored results found for current runtime.</div>`;
      return;
    }
    dom.resultList.innerHTML = state.storeRecords
      .map((record) => {
        const active = state.currentResultNodeId === record.node_id ? "active" : "";
        return `
          <article class="job-item result-item ${active}" data-node-id="${sanitize(record.node_id)}">
            <div class="job-row">
              <strong>${sanitize(record.node_id.slice(0, 16))}</strong>
              <span class="chip ${sanitize(record.status)}">${sanitize(record.status)}</span>
            </div>
            <div class="job-row">
              <span>${sanitize(record.runtime_version || "-")}</span>
              <span>${sanitize(fmtBytes(Number(record.payload_bytes || 0)))}</span>
            </div>
            <div class="job-row">
              <span>${sanitize(record.updated_at || "-")}</span>
              <button class="btn btn-ghost btn-small" data-open-result="${sanitize(record.node_id)}">Inspect</button>
            </div>
          </article>
        `;
      })
      .join("");
    dom.resultList.querySelectorAll("[data-open-result]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const nodeId = btn.getAttribute("data-open-result");
        if (!nodeId) return;
        inspectStoreRecord(nodeId, "");
      });
    });
  };

  const inspectStoreRecord = async (nodeId, path = "") => {
    ensureResultViewer();
    state.currentResultNodeId = nodeId;
    state.currentResultPath = path || "";
    renderStoreList();
    state.resultViewer.setLoading(`Loading ${nodeId}${path || ""} ...`);
    try {
      const suffix = path ? `?path=${encodeURIComponent(path)}` : "";
      const payload = await api(`/api/v1/results/store/${encodeURIComponent(nodeId)}${suffix}`);
      state.resultViewer.renderRecord(payload);
    } catch (err) {
      state.resultViewer.setError(`Failed loading result ${nodeId}: ${err.message}`);
    }
  };

  const refreshStoreResults = async () => {
    ensureResultViewer();
    if (state.capabilities.store_results_viewer === false) {
      dom.resultListMeta.textContent = "Store results viewer unavailable on this backend.";
      state.storeRecords = [];
      renderStoreList();
      state.resultViewer.setError("Store results endpoint unavailable.");
      return;
    }
    const params = new URLSearchParams();
    params.set("limit", "200");
    const statusFilter = (dom.resultStatusFilter && dom.resultStatusFilter.value) || "";
    const search = (dom.resultSearchInput && dom.resultSearchInput.value) || "";
    if (statusFilter) params.set("status_filter", statusFilter);
    if (search.trim()) params.set("node_filter", search.trim());
    try {
      const payload = await api(`/api/v1/results/store?${params.toString()}`);
      if (!payload.available) {
        dom.resultListMeta.textContent = payload.error || "Store results unavailable.";
        state.storeRecords = [];
        renderStoreList();
        state.resultViewer.setError(payload.error || "Store results unavailable.");
        return;
      }
      state.storeRecords = payload.records || [];
      const summary = payload.summary || {};
      dom.resultListMeta.textContent =
        `${Number(summary.total || 0)} total | ` +
        `${Number(summary.materialized || 0)} materialized | ` +
        `${Number(summary.failed || 0)} failed`;
      renderStoreList();
      if (state.currentResultNodeId) {
        const stillPresent = state.storeRecords.some((record) => record.node_id === state.currentResultNodeId);
        if (stillPresent) {
          await inspectStoreRecord(state.currentResultNodeId, state.currentResultPath || "");
          return;
        }
      }
      if (state.storeRecords.length) {
        await inspectStoreRecord(state.storeRecords[0].node_id, "");
      } else {
        state.resultViewer.renderRecord(null);
      }
    } catch (err) {
      dom.resultListMeta.textContent = `Unable to load results: ${err.message}`;
      state.storeRecords = [];
      renderStoreList();
      state.resultViewer.setError(`Unable to load result list: ${err.message}`);
    }
  };

  const ensurePlayResultViewer = () => {
    if (state.playResultViewer || !dom.playResultInspector) return;
    const ctor = window.VoxResultViewer && window.VoxResultViewer.ResultViewer;
    if (typeof ctor === "function") {
      state.playResultViewer = new ctor(dom.playResultInspector, {
        onNavigate: (path) => {
          const target = state.playSelectableTargets[Number(dom.playResultSelector.value)];
          if (!target || !target.nodeId) return;
          inspectPlayTarget(target.nodeId, path || "");
        },
        fetchPage: ({ nodeId, path, offset, limit }) => {
          const target = state.playSelectableTargets[Number(dom.playResultSelector.value)];
          const variable = target && target.kind === "variable" ? String(target.label || "") : "";
          return requestPlayValuePage({
            nodeId: nodeId || (target && target.nodeId) || "",
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
    state.playResultViewer = {
      setLoading: (message) => {
        dom.playResultInspector.innerHTML = `<div class="muted">${sanitize(message || "Loading...")}</div>`;
      },
      setError: (message) => {
        dom.playResultInspector.innerHTML = `<div class="inline-error">${sanitize(message || "Viewer error")}</div>`;
      },
      renderRecord: (record) => {
        dom.playResultInspector.innerHTML = `<pre class="mono-scroll">${sanitize(
          JSON.stringify(record || {}, null, 2),
        )}</pre>`;
      },
    };
  };

  const parseLogTail = (logTail) => {
    const out = [];
    const lines = String(logTail || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    for (const line of lines) {
      try {
        const parsed = JSON.parse(line);
        if (parsed && typeof parsed === "object") {
          out.push(parsed);
          continue;
        }
      } catch (_err) {
        // Keep raw lines visible when they are not JSON payloads.
      }
      out.push({ event: "raw", message: line });
    }
    return out;
  };

  const ensureQueueVisualizer = () => {
    if (state.queueVisualizer || !dom.daskQueueViz) return;
    const ctor = window.VoxDaskQueueViz && window.VoxDaskQueueViz.DaskQueueVisualizer;
    if (typeof ctor === "function") {
      state.queueVisualizer = new ctor(dom.daskQueueViz);
      return;
    }
    state.queueVisualizer = {
      render: (snapshot) => {
        const counts = (snapshot && snapshot.counts) || {};
        dom.daskQueueViz.innerHTML = `
          <div class="muted">
            queue: queued ${Number(counts.queued || 0)} | running ${Number(counts.running || 0)} |
            completed ${Number(counts.completed || 0)} | failed ${Number(counts.failed || 0)}
          </div>
        `;
      },
    };
  };

  const buildQueueSnapshot = (jobs, currentJob) => {
    const safeJobs = Array.isArray(jobs) ? jobs : [];
    const counts = {
      queued: 0,
      running: 0,
      completed: 0,
      failed: 0,
      killed: 0,
      other: 0,
      total: safeJobs.length,
    };
    for (const job of safeJobs) {
      const status = String((job && job.status) || "").toLowerCase();
      if (status in counts) {
        counts[status] += 1;
      } else if (status === "success") {
        counts.completed += 1;
      } else {
        counts.other += 1;
      }
    }

    const logEntries = parseLogTail(currentJob && currentJob.log_tail ? currentJob.log_tail : "");
    const nodeEvents = logEntries
      .filter((entry) => entry && entry.event === "playground.node")
      .slice(-360)
      .map((entry) => ({
        status: String(entry.status || "unknown"),
        cache_source: String(entry.cache_source || "-"),
        duration_s: Number(entry.duration_s || 0),
        operator: String(entry.operator || "-"),
        node_id: String(entry.node_id || ""),
      }));

    const displayJobs = safeJobs.slice(0, 36).map((job) => ({
      id: String(job.job_id || ""),
      status: String(job.status || "unknown"),
      created_at: String(job.created_at || ""),
      started_at: String(job.started_at || ""),
      finished_at: String(job.finished_at || ""),
      wall_time_s: Number(job.metrics && job.metrics.wall_time_s ? job.metrics.wall_time_s : 0),
      strategy: String((job.request && job.request.execution_strategy) || "dask"),
      kind: String((job.request && job.request.job_kind) || "run"),
    }));

    return {
      generated_at: Date.now(),
      counts,
      jobs: displayJobs,
      node_events: nodeEvents,
      active_job_id: currentJob && currentJob.job_id ? String(currentJob.job_id) : "",
    };
  };

  const renderQueueVisualizer = () => {
    ensureQueueVisualizer();
    if (!state.queueVisualizer) return;
    const snapshot = buildQueueSnapshot(state.latestPlayJobs, state.currentPlayJob);
    state.queueVisualizer.render(snapshot);
  };

  const renderPlayExecutionLog = (job) => {
    const result = (job && job.result) || {};
    const execution = (result && result.execution) || {};
    const summary = execution.cache_summary || {};
    const logTail = String((job && job.log_tail) || "");
    if (dom.playExecRaw) {
      dom.playExecRaw.textContent = logTail;
    }
    const entries = parseLogTail(logTail);
    const nodeEvents = entries.filter((entry) => entry.event === "playground.node");
    const displayedEvents = nodeEvents.length ? nodeEvents : entries.filter((entry) => entry.event !== "raw");
    dom.playExecSummary.textContent =
      `computed ${Number(summary.computed || 0)} | ` +
      `cached(store) ${Number(summary.cached_store || 0)} | ` +
      `cached(local) ${Number(summary.cached_local || 0)} | ` +
      `failed ${Number(summary.failed || 0)} | ` +
      `events ${Number(summary.events_stored || nodeEvents.length)}/${Number(summary.events_total || nodeEvents.length)}`;
    if (!displayedEvents.length) {
      dom.playExecLog.innerHTML = `<div class="muted">No node events available yet. Raw log is shown below.</div>`;
      renderQueueVisualizer();
      return;
    }
    const rows = displayedEvents.slice(-220).reverse();
    dom.playExecLog.innerHTML = rows
      .map((entry) => {
        if (entry.event === "playground.node") {
          const status = sanitize(entry.status || "unknown");
          const source = sanitize(entry.cache_source || "-");
          const operator = sanitize(entry.operator || "-");
          const durationMs = `${(Number(entry.duration_s || 0) * 1000).toFixed(2)} ms`;
          const node = sanitize(String(entry.node_id || "").slice(0, 12));
          const extra = entry.error ? ` | ${sanitize(String(entry.error))}` : "";
          return `
            <article class="table-like-row">
              <div class="name"><span class="mini-chip">${status}</span> ${operator}</div>
              <div class="detail">node=${node} | source=${source} | duration=${durationMs}${extra}</div>
            </article>
          `;
        }
        const eventName = sanitize(entry.event || "event");
        const detail = sanitize(
          entry.message ||
            entry.error ||
            JSON.stringify(entry).slice(0, 180),
        );
        return `
          <article class="table-like-row">
            <div class="name">${eventName}</div>
            <div class="detail">${detail}</div>
          </article>
        `;
      })
      .join("");
    renderQueueVisualizer();
  };

  const buildPlayTargets = () => {
    const targets = [];
    const seen = new Set();
    const pushTarget = (target) => {
      const key = `${target.kind}:${target.label}:${target.nodeId}`;
      if (seen.has(key)) return;
      seen.add(key);
      targets.push(target);
    };
    for (const goal of state.precomputedPrintTargets) {
      if (!goal || !goal.node_id) continue;
      pushTarget({
        kind: "print",
        label: String(goal.name || "print"),
        nodeId: String(goal.node_id),
      });
    }
    for (const goal of state.latestGoalResults) {
      if (!goal || goal.operation !== "print" || !goal.node_id) continue;
      pushTarget({
        kind: "print",
        label: String(goal.name || "print"),
        nodeId: String(goal.node_id),
      });
    }
    const mergedSymbols =
      Object.keys(state.precomputedSymbolTable || {}).length > 0
        ? state.precomputedSymbolTable
        : state.latestSymbolTable;
    for (const [name, nodeId] of Object.entries(mergedSymbols)) {
      if (!nodeId || typeof nodeId !== "string") continue;
      pushTarget({
        kind: "variable",
        label: String(name),
        nodeId,
      });
    }
    return targets;
  };

  const requestPlayValue = async ({ nodeId, variable = "", path = "", enqueue = true }) => {
    const payload = {
      program: dom.programInput.value,
      execution_strategy: "dask",
      variable,
      path,
      enqueue,
    };
    if (!variable && nodeId) {
      payload.node_id = nodeId;
    }
    return api("/api/v1/playground/value", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  };

  const requestPlayValuePage = async ({
    nodeId,
    variable = "",
    path = "",
    offset = 0,
    limit = 64,
    enqueue = true,
  }) => {
    const payload = {
      program: dom.programInput.value,
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
    return api("/api/v1/playground/value/page", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  };

  const requestStorePage = async ({ nodeId, path = "", offset = 0, limit = 64 }) => {
    if (!nodeId) throw new Error("Missing node id for page request.");
    const params = new URLSearchParams();
    if (path) params.set("path", path);
    params.set("offset", `${Math.max(0, Number(offset || 0))}`);
    params.set("limit", `${Math.max(1, Number(limit || 64))}`);
    return api(`/api/v1/results/store/${encodeURIComponent(nodeId)}/page?${params.toString()}`);
  };

  const normalizedExecutionErrors = (payload) => {
    const direct = payload && typeof payload === "object" ? payload.execution_errors : null;
    if (direct && typeof direct === "object") {
      return direct;
    }
    const diagnostics = payload && typeof payload === "object" ? payload.diagnostics : null;
    const nested = diagnostics && typeof diagnostics === "object" ? diagnostics.execution_errors : null;
    if (nested && typeof nested === "object") {
      return nested;
    }
    return {};
  };

  const normalizedExecutionErrorDetails = (payload) => {
    const direct = payload && typeof payload === "object" ? payload.execution_error_details : null;
    if (direct && typeof direct === "object") {
      return direct;
    }
    const diagnostics = payload && typeof payload === "object" ? payload.diagnostics : null;
    const nested = diagnostics && typeof diagnostics === "object" ? diagnostics.execution_error_details : null;
    if (nested && typeof nested === "object") {
      return nested;
    }
    return {};
  };

  const formatExecutionErrors = (errors) => {
    const entries = Object.entries(errors || {});
    if (!entries.length) return "none";
    return entries
      .map(([node, message]) => `${String(node).slice(0, 12)}: ${String(message)}`)
      .join("\n");
  };

  const formatExecutionErrorDetails = (details, executionErrors) => {
    const entries = Object.entries(details || {});
    if (!entries.length) return "";
    const summarize = (value, maxLen = 120) => {
      const text = String(value ?? "");
      if (text.length <= maxLen) return text;
      return `${text.slice(0, maxLen)}...`;
    };
    return entries
      .map(([node, detail]) => {
        const op = detail && detail.operator ? String(detail.operator) : "unknown";
        const args = Array.isArray(detail && detail.args) ? detail.args.map((v) => summarize(v)).join(", ") : "";
        const kwargs =
          detail && typeof detail.kwargs === "object" && detail.kwargs
            ? Object.entries(detail.kwargs)
                .map(([k, v]) => `${String(k)}=${summarize(v)}`)
                .join(", ")
            : "";
        const attrs =
          detail && typeof detail.attrs === "object" && detail.attrs
            ? summarize(JSON.stringify(detail.attrs))
            : "";
        const kind = detail && detail.kind ? ` kind=${String(detail.kind)}` : "";
        const outputKind = detail && detail.output_kind ? ` output=${String(detail.output_kind)}` : "";
        const message = executionErrors && executionErrors[node] ? String(executionErrors[node]) : "";
        const signature = kwargs ? `${op}(${args}${args ? ", " : ""}${kwargs})` : `${op}(${args})`;
        const attrsPart = attrs && attrs !== "{}" ? ` attrs=${attrs}` : "";
        const kindPart = `${kind}${outputKind}`;
        const errPart = message ? ` error=${message}` : "";
        return `${String(node).slice(0, 12)}: ${signature}${attrsPart}${kindPart}${errPart}`;
      })
      .join("\n");
  };

  const buildFailureDetailsText = (payload, requestState) => {
    const nodeId = String(payload.node_id || requestState.nodeId || "");
    const path = payload.path || requestState.path || "";
    const computeStatus = String(payload.compute_status || payload.status || "failed");
    const lines = [
      `node: ${nodeId || "-"}`,
      `path: ${path || "/"}`,
      `status: ${computeStatus}`,
    ];

    const storeStatus =
      String(payload.store_status || ((payload.diagnostics && payload.diagnostics.store_status) || "missing"));
    lines.push(`store: ${storeStatus}`);

    if (payload.error) lines.push(`error: ${String(payload.error)}`);
    if (payload.job_error) lines.push(`job_error: ${String(payload.job_error)}`);
    if (payload.store_error) lines.push(`store_error: ${String(payload.store_error)}`);

    const diagnostics = payload && typeof payload === "object" ? payload.diagnostics : null;
    if (diagnostics && typeof diagnostics === "object") {
      if (diagnostics.job_error && !payload.job_error) {
        lines.push(`job_error: ${String(diagnostics.job_error)}`);
      }
      if (diagnostics.store_error && !payload.store_error) {
        lines.push(`store_error: ${String(diagnostics.store_error)}`);
      }
    }

    const executionErrors = normalizedExecutionErrors(payload);
    if (Object.keys(executionErrors).length) {
      lines.push("execution_errors:");
      lines.push(formatExecutionErrors(executionErrors));
    }
    const executionErrorDetails = normalizedExecutionErrorDetails(payload);
    const detailsText = formatExecutionErrorDetails(executionErrorDetails, executionErrors);
    if (detailsText) {
      lines.push("failed_operations:");
      lines.push(detailsText);
    }

    return lines.join("\n");
  };

  const renderPlayFailureDiagnostics = (payload, requestState) => {
    const nodeId = String(payload.node_id || requestState.nodeId || "");
    const path = payload.path || requestState.path || "";
    const computeStatus = String(payload.compute_status || payload.status || "failed").toLowerCase();
    const badgeStatus = computeStatus === "killed" ? "killed" : "failed";
    const detailsText = buildFailureDetailsText(payload, requestState);
    const primaryError = String(payload.error || "Value inspection failed.");

    state.playResultViewer.renderRecord({
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
        summary: {
          value: detailsText,
          length: detailsText.length,
          truncated: false,
        },
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

    dom.playResultMeta.textContent =
      `Unable to inspect ${nodeId.slice(0, 12)}${path ? ` @ ${path}` : ""}: ${primaryError}`;

    const logTail = String(payload.log_tail || "");
    if (logTail) {
      renderPlayExecutionLog({
        log_tail: logTail,
        result: {
          execution: {
            cache_summary:
              payload.cache_summary ||
              (payload.diagnostics && payload.diagnostics.cache_summary) ||
              {},
          },
        },
      });
      return;
    }
    if (dom.playExecRaw) {
      dom.playExecRaw.textContent = "";
    }
    dom.playExecSummary.textContent = "No execution log available for this failure.";
    dom.playExecLog.innerHTML = `<div class="muted">No job log was attached to this failed lookup.</div>`;
  };

  const applyPlayValuePayload = (payload, requestState) => {
    const materialization = String(payload.materialization || "");
    const computeStatus = String(payload.compute_status || "");
    const statusName = String(payload.status || "");
    const nodeId = String(payload.node_id || requestState.nodeId || "");
    const path = payload.path || requestState.path || "";
    const jobId = payload.job_id ? String(payload.job_id) : "";
    const descriptorKind =
      payload.descriptor && typeof payload.descriptor === "object"
        ? String(payload.descriptor.vox_type || "")
        : "";
    const hasRenderableDescriptor =
      payload.descriptor &&
      typeof payload.descriptor === "object" &&
      descriptorKind &&
      descriptorKind !== "unavailable" &&
      descriptorKind !== "error";
    const isMaterialized = materialization === "cached" || materialization === "computed" || statusName === "materialized";
    if (isMaterialized && hasRenderableDescriptor) {
      stopValuePolling();
      state.playResultViewer.renderRecord(payload);
      dom.playResultMeta.textContent =
        `Inspecting ${nodeId.slice(0, 12)}${path ? ` @ ${path}` : ""} | ` +
        `${materialization || "materialized"} | status=${computeStatus || "materialized"}`;
      return;
    }

    if (
      materialization === "failed" ||
      computeStatus === "failed" ||
      computeStatus === "killed" ||
      statusName === "failed"
    ) {
      stopValuePolling();
      renderPlayFailureDiagnostics(payload, requestState);
      return;
    }

    if (
      materialization === "pending" ||
      computeStatus === "queued" ||
      computeStatus === "running" ||
      computeStatus === "persisting"
    ) {
      state.playResultViewer.setLoading(
        `Value ${nodeId.slice(0, 12)} ${path || ""} is ${computeStatus || "pending"}...`,
      );
      dom.playResultMeta.textContent =
        `Waiting for ${nodeId.slice(0, 12)}${path ? ` @ ${path}` : ""} | ` +
        `${computeStatus || "pending"}${jobId ? ` | job ${jobId.slice(0, 12)}` : ""}`;
      if (payload.log_tail && dom.playExecRaw) {
        dom.playExecRaw.textContent = String(payload.log_tail);
      }
      if (!state.valuePollTimer) {
        state.pendingValueRequest = {
          nodeId,
          variable: requestState.variable || "",
          path,
        };
        state.valuePollTimer = setInterval(async () => {
          const pending = state.pendingValueRequest;
          if (!pending) return;
          try {
            const polled = await requestPlayValue({
              nodeId: pending.nodeId,
              variable: pending.variable,
              path: pending.path,
              enqueue: false,
            });
            applyPlayValuePayload(polled, pending);
          } catch (err) {
            state.playResultViewer.setError(`Polling value failed: ${err.message}`);
            stopValuePolling();
          }
        }, 900);
      }
      return;
    }

    stopValuePolling();
    const errorMessage =
      payload.error ||
      (materialization === "missing"
        ? "Value is not materialized yet."
        : `Unable to inspect value (${materialization || computeStatus || "unknown"}).`);
    renderPlayFailureDiagnostics(
      {
        ...payload,
        error: errorMessage,
        compute_status: computeStatus || "failed",
        materialization: materialization || "failed",
        status: payload.status || "failed",
      },
      requestState,
    );
  };

  const inspectPlayTarget = async (nodeId, path = "", options = {}) => {
    ensurePlayResultViewer();
    const variable = String(options.variable || "");
    stopValuePolling();
    state.playResultViewer.setLoading(`Loading value for ${nodeId} ...`);
    try {
      const payload = await requestPlayValue({
        nodeId,
        variable,
        path,
        enqueue: options.enqueue !== false,
      });
      applyPlayValuePayload(payload, { nodeId, variable, path });
    } catch (err) {
      stopValuePolling();
      state.playResultViewer.setError(`Value lookup failed: ${err.message}`);
      dom.playResultMeta.textContent = `Unable to inspect ${nodeId.slice(0, 12)}: ${err.message}`;
    }
  };

  const refreshPlaySelector = async (runResult = {}, options = {}) => {
    ensurePlayResultViewer();
    if (runResult && Array.isArray(runResult.goal_results)) {
      state.latestGoalResults = runResult.goal_results;
    }
    if (runResult && runResult.symbol_table && typeof runResult.symbol_table === "object") {
      state.latestSymbolTable = runResult.symbol_table;
    }

    const previous = state.playSelectableTargets[Number(dom.playResultSelector.value)];
    const targets = buildPlayTargets();
    state.playSelectableTargets = targets;
    if (!targets.length) {
      dom.playResultSelector.innerHTML = `<option value="">No print labels or variables available</option>`;
      dom.playResultSelector.disabled = true;
      if (!options.preserveMeta) {
        dom.playResultMeta.textContent = "No selectable targets in this query.";
      }
      if (!options.keepViewer) {
        state.playResultViewer.renderRecord(null);
      }
      return;
    }

    dom.playResultSelector.disabled = false;
    dom.playResultSelector.innerHTML = targets
      .map(
        (target, index) =>
          `<option value="${index}">${sanitize(target.kind === "print" ? `print: ${target.label}` : `var: ${target.label}`)}</option>`,
      )
      .join("");

    let selectedIndex = 0;
    if (previous) {
      const matched = targets.findIndex(
        (target) => target.nodeId === previous.nodeId && target.kind === previous.kind && target.label === previous.label,
      );
      if (matched >= 0) selectedIndex = matched;
    }
    dom.playResultSelector.value = `${selectedIndex}`;
    if (options.autoInspectFirst) {
      const target = targets[selectedIndex];
      await inspectPlayTarget(target.nodeId, "", { variable: target.kind === "variable" ? target.label : "" });
    }
  };

  const refreshProgramSymbols = async () => {
    if (state.capabilities.playground_symbols === false) return;
    const token = state.symbolRefreshToken + 1;
    state.symbolRefreshToken = token;
    const program = dom.programInput.value || "";
    if (!program.trim()) {
      state.precomputedSymbolTable = {};
      state.precomputedPrintTargets = [];
      state.currentProgramHash = null;
      setProgramDiagnostics([]);
      renderEditorTokenOverlay();
      await refreshPlaySelector({}, { keepViewer: true, preserveMeta: true });
      return;
    }
    try {
      const payload = await api("/api/v1/playground/symbols", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ program }),
      });
      if (token !== state.symbolRefreshToken) return;
      const available = payload && payload.available !== false;
      state.precomputedSymbolTable = available ? payload.symbol_table || {} : {};
      state.precomputedPrintTargets = available ? payload.print_targets || [] : [];
      state.currentProgramHash = payload.program_hash || null;
      setProgramDiagnostics(payload && payload.diagnostics ? payload.diagnostics : []);
      renderEditorTokenOverlay();
      await refreshPlaySelector({}, { keepViewer: true, preserveMeta: true });
    } catch (err) {
      if (token !== state.symbolRefreshToken) return;
      state.precomputedSymbolTable = {};
      state.precomputedPrintTargets = [];
      state.currentProgramHash = null;
      setProgramDiagnostics([
        {
          code: "E_SYMBOLS",
          message: `Unable to refresh symbols: ${err.message}`,
        },
      ]);
      renderEditorTokenOverlay();
      await refreshPlaySelector({}, { keepViewer: true, preserveMeta: true });
    }
  };

  const scheduleProgramSymbolsRefresh = () => {
    if (state.symbolRefreshTimer) {
      clearTimeout(state.symbolRefreshTimer);
      state.symbolRefreshTimer = null;
    }
    state.symbolRefreshTimer = setTimeout(() => {
      refreshProgramSymbols();
    }, 260);
  };

  const tokenPattern = /[A-Za-z0-9_.$+\-*/<>=!?~^%:&|]/;
  const extractTokenAt = (text, position) => {
    if (!text || !Number.isInteger(position) || position < 0) return "";
    const safePos = Math.max(0, Math.min(position, text.length));
    let start = safePos;
    let end = safePos;
    while (start > 0 && tokenPattern.test(text[start - 1])) start -= 1;
    while (end < text.length && tokenPattern.test(text[end])) end += 1;
    const token = text.slice(start, end).trim();
    return token.replace(/^[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+|[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+$/g, "");
  };

  const extractTokenInfoAt = (text, position) => {
    if (!text || !Number.isInteger(position) || position < 0) return { token: "", start: 0, end: 0 };
    const safePos = Math.max(0, Math.min(position, text.length));
    let start = safePos;
    let end = safePos;
    while (start > 0 && tokenPattern.test(text[start - 1])) start -= 1;
    while (end < text.length && tokenPattern.test(text[end])) end += 1;
    const token = text
      .slice(start, end)
      .trim()
      .replace(/^[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+|[^A-Za-z0-9_.$+\-*/<>=!?~^%:&|]+$/g, "");
    return { token, start, end };
  };

  const isOperatorToken = (token) => {
    if (!token) return false;
    if (/[A-Za-z0-9_$]/.test(token)) return false;
    return true;
  };

  const expressionContextAt = (text, position) => {
    const safePos = Math.max(0, Math.min(Number(position || 0), text.length));
    let start = safePos;
    let end = safePos;
    while (start > 0 && text[start - 1] !== "\n") start -= 1;
    while (end < text.length && text[end] !== "\n") end += 1;
    const line = text.slice(start, end).trim();
    if (!line) return "";
    if (line.length <= 140) return line;
    return `${line.slice(0, 137)}...`;
  };

  const extractTokenFromSelection = () => {
    const text = dom.programInput.value || "";
    const start = Number(dom.programInput.selectionStart || 0);
    const end = Number(dom.programInput.selectionEnd || 0);
    let token = text.slice(start, end).trim();
    if (!token) {
      token = extractTokenAt(text, start);
    }
    return token;
  };

  const textIndexFromPoint = (textarea, clientX, clientY) => {
    const text = textarea.value || "";
    const rect = textarea.getBoundingClientRect();
    if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) return null;
    const style = window.getComputedStyle(textarea);
    const font = style.font || `${style.fontSize} ${style.fontFamily}`;
    const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.5;
    const padLeft = Number.parseFloat(style.paddingLeft) || 0;
    const padTop = Number.parseFloat(style.paddingTop) || 0;
    const probe = document.createElement("span");
    probe.style.position = "absolute";
    probe.style.visibility = "hidden";
    probe.style.whiteSpace = "pre";
    probe.style.font = font;
    probe.textContent = "MMMMMMMMMM";
    document.body.appendChild(probe);
    const charWidth = Math.max(1, probe.getBoundingClientRect().width / 10);
    probe.remove();

    const x = clientX - rect.left + textarea.scrollLeft - padLeft;
    const y = clientY - rect.top + textarea.scrollTop - padTop;
    const lineIndex = Math.max(0, Math.floor(y / lineHeight));
    const colIndex = Math.max(0, Math.floor(x / charWidth));
    const lines = text.split("\n");
    const boundedLine = Math.min(lineIndex, Math.max(0, lines.length - 1));
    let offset = 0;
    for (let idx = 0; idx < boundedLine; idx += 1) {
      offset += lines[idx].length + 1;
    }
    offset += Math.min(colIndex, lines[boundedLine].length);
    return Math.min(offset, text.length);
  };

  const resolveSymbolNode = (token) => {
    if (!token) return "";
    return state.precomputedSymbolTable[token] || state.latestSymbolTable[token] || "";
  };

  const decodeTokenAttr = (value) => {
    try {
      return decodeURIComponent(String(value || ""));
    } catch (_err) {
      return String(value || "");
    }
  };

  const setOverlayActiveToken = (token) => {
    if (!dom.programOverlay) return;
    const encoded = encodeURIComponent(String(token || ""));
    dom.programOverlay.querySelectorAll(".editor-token-hit").forEach((el) => {
      if (!(el instanceof HTMLElement)) return;
      if (token && el.dataset.token === encoded) {
        el.classList.add("is-active");
      } else {
        el.classList.remove("is-active");
      }
    });
  };

  const syncEditorOverlayScroll = () => {
    if (!dom.programOverlay || !dom.programInput) return;
    dom.programOverlay.scrollTop = dom.programInput.scrollTop;
    dom.programOverlay.scrollLeft = dom.programInput.scrollLeft;
  };

  const renderEditorTokenOverlay = () => {
    if (!dom.programOverlay || !dom.programInput) return;
    const text = String(dom.programInput.value || "");
    if (!text) {
      dom.programOverlay.innerHTML = "";
      syncEditorOverlayScroll();
      return;
    }
    const out = [];
    let idx = 0;
    while (idx < text.length) {
      const ch = text[idx];
      if (/[A-Za-z_.$]/.test(ch)) {
        let end = idx + 1;
        while (end < text.length && /[A-Za-z0-9_.$]/.test(text[end])) end += 1;
        const token = text.slice(idx, end);
        if (resolveSymbolNode(token)) {
          const encoded = encodeURIComponent(token);
          out.push(
            `<button type="button" class="editor-token-hit" data-token="${sanitizeAttr(encoded)}" tabindex="-1" title="Inspect ${sanitizeAttr(token)}">${sanitize(token)}</button>`,
          );
        } else {
          out.push(sanitize(token));
        }
        idx = end;
        continue;
      }
      out.push(sanitize(ch));
      idx += 1;
    }
    dom.programOverlay.innerHTML = out.join("");
    syncEditorOverlayScroll();
    if (state.hoverActiveToken) {
      setOverlayActiveToken(state.hoverActiveToken);
    }
  };

  const ensureEditorTokenOverlay = () => {
    if (dom.programOverlay || !dom.editorShell || !dom.programInput) return;
    const overlay = document.createElement("pre");
    overlay.className = "editor-token-overlay";
    overlay.setAttribute("aria-hidden", "true");
    dom.editorShell.appendChild(overlay);
    dom.programOverlay = overlay;

    overlay.addEventListener("mousedown", (event) => {
      const tokenEl = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!tokenEl) return;
      event.preventDefault();
    });
    overlay.addEventListener("mouseover", (event) => {
      const tokenEl = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!tokenEl) return;
      const token = decodeTokenAttr(tokenEl.getAttribute("data-token"));
      if (!token) return;
      state.lastHoverToken = token;
      setHoverTokenState(token);
      if (state.hoverPreviewToken !== token) {
        state.hoverPreviewToken = token;
        previewVariableHover(token);
      }
    });
    overlay.addEventListener("mouseout", (event) => {
      const fromToken = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!fromToken) return;
      const toToken =
        event.relatedTarget instanceof Element ? event.relatedTarget.closest(".editor-token-hit") : null;
      if (toToken) return;
      state.lastHoverToken = "";
      clearHoverTokenState();
    });
    overlay.addEventListener("click", async (event) => {
      const tokenEl = event.target instanceof Element ? event.target.closest(".editor-token-hit") : null;
      if (!tokenEl) return;
      event.preventDefault();
      const token = decodeTokenAttr(tokenEl.getAttribute("data-token"));
      if (!token) return;
      await inspectTokenWithRefresh(token);
      dom.programInput.focus({ preventScroll: true });
    });
    renderEditorTokenOverlay();
  };

  const pickResolvableToken = (...candidates) => {
    for (const candidate of candidates) {
      const token = String(candidate || "").trim();
      if (!token) continue;
      if (resolveSymbolNode(token)) return token;
    }
    return "";
  };

  const clearHoverTokenState = () => {
    state.hoverActiveToken = "";
    state.hoverPreviewToken = "";
    setOverlayActiveToken("");
    setEditorHoverPreview("Hover a variable to see cached value status, or hover an operator to inspect expression context.");
  };

  const setHoverTokenState = (token) => {
    state.hoverActiveToken = token || "";
    setOverlayActiveToken(token || "");
  };

  const summarizeDescriptor = (descriptor) => {
    if (!descriptor || typeof descriptor !== "object") return "value available";
    const voxType = String(descriptor.vox_type || "value");
    const summary = descriptor.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
    if (voxType === "integer" || voxType === "number" || voxType === "boolean") {
      return `${voxType}: ${summary.value}`;
    }
    if (voxType === "string") {
      const preview = summary && summary.value ? String(summary.value) : "";
      return `string(${summary.length || 0}): ${preview}`;
    }
    if (voxType === "ndarray") {
      return `ndarray ${Array.isArray(summary.shape) ? summary.shape.join("x") : ""} ${summary.dtype || ""}`.trim();
    }
    if (voxType === "image2d" || voxType === "volume3d") {
      return `${voxType} ${Array.isArray(summary.size) ? summary.size.join("x") : ""} ${summary.pixel_id || ""}`.trim();
    }
    if (voxType === "sequence") {
      return `sequence length=${summary.length || 0}`;
    }
    if (voxType === "mapping") {
      return `mapping length=${summary.length || 0}`;
    }
    return voxType;
  };

  const previewVariableHover = async (token) => {
    const seq = state.hoverPreviewSeq + 1;
    state.hoverPreviewSeq = seq;
    setEditorHoverPreview(`var ${token}: checking cache...`);
    try {
      const payload = await requestPlayValue({
        nodeId: "",
        variable: token,
        path: "",
        enqueue: false,
      });
      if (seq !== state.hoverPreviewSeq || state.hoverActiveToken !== token) return;
      const materialization = String(payload.materialization || "");
      if (payload.descriptor && (materialization === "cached" || materialization === "computed")) {
        setEditorHoverPreview(`var ${token}: ${summarizeDescriptor(payload.descriptor)} (${materialization})`);
        return;
      }
      if (
        materialization === "pending" ||
        payload.compute_status === "running" ||
        payload.compute_status === "queued" ||
        payload.compute_status === "persisting"
      ) {
        setEditorHoverPreview(`var ${token}: running (${payload.compute_status}). Click to focus/inspect.`);
        return;
      }
      setEditorHoverPreview(`var ${token}: not materialized yet. Click to compute on demand.`);
    } catch (_err) {
      if (seq !== state.hoverPreviewSeq || state.hoverActiveToken !== token) return;
      setEditorHoverPreview(`var ${token}: click to compute on demand.`);
    }
  };

  const previewOperatorHover = (token, position) => {
    const text = dom.programInput.value || "";
    const context = expressionContextAt(text, position);
    if (!context) {
      setEditorHoverPreview(`operator '${token}'`);
      return;
    }
    setEditorHoverPreview(`operator '${token}' in: ${context}`);
  };

  const inspectSymbolToken = async (token) => {
    const nodeId = resolveSymbolNode(token);
    if (!nodeId) return;
    clearHoverTokenState();
    const index = state.playSelectableTargets.findIndex((target) => target.kind === "variable" && target.label === token);
    if (index >= 0) {
      dom.playResultSelector.value = `${index}`;
    }
    await inspectPlayTarget(nodeId, "", { variable: token });
  };

  const handleProgramHover = (event) => {
    if (state.activeTab !== "playground") return;
    if (state.hoverTimer) {
      clearTimeout(state.hoverTimer);
      state.hoverTimer = null;
    }
    state.hoverTimer = setTimeout(() => {
      const position = textIndexFromPoint(dom.programInput, event.clientX, event.clientY);
      if (!Number.isInteger(position)) {
        clearHoverTokenState();
        return;
      }
      const tokenInfo = extractTokenInfoAt(dom.programInput.value || "", position);
      const token = tokenInfo.token;
      if (!token) {
        clearHoverTokenState();
        return;
      }
      const nodeId = resolveSymbolNode(token);
      if (!nodeId && !isOperatorToken(token)) {
        clearHoverTokenState();
        return;
      }
      state.lastHoverToken = token;
      if (nodeId) {
        setHoverTokenState(token);
        if (state.hoverPreviewToken !== token) {
          state.hoverPreviewToken = token;
          previewVariableHover(token);
        }
        return;
      }
      setHoverTokenState("");
      state.hoverPreviewToken = token;
      previewOperatorHover(token, tokenInfo.start);
    }, 140);
  };

  const inspectTokenWithRefresh = async (token) => {
    if (!token) return false;
    let resolved = pickResolvableToken(token);
    if (!resolved) {
      await refreshProgramSymbols();
      resolved = pickResolvableToken(token, extractTokenFromSelection(), state.lastHoverToken);
    }
    if (!resolved) return false;
    state.lastHoverToken = resolved;
    await inspectSymbolToken(resolved);
    return true;
  };

  const renderBarList = (container, items, valueField, labelField, formatter = (v) => `${v}`) => {
    container.innerHTML = "";
    if (!items || !items.length) {
      container.innerHTML = `<div class="muted">No data yet.</div>`;
      return;
    }
    const max = Math.max(...items.map((it) => Number(it[valueField] || 0)), 1);
    for (const item of items) {
      const value = Number(item[valueField] || 0);
      const pct = (value / max) * 100;
      const row = document.createElement("div");
      row.className = "bar-row";
      row.innerHTML = `
        <div class="meta">
          <span>${sanitize(item[labelField])}</span>
          <strong>${sanitize(formatter(value))}</strong>
        </div>
        <div class="bar-bg"><div class="bar-fill" style="width: ${pct.toFixed(2)}%"></div></div>
      `;
      container.appendChild(row);
    }
  };

  const openPlayJob = async (jobId) => {
    if (!jobId) return;
    try {
      const job = await api(`/api/v1/playground/jobs/${jobId}`);
      state.currentJobId = jobId;
      state.currentPlayJob = job || null;
      setJobStatus(job.status);
      renderRuntimeMetrics(job);
      await renderExecutionPayload(job);
      if (job.status === "running") {
        stopPollingCurrentJob();
        state.pollTimer = setInterval(pollCurrentJob, 800);
      }
      const executionErrors =
        job &&
        job.result &&
        job.result.execution &&
        job.result.execution.errors &&
        typeof job.result.execution.errors === "object"
          ? job.result.execution.errors
          : {};
      if ((job.status === "failed" || job.status === "killed") && Object.keys(executionErrors).length) {
        const combinedError = [job.error || "Execution failed", formatExecutionErrors(executionErrors)]
          .filter(Boolean)
          .join("\n");
        setRunError(combinedError);
        return;
      }
      if (job.error) {
        setRunError(job.error);
      }
    } catch (err) {
      setRunError(`Unable to open ${jobId}: ${err.message}`);
    }
  };

  const refreshJobList = async () => {
    try {
      const payload = await api("/api/v1/playground/jobs");
      const jobs = payload.jobs || [];
      state.latestPlayJobs = jobs;
      if (!state.currentPlayJob && jobs.length) {
        state.currentPlayJob = jobs[0];
      }
      renderQueueVisualizer();
      if (!jobs.length) {
        dom.recentJobs.innerHTML = `<div class="muted">No jobs executed yet.</div>`;
        return;
      }
      dom.recentJobs.innerHTML = jobs
        .map((job) => {
          const wall = job.metrics ? fmtSeconds(Number(job.metrics.wall_time_s)) : "-";
          const canKill = job.status === "running";
          const status = String(job.status || "unknown");
          const isFailedBadge = status === "failed" || status === "killed";
          const chipAttr = isFailedBadge ? `data-open-job-chip="${sanitize(job.job_id)}"` : "";
          return `
            <article class="job-item">
              <div class="job-row">
                <strong>${sanitize(job.job_id.slice(0, 12))}</strong>
                <span class="chip ${sanitize(status)} ${isFailedBadge ? "chip-clickable" : ""}" ${chipAttr}>${sanitize(status)}</span>
              </div>
              <div class="job-row">
                <span>${sanitize(job.request.execution_strategy)}</span>
                <span>${sanitize(wall)}</span>
              </div>
              <div class="job-row">
                <span>${sanitize(job.created_at || "-")}</span>
                <div class="row gap-s">
                  <button class="btn btn-ghost btn-small" data-open-job="${sanitize(job.job_id)}">Open</button>
                  ${canKill ? `<button class="btn btn-danger btn-small" data-kill-job="${sanitize(job.job_id)}">Kill</button>` : ""}
                </div>
              </div>
            </article>
          `;
        })
        .join("");
      dom.recentJobs.querySelectorAll("[data-open-job]").forEach((button) => {
        button.addEventListener("click", async () => {
          const jobId = button.getAttribute("data-open-job");
          await openPlayJob(jobId);
        });
      });
      dom.recentJobs.querySelectorAll("[data-open-job-chip]").forEach((chip) => {
        chip.addEventListener("click", async () => {
          const jobId = chip.getAttribute("data-open-job-chip");
          await openPlayJob(jobId);
        });
      });
      dom.recentJobs.querySelectorAll("[data-kill-job]").forEach((button) => {
        button.addEventListener("click", async () => {
          const jobId = button.getAttribute("data-kill-job");
          if (!jobId) return;
          try {
            await api(`/api/v1/playground/jobs/${jobId}`, { method: "DELETE" });
            if (state.currentJobId === jobId) {
              stopPollingCurrentJob();
              setBusy(false);
              setJobStatus("killed");
            }
            await refreshJobList();
          } catch (err) {
            setRunError(`Unable to kill ${jobId}: ${err.message}`);
          }
        });
      });
    } catch (err) {
      renderQueueVisualizer();
      dom.recentJobs.innerHTML = `<div class="muted">Failed to load jobs: ${sanitize(err.message)}</div>`;
    }
  };

  const loadGallery = async () => {
    try {
      const payload = await api("/api/v1/docs/gallery");
      state.examples = payload.examples || [];
      const modules = ["all", ...(payload.modules || [])];
      dom.moduleFilter.innerHTML = modules
        .map((moduleName) => `<option value="${sanitize(moduleName)}">${sanitize(moduleName)}</option>`)
        .join("");
      renderGalleryCards("all");
    } catch (err) {
      dom.galleryCards.innerHTML = `<div class="muted">Unable to load gallery: ${sanitize(err.message)}</div>`;
    }
  };

  const renderGalleryCards = (moduleFilter) => {
    dom.galleryCards.innerHTML = "";
    const items = state.examples.filter((example) => moduleFilter === "all" || example.module === moduleFilter);
    if (!items.length) {
      dom.galleryCards.innerHTML = `<div class="muted">No examples for selected module.</div>`;
      return;
    }
    for (const example of items) {
      const card = document.createElement("article");
      card.className = "gallery-item";
      card.innerHTML = `
        <h3>${sanitize(example.title)}</h3>
        <div class="gallery-meta">
          <span class="mini-chip">${sanitize(example.module)}</span>
          <span class="mini-chip">${sanitize(example.level)}</span>
          <span class="mini-chip">${sanitize(example.strategy || "dask")}</span>
        </div>
        <p class="muted">${sanitize(example.description || "")}</p>
        <pre class="gallery-code">${sanitize(example.code)}</pre>
        <div class="row gap-s">
          <button class="btn btn-primary btn-small" data-load-example="${sanitize(example.id)}">Load</button>
          <button class="btn btn-ghost btn-small" data-run-example="${sanitize(example.id)}">Run</button>
        </div>
      `;
      dom.galleryCards.appendChild(card);
    }

    dom.galleryCards.querySelectorAll("[data-load-example]").forEach((button) => {
      button.addEventListener("click", async () => {
        const exampleId = button.getAttribute("data-load-example");
        const selected = state.examples.find((item) => item.id === exampleId);
        if (!selected) return;
        dom.programInput.value = selected.code;
        if (dom.executionStrategy) dom.executionStrategy.value = "dask";
        renderEditorTokenOverlay();
        await refreshProgramSymbols();
        activateTab("playground");
      });
    });

    dom.galleryCards.querySelectorAll("[data-run-example]").forEach((button) => {
      button.addEventListener("click", async () => {
        const exampleId = button.getAttribute("data-run-example");
        const selected = state.examples.find((item) => item.id === exampleId);
        if (!selected) return;
        dom.programInput.value = selected.code;
        if (dom.executionStrategy) dom.executionStrategy.value = "dask";
        renderEditorTokenOverlay();
        await refreshProgramSymbols();
        activateTab("playground");
        await createPlaygroundJob();
      });
    });
  };

  const refreshQualityReport = async () => {
    try {
      const report = await api("/api/v1/testing/report");
      const junit = report.junit && report.junit.summary ? report.junit.summary : {};
      const coverage = report.coverage && report.coverage.summary ? report.coverage.summary : {};
      dom.qTotalTests.textContent = `${Number(junit.total || 0)}`;
      dom.qPassed.textContent = `${Number(junit.passed || 0)}`;
      dom.qFailed.textContent = `${Number(junit.failed || 0)}`;
      dom.qSkipped.textContent = `${Number(junit.skipped || 0)}`;
      dom.qDuration.textContent = fmtSeconds(Number(junit.duration_s || 0));
      dom.qCoverage.textContent = `${Number(coverage.line_percent || 0).toFixed(2)}%`;

      const modules = (report.coverage && report.coverage.lowest_modules) || [];
      renderBarList(dom.coverageBars, modules, "line_percent", "name", (v) => `${Number(v).toFixed(2)}%`);

      const failed = (report.junit && report.junit.failed_cases) || [];
      const allCases = (report.junit && report.junit.test_cases) || [];
      if (!failed.length) {
        dom.failedCases.innerHTML = `<div class="muted">No failures in current report.</div>`;
      } else {
        dom.failedCases.innerHTML = failed
          .map(
            (item) => `
              <article class="table-like-row">
                <div class="name">${sanitize(item.id)}</div>
                <div class="detail">${sanitize(item.message || "Failure without message")}</div>
              </article>
            `,
          )
          .join("");
      }
      if (!allCases.length) {
        dom.testTitles.innerHTML = `<div class="muted">No test titles in current report.</div>`;
      } else {
        dom.testTitles.innerHTML = allCases
          .slice(0, 200)
          .map(
            (item) => `
              <article class="table-like-row">
                <div class="name">${sanitize(item.id)}</div>
                <div class="detail">status=${sanitize(item.status)} | time=${sanitize(fmtSeconds(Number(item.time_s || 0)))}</div>
              </article>
            `,
          )
          .join("");
      }

      const perf = report.performance || {};
      if (perf.available) {
        const speedRatio = Number(perf.speed_ratio || 0);
        const vox1 = Number(perf.vox1_median_s || 0);
        const vox2 = Number(perf.vox2_median_s || 0);
        const vox1Cpu = Number(perf.vox1_cpu_median_s || 0);
        const vox2Cpu = Number(perf.vox2_cpu_median_s || 0);
        const vox1Mem = Number(perf.vox1_ru_maxrss_delta_median_bytes || 0);
        const vox2Mem = Number(perf.vox2_ru_maxrss_delta_median_bytes || 0);
        const telemetry = perf.test_metrics || {};
        let telemetrySummary = "";
        if (telemetry.available) {
          const tests = telemetry.tests || [];
          const medCpuUtil = median(tests.map((t) => Number(t.cpu_utilization)));
          const medHeap = median(tests.map((t) => Number(t.python_heap_peak_bytes)));
          const medRss = median(tests.map((t) => Number(t.ru_maxrss_delta_bytes)));
          telemetrySummary =
            ` | perf tests: ${Number(telemetry.count || tests.length)} ` +
            `| median util: ${fmtPercent(medCpuUtil)} ` +
            `| median heap: ${fmtBytes(medHeap)} ` +
            `| median rss delta: ${fmtBytes(medRss)}`;
        }
        dom.perfSummary.textContent =
          `vox1 median: ${fmtSeconds(vox1)} (cpu ${fmtSeconds(vox1Cpu)}, rss ${fmtBytes(vox1Mem)}) ` +
          `| vox2 median: ${fmtSeconds(vox2)} (cpu ${fmtSeconds(vox2Cpu)}, rss ${fmtBytes(vox2Mem)}) ` +
          `| ratio vox1/vox2: ${speedRatio.toFixed(2)}x${telemetrySummary}`;
        dom.perfChart.src = `/api/v1/testing/performance/chart?t=${Date.now()}`;
        dom.perfChart.classList.remove("hidden");
        const primitive = perf.primitive_benchmarks || {};
        if (primitive.available) {
          const cases = primitive.cases || [];
          renderBarList(
            dom.primitivePerfBars,
            cases,
            "speed_ratio",
            "name",
            (v) => `${Number(v).toFixed(3)}x`,
          );
          if (primitive.svg_available) {
            dom.primitivePerfChart.src = `/api/v1/testing/performance/primitive-chart?t=${Date.now()}`;
            dom.primitivePerfChart.classList.remove("hidden");
          } else {
            dom.primitivePerfChart.classList.add("hidden");
          }
        } else {
          const reason = primitive.reason || "No primitive benchmark report yet.";
          dom.primitivePerfBars.innerHTML = `<div class="muted">${sanitize(reason)}</div>`;
          dom.primitivePerfChart.classList.add("hidden");
        }
      } else {
        dom.perfSummary.textContent = "No performance report yet. Run `./tests/run-tests.sh` with perf tests enabled.";
        dom.perfChart.classList.add("hidden");
        dom.primitivePerfBars.innerHTML = `<div class="muted">No primitive benchmark report yet.</div>`;
        dom.primitivePerfChart.classList.add("hidden");
      }
    } catch (err) {
      dom.perfSummary.textContent = `Failed loading report: ${err.message}`;
    }
  };

  const setTestingControlsBusy = (running) => {
    dom.runQuickTestsBtn.disabled = running;
    dom.runFullTestsBtn.disabled = running;
    dom.runPerfTestsBtn.disabled = running;
    dom.killTestRunBtn.disabled = !running;
  };

  const setTestingUnavailableMessage = (message) => {
    setTestingControlsBusy(false);
    dom.runQuickTestsBtn.disabled = true;
    dom.runFullTestsBtn.disabled = true;
    dom.runPerfTestsBtn.disabled = true;
    dom.killTestRunBtn.disabled = true;
    setTestRunStatus("unavailable");
    dom.testRunLog.textContent = message;
  };

  const refreshTestingJobs = async () => {
    try {
      const payload = await api("/api/v1/testing/jobs");
      const jobs = payload.jobs || [];
      if (!jobs.length) {
        dom.recentTestJobs.innerHTML = `<div class="muted">No test jobs started from UI yet.</div>`;
        return;
      }
      dom.recentTestJobs.innerHTML = jobs
        .map((job) => {
          const canKill = job.status === "running";
          return `
            <article class="job-item">
              <div class="job-row">
                <strong>${sanitize(job.job_id.slice(0, 12))}</strong>
                <span class="chip ${sanitize(job.status)}">${sanitize(job.status)}</span>
              </div>
              <div class="job-row">
                <span>${sanitize(job.profile)}</span>
                <span>${sanitize(job.include_perf ? "with perf" : "no perf")}</span>
              </div>
              <div class="job-row">
                <span>${sanitize(job.created_at || "-")}</span>
                ${canKill ? `<button class="btn btn-danger btn-small" data-kill-test-job="${sanitize(job.job_id)}">Kill</button>` : ""}
              </div>
            </article>
          `;
        })
        .join("");
      dom.recentTestJobs.querySelectorAll("[data-kill-test-job]").forEach((button) => {
        button.addEventListener("click", async () => {
          const jobId = button.getAttribute("data-kill-test-job");
          if (!jobId) return;
          await api(`/api/v1/testing/jobs/${jobId}`, { method: "DELETE" });
          if (state.currentTestJobId === jobId) {
            stopPollingCurrentTestJob();
            setTestingControlsBusy(false);
            setTestRunStatus("killed");
          }
          await refreshTestingJobs();
        });
      });
    } catch (err) {
      dom.recentTestJobs.innerHTML = `<div class="muted">Failed to load test jobs: ${sanitize(err.message)}</div>`;
    }
  };

  const pollCurrentTestJob = async () => {
    if (!state.currentTestJobId) return;
    try {
      const job = await api(`/api/v1/testing/jobs/${state.currentTestJobId}`);
      setTestRunStatus(job.status);
      dom.testRunLog.textContent = job.log_tail || "";
      if (job.status === "running") {
        return;
      }
      stopPollingCurrentTestJob();
      setTestingControlsBusy(false);
      await Promise.all([refreshQualityReport(), refreshTestingJobs()]);
    } catch (err) {
      stopPollingCurrentTestJob();
      setTestingControlsBusy(false);
      setTestRunStatus("failed");
      dom.testRunLog.textContent = `Unable to poll test job: ${err.message}`;
    }
  };

  const startTestingJob = async (profile, includePerf) => {
    if (state.capabilities.testing_jobs === false) {
      setTestingUnavailableMessage("Testing jobs API unavailable. Restart `./voxlogica serve` from latest code.");
      return;
    }
    try {
      setTestingControlsBusy(true);
      setTestRunStatus("running");
      dom.testRunLog.textContent = "";
      const job = await api("/api/v1/testing/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile, include_perf: includePerf }),
      });
      state.currentTestJobId = job.job_id;
      dom.testRunLog.textContent = job.log_tail || "";
      stopPollingCurrentTestJob();
      state.testPollTimer = setInterval(pollCurrentTestJob, 1200);
      await Promise.all([pollCurrentTestJob(), refreshTestingJobs()]);
    } catch (err) {
      setTestingControlsBusy(false);
      setTestRunStatus("failed");
      dom.testRunLog.textContent = `Unable to start test run: ${err.message}`;
    }
  };

  const killCurrentTestJob = async () => {
    if (!state.currentTestJobId) return;
    try {
      const job = await api(`/api/v1/testing/jobs/${state.currentTestJobId}`, { method: "DELETE" });
      stopPollingCurrentTestJob();
      setTestingControlsBusy(false);
      setTestRunStatus(job.status);
      dom.testRunLog.textContent = job.log_tail || "";
      await refreshTestingJobs();
    } catch (err) {
      dom.testRunLog.textContent = `Unable to kill current test job: ${err.message}`;
    }
  };

  const loadCapabilities = async () => {
    try {
      const caps = await api("/api/v1/capabilities");
      state.capabilities = caps || {};
      if (state.capabilities.testing_jobs === false) {
        setTestingUnavailableMessage("This backend does not expose testing jobs. Restart server from latest commit.");
      }
      if (state.capabilities.playground_program_library === false) {
        dom.programLibraryMeta.textContent = "Program library endpoint unavailable on this backend.";
        dom.loadProgramFileBtn.disabled = true;
      }
      if (state.capabilities.store_results_viewer === false) {
        dom.resultListMeta.textContent = "Store results viewer unavailable on this backend.";
        dom.playResultMeta.textContent = "Stored-value inspection unavailable on this backend.";
      }
      if (state.capabilities.playground_symbols === false || state.capabilities.playground_value_resolver === false) {
        dom.playResultMeta.textContent = "Lazy click-to-inspect is unavailable on this backend.";
      }
    } catch (err) {
      if (String(err.message).includes("404")) {
        state.capabilities.testing_jobs = false;
        setTestingUnavailableMessage(
          "Backend is older than UI (missing /api/v1/capabilities). Stop and restart `./voxlogica serve`."
        );
      }
    }
  };

  const refreshStorageStats = async () => {
    try {
      const stats = await api("/api/v1/storage/stats");
      if (!stats.available) {
        dom.storageMeta.textContent = stats.error || "Storage stats unavailable.";
        return;
      }
      const summary = stats.summary || {};
      const disk = stats.disk || {};
      dom.sTotal.textContent = `${Number(summary.total_records || 0)}`;
      dom.sMaterialized.textContent = `${Number(summary.materialized_records || 0)}`;
      dom.sFailed.textContent = `${Number(summary.failed_records || 0)}`;
      dom.sAvgPayload.textContent = fmtBytes(Number(summary.avg_payload_bytes || 0));
      dom.sTotalPayload.textContent = fmtBytes(Number(summary.total_payload_bytes || 0));
      dom.sDbSize.textContent = fmtBytes(Number(disk.db_bytes || 0) + Number(disk.wal_bytes || 0));
      dom.storageMeta.textContent = `${stats.db_path} | last update ${summary.last_update_at || "-"}`;

      renderBarList(dom.payloadBuckets, stats.payload_buckets || [], "count", "bucket", (v) => `${v}`);
      renderBarList(dom.runtimeVersions, stats.runtime_versions || [], "count", "runtime_version", (v) => `${v}`);
    } catch (err) {
      dom.storageMeta.textContent = `Failed loading storage stats: ${err.message}`;
    }
  };

  const activateTab = (tabName) => {
    state.activeTab = tabName;
    dom.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabName));
    dom.panels.forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${tabName}`));
    if (tabName === "results") {
      refreshStoreResults();
    }
    if (tabName === "playground") {
      loadProgramLibrary();
      refreshProgramSymbols();
    }
  };

  const connectLiveReload = () => {
    const wsProto = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${wsProto}://${window.location.host}/livereload`);
    socket.onmessage = (event) => {
      if (event.data === "reload") {
        window.location.reload();
      }
    };
    socket.onclose = () => setTimeout(connectLiveReload, 2000);
  };

  const loadVersionStamp = async () => {
    try {
      const version = await api("/api/v1/version");
      dom.buildStamp.textContent = `v${version.version || "unknown"}`;
    } catch (err) {
      dom.buildStamp.textContent = "version unavailable";
    }
  };

  const bindEvents = () => {
    let resultSearchDebounce = null;
    ensureEditorTokenOverlay();
    dom.tabs.forEach((tab) => {
      tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    });
    if (dom.runProgramBtn) {
      dom.runProgramBtn.classList.add("hidden");
    }
    dom.killProgramBtn.addEventListener("click", killCurrentJob);
    dom.clearOutputBtn.addEventListener("click", clearOutput);
    dom.programInput.addEventListener("input", () => {
      state.latestSymbolTable = {};
      state.latestGoalResults = [];
      renderEditorTokenOverlay();
      scheduleProgramSymbolsRefresh();
      clearHoverTokenState();
    });
    dom.programInput.addEventListener("mousemove", handleProgramHover);
    dom.programInput.addEventListener("scroll", () => {
      syncEditorOverlayScroll();
      clearHoverTokenState();
    });
    if (dom.editorShell) {
      dom.editorShell.addEventListener("mouseleave", () => {
        state.lastHoverToken = "";
        clearHoverTokenState();
        if (state.hoverTimer) {
          clearTimeout(state.hoverTimer);
          state.hoverTimer = null;
        }
      });
    }
    dom.programInput.addEventListener("mouseleave", () => {
      state.lastHoverToken = "";
      clearHoverTokenState();
      if (state.hoverTimer) {
        clearTimeout(state.hoverTimer);
        state.hoverTimer = null;
      }
    });
    dom.loadProgramFileBtn.addEventListener("click", loadProgramFromLibrary);
    dom.playResultSelector.addEventListener("change", async () => {
      const idx = Number(dom.playResultSelector.value);
      if (!Number.isFinite(idx)) return;
      const target = state.playSelectableTargets[idx];
      if (!target || !target.nodeId) return;
      await inspectPlayTarget(target.nodeId, "", { variable: target.kind === "variable" ? target.label : "" });
    });
    dom.refreshJobsBtn.addEventListener("click", refreshJobList);
    dom.moduleFilter.addEventListener("change", () => renderGalleryCards(dom.moduleFilter.value));
    dom.refreshQualityBtn.addEventListener("click", refreshQualityReport);
    dom.runQuickTestsBtn.addEventListener("click", () => startTestingJob("quick", false));
    dom.runFullTestsBtn.addEventListener("click", () => startTestingJob("full", true));
    dom.runPerfTestsBtn.addEventListener("click", () => startTestingJob("perf", true));
    dom.killTestRunBtn.addEventListener("click", killCurrentTestJob);
    dom.refreshStorageBtn.addEventListener("click", refreshStorageStats);
    dom.refreshResultsBtn.addEventListener("click", refreshStoreResults);
    dom.resultStatusFilter.addEventListener("change", refreshStoreResults);
    dom.resultSearchInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") refreshStoreResults();
    });
    dom.resultSearchInput.addEventListener("input", () => {
      if (resultSearchDebounce) clearTimeout(resultSearchDebounce);
      resultSearchDebounce = setTimeout(refreshStoreResults, 260);
    });
  };

  const init = async () => {
    bindEvents();
    setBusy(false);
    setJobStatus("idle");
    setTestingControlsBusy(false);
    setTestRunStatus("idle");
    dom.playResultSelector.disabled = true;
    dom.playResultMeta.textContent = "Click inspect on a variable to resolve it on demand.";
    ensureResultViewer();
    ensurePlayResultViewer();
    ensureQueueVisualizer();
    renderQueueVisualizer();
    await Promise.all([
      loadCapabilities(),
      loadVersionStamp(),
      loadProgramLibrary(),
      loadGallery(),
      refreshJobList(),
      refreshQualityReport(),
      refreshTestingJobs(),
      refreshStorageStats(),
      refreshStoreResults(),
    ]);
    await refreshProgramSymbols();
    connectLiveReload();
    setInterval(() => {
      if (state.activeTab === "quality") {
        refreshQualityReport();
        refreshTestingJobs();
        pollCurrentTestJob();
      }
      if (state.activeTab === "storage") {
        refreshStorageStats();
      }
      if (state.activeTab === "playground") {
        refreshJobList();
        refreshProgramSymbols();
      }
      if (state.activeTab === "results") {
        refreshStoreResults();
      }
    }, 15000);
  };

  init();
})();
