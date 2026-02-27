export const parseLogTail = (logTail) => {
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
    } catch {
      // Keep raw non-json lines visible.
    }
    out.push({ event: "raw", message: line });
  }

  return out;
};

export const buildQueueSnapshot = (jobs, currentJob) => {
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
    const status = String(job?.status || "").toLowerCase();
    if (status in counts) {
      counts[status] += 1;
    } else if (status === "success") {
      counts.completed += 1;
    } else {
      counts.other += 1;
    }
  }

  const logEntries = parseLogTail(currentJob?.log_tail || "");
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
    wall_time_s: Number(job?.metrics?.wall_time_s || 0),
    strategy: String(job?.request?.execution_strategy || "dask"),
    kind: String(job?.request?.job_kind || "run"),
  }));

  return {
    generated_at: Date.now(),
    counts,
    jobs: displayJobs,
    node_events: nodeEvents,
    active_job_id: currentJob?.job_id ? String(currentJob.job_id) : "",
  };
};

export const buildExecutionLogRows = (job) => {
  const result = job?.result || {};
  const execution = result?.execution || {};
  const summary = execution.cache_summary || {};
  const logTail = String(job?.log_tail || "");
  const entries = parseLogTail(logTail);
  const nodeEvents = entries.filter((entry) => entry.event === "playground.node");
  const displayedEvents = nodeEvents.length ? nodeEvents : entries.filter((entry) => entry.event !== "raw");

  const summaryText =
    `computed ${Number(summary.computed || 0)} | ` +
    `cached(store) ${Number(summary.cached_store || 0)} | ` +
    `cached(local) ${Number(summary.cached_local || 0)} | ` +
    `failed ${Number(summary.failed || 0)} | ` +
    `events ${Number(summary.events_stored || nodeEvents.length)}/${Number(summary.events_total || nodeEvents.length)}`;

  return {
    raw: logTail,
    summaryText,
    rows: displayedEvents.slice(-220).reverse(),
  };
};
