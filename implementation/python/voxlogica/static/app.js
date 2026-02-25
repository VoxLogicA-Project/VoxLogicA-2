(() => {
  const state = {
    activeTab: "playground",
    currentJobId: null,
    pollTimer: null,
    examples: [],
  };

  const dom = {
    tabs: Array.from(document.querySelectorAll(".tab")),
    panels: Array.from(document.querySelectorAll(".panel")),
    buildStamp: document.getElementById("buildStamp"),
    programInput: document.getElementById("programInput"),
    executionStrategy: document.getElementById("executionStrategy"),
    noCache: document.getElementById("noCache"),
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
    moduleFilter: document.getElementById("moduleFilter"),
    galleryCards: document.getElementById("galleryCards"),
    refreshQualityBtn: document.getElementById("refreshQualityBtn"),
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

  const renderExecutionPayload = (job) => {
    const result = (job && job.result) || {};
    dom.executionOutput.textContent = result ? JSON.stringify(result, null, 2) : "";
    dom.taskGraphOutput.textContent = result.task_graph || "(no task graph in payload)";
  };

  const stopPollingCurrentJob = () => {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  };

  const onJobTerminal = async (job) => {
    setBusy(false);
    setJobStatus(job.status);
    renderRuntimeMetrics(job);
    renderExecutionPayload(job);
    if (job.error) {
      setRunError(job.error);
    } else {
      setRunError("");
    }
    await refreshJobList();
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
                ${canKill ? `<button class="btn btn-danger btn-small" data-kill-job="${sanitize(job.job_id)}">Kill</button>` : ""}
              </div>
            </article>
          `;
        })
        .join("");
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

      const perf = report.performance || {};
      if (perf.available) {
        const speedRatio = Number(perf.speed_ratio || 0);
        const vox1 = Number(perf.vox1_median_s || 0);
        const vox2 = Number(perf.vox2_median_s || 0);
        dom.perfSummary.textContent = `vox1 median: ${fmtSeconds(vox1)} | vox2 median: ${fmtSeconds(vox2)} | ratio vox1/vox2: ${speedRatio.toFixed(2)}x`;
        dom.perfChart.src = `/api/v1/testing/performance/chart?t=${Date.now()}`;
        dom.perfChart.classList.remove("hidden");
      } else {
        dom.perfSummary.textContent = "No performance report yet. Run `./tests/run-tests.sh` with perf tests enabled.";
        dom.perfChart.classList.add("hidden");
      }
    } catch (err) {
      dom.perfSummary.textContent = `Failed loading report: ${err.message}`;
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
    dom.tabs.forEach((tab) => {
      tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    });
    dom.runProgramBtn.addEventListener("click", createPlaygroundJob);
    dom.killProgramBtn.addEventListener("click", killCurrentJob);
    dom.clearOutputBtn.addEventListener("click", clearOutput);
    dom.refreshJobsBtn.addEventListener("click", refreshJobList);
    dom.moduleFilter.addEventListener("change", () => renderGalleryCards(dom.moduleFilter.value));
    dom.refreshQualityBtn.addEventListener("click", refreshQualityReport);
    dom.refreshStorageBtn.addEventListener("click", refreshStorageStats);
  };

  const init = async () => {
    bindEvents();
    setBusy(false);
    setJobStatus("idle");
    await Promise.all([
      loadVersionStamp(),
      loadGallery(),
      refreshJobList(),
      refreshQualityReport(),
      refreshStorageStats(),
    ]);
    connectLiveReload();
    setInterval(() => {
      if (state.activeTab === "quality") {
        refreshQualityReport();
      }
      if (state.activeTab === "storage") {
        refreshStorageStats();
      }
      if (state.activeTab === "playground") {
        refreshJobList();
      }
    }, 15000);
  };

  init();
})();
