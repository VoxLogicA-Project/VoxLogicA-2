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
  };

  const dom = {
    tabs: Array.from(document.querySelectorAll(".tab")),
    panels: Array.from(document.querySelectorAll(".panel")),
    buildStamp: document.getElementById("buildStamp"),
    programInput: document.getElementById("programInput"),
    executionStrategy: document.getElementById("executionStrategy"),
    noCache: document.getElementById("noCache"),
    programLibrarySelect: document.getElementById("programLibrarySelect"),
    loadProgramFileBtn: document.getElementById("loadProgramFileBtn"),
    programLibraryMeta: document.getElementById("programLibraryMeta"),
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
      const detail = payload.detail || `${response.status} ${response.statusText}`;
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
    dom.runProgramBtn.disabled = busy;
    dom.killProgramBtn.disabled = !busy;
    dom.executionStrategy.disabled = busy;
    dom.noCache.disabled = busy;
  };

  const renderExecutionPayload = async (job) => {
    const result = (job && job.result) || {};
    dom.executionOutput.textContent = result ? JSON.stringify(result, null, 2) : "";
    dom.taskGraphOutput.textContent = result.task_graph || "(no task graph in payload)";
    renderPlayExecutionLog(result);
    await refreshPlaySelector(result);
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

  const onJobTerminal = async (job) => {
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
      setJobStatus(job.status);
      if (job.status === "running") {
        renderRuntimeMetrics(job);
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
    const payload = {
      program: dom.programInput.value,
      execute: true,
      no_cache: dom.noCache.checked,
      execution_strategy: dom.executionStrategy.value,
    };
    try {
      setBusy(true);
      setJobStatus("running");
      dom.executionOutput.textContent = "";
      dom.taskGraphOutput.textContent = "";
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
    dom.playExecSummary.textContent = "No execution trace yet.";
    dom.playExecLog.innerHTML = `<div class="muted">Run a query to see computed vs cached nodes.</div>`;
    state.latestGoalResults = [];
    state.latestSymbolTable = {};
    state.playSelectableTargets = [];
    dom.playResultSelector.innerHTML = `<option value="">Run a query first</option>`;
    dom.playResultSelector.disabled = true;
    dom.playResultMeta.textContent = "Choose a print label or variable to inspect.";
    ensurePlayResultViewer();
    state.playResultViewer.renderRecord(null);
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

  const renderPlayExecutionLog = (runResult) => {
    const execution = (runResult && runResult.execution) || {};
    const summary = execution.cache_summary || {};
    const events = Array.isArray(execution.node_events) ? execution.node_events : [];
    dom.playExecSummary.textContent =
      `computed ${Number(summary.computed || 0)} | ` +
      `cached(store) ${Number(summary.cached_store || 0)} | ` +
      `cached(local) ${Number(summary.cached_local || 0)} | ` +
      `failed ${Number(summary.failed || 0)} | ` +
      `events ${Number(summary.events_stored || events.length)}/${Number(summary.events_total || events.length)}`;
    if (!events.length) {
      dom.playExecLog.innerHTML = `<div class="muted">No node events available for this run.</div>`;
      return;
    }
    const rows = events.slice(-260).reverse();
    dom.playExecLog.innerHTML = rows
      .map((event) => {
        const status = sanitize(event.status || "unknown");
        const source = sanitize(event.cache_source || "-");
        const operator = sanitize(event.operator || "-");
        const durationMs = `${(Number(event.duration_s || 0) * 1000).toFixed(2)} ms`;
        const node = sanitize(String(event.node_id || "").slice(0, 12));
        const extra = event.error ? ` | ${sanitize(String(event.error))}` : "";
        return `
          <article class="table-like-row">
            <div class="name"><span class="mini-chip">${status}</span> ${operator}</div>
            <div class="detail">node=${node} | source=${source} | duration=${durationMs}${extra}</div>
          </article>
        `;
      })
      .join("");
  };

  const inspectPlayTarget = async (nodeId, path = "") => {
    ensurePlayResultViewer();
    state.playResultViewer.setLoading(`Loading value for ${nodeId} ...`);
    try {
      const suffix = path ? `?path=${encodeURIComponent(path)}` : "";
      const payload = await api(`/api/v1/results/store/${encodeURIComponent(nodeId)}${suffix}`);
      state.playResultViewer.renderRecord(payload);
      dom.playResultMeta.textContent =
        `Inspecting ${nodeId.slice(0, 12)}${path ? ` @ ${path}` : ""} from persisted store.`;
    } catch (err) {
      state.playResultViewer.setError(
        `Stored value unavailable: ${err.message}. Run with cache enabled to inspect this target.`,
      );
      dom.playResultMeta.textContent = `Unable to inspect ${nodeId}: ${err.message}`;
    }
  };

  const refreshPlaySelector = async (runResult) => {
    ensurePlayResultViewer();
    state.latestGoalResults = Array.isArray(runResult && runResult.goal_results) ? runResult.goal_results : [];
    state.latestSymbolTable = (runResult && runResult.symbol_table) || {};
    const targets = [];

    for (const goal of state.latestGoalResults) {
      if (goal && goal.operation === "print" && goal.node_id) {
        targets.push({
          kind: "print",
          label: String(goal.name || "print"),
          nodeId: String(goal.node_id),
        });
      }
    }
    for (const [name, nodeId] of Object.entries(state.latestSymbolTable)) {
      if (!nodeId || typeof nodeId !== "string") continue;
      targets.push({
        kind: "variable",
        label: String(name),
        nodeId,
      });
    }

    state.playSelectableTargets = targets;
    if (!targets.length) {
      dom.playResultSelector.innerHTML = `<option value="">No print labels or variables available</option>`;
      dom.playResultSelector.disabled = true;
      dom.playResultMeta.textContent = "No selectable targets in this query.";
      state.playResultViewer.renderRecord(null);
      return;
    }

    dom.playResultSelector.disabled = false;
    dom.playResultSelector.innerHTML = targets
      .map(
        (target, index) =>
          `<option value="${index}">${sanitize(target.kind === "print" ? `print: ${target.label}` : `var: ${target.label}`)}</option>`,
      )
      .join("");
    dom.playResultSelector.value = "0";
    await inspectPlayTarget(targets[0].nodeId, "");
  };

  const handleProgramDoubleClick = async () => {
    const text = dom.programInput.value || "";
    let token = (dom.programInput.value || "").slice(dom.programInput.selectionStart, dom.programInput.selectionEnd).trim();
    if (!token) {
      const pos = dom.programInput.selectionStart || 0;
      const left = text.slice(0, pos);
      const right = text.slice(pos);
      const leftMatch = left.match(/[A-Za-z0-9_.$+\-*/<>=!?~]+$/);
      const rightMatch = right.match(/^[A-Za-z0-9_.$+\-*/<>=!?~]+/);
      token = `${leftMatch ? leftMatch[0] : ""}${rightMatch ? rightMatch[0] : ""}`.trim();
    }
    token = token.replace(/^[^A-Za-z0-9_.$+\-*/<>=!?~]+|[^A-Za-z0-9_.$+\-*/<>=!?~]+$/g, "");
    if (!token) return;
    const nodeId = state.latestSymbolTable[token];
    if (!nodeId || typeof nodeId !== "string") return;

    const index = state.playSelectableTargets.findIndex((target) => target.kind === "variable" && target.label === token);
    if (index >= 0) {
      dom.playResultSelector.value = `${index}`;
    }
    await inspectPlayTarget(nodeId, "");
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

  const refreshJobList = async () => {
    try {
      const payload = await api("/api/v1/playground/jobs");
      const jobs = payload.jobs || [];
      if (!jobs.length) {
        dom.recentJobs.innerHTML = `<div class="muted">No jobs executed yet.</div>`;
        return;
      }
      dom.recentJobs.innerHTML = jobs
        .map((job) => {
          const wall = job.metrics ? fmtSeconds(Number(job.metrics.wall_time_s)) : "-";
          const canKill = job.status === "running";
          return `
            <article class="job-item">
              <div class="job-row">
                <strong>${sanitize(job.job_id.slice(0, 12))}</strong>
                <span class="chip ${sanitize(job.status)}">${sanitize(job.status)}</span>
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
          if (!jobId) return;
          try {
            const job = await api(`/api/v1/playground/jobs/${jobId}`);
            state.currentJobId = jobId;
            setJobStatus(job.status);
            renderRuntimeMetrics(job);
            await renderExecutionPayload(job);
            if (job.error) {
              setRunError(job.error);
            }
          } catch (err) {
            setRunError(`Unable to open ${jobId}: ${err.message}`);
          }
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
          <span class="mini-chip">${sanitize(example.strategy || "strict")}</span>
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
      button.addEventListener("click", () => {
        const exampleId = button.getAttribute("data-load-example");
        const selected = state.examples.find((item) => item.id === exampleId);
        if (!selected) return;
        dom.programInput.value = selected.code;
        if (selected.strategy) dom.executionStrategy.value = selected.strategy;
        activateTab("playground");
      });
    });

    dom.galleryCards.querySelectorAll("[data-run-example]").forEach((button) => {
      button.addEventListener("click", async () => {
        const exampleId = button.getAttribute("data-run-example");
        const selected = state.examples.find((item) => item.id === exampleId);
        if (!selected) return;
        dom.programInput.value = selected.code;
        if (selected.strategy) dom.executionStrategy.value = selected.strategy;
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
    dom.tabs.forEach((tab) => {
      tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    });
    dom.runProgramBtn.addEventListener("click", createPlaygroundJob);
    dom.killProgramBtn.addEventListener("click", killCurrentJob);
    dom.clearOutputBtn.addEventListener("click", clearOutput);
    dom.programInput.addEventListener("dblclick", handleProgramDoubleClick);
    dom.loadProgramFileBtn.addEventListener("click", loadProgramFromLibrary);
    dom.playResultSelector.addEventListener("change", async () => {
      const idx = Number(dom.playResultSelector.value);
      if (!Number.isFinite(idx)) return;
      const target = state.playSelectableTargets[idx];
      if (!target || !target.nodeId) return;
      await inspectPlayTarget(target.nodeId, "");
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
    ensureResultViewer();
    ensurePlayResultViewer();
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
      }
      if (state.activeTab === "results") {
        refreshStoreResults();
      }
    }, 15000);
  };

  init();
})();
