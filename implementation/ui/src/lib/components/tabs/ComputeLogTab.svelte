<script>
  import { onDestroy } from "svelte";
  import { getPlaygroundJob, listPlaygroundJobs } from "$lib/api/client.js";
  import StatusChip from "$lib/components/shared/StatusChip.svelte";

  export let active = false;

  let jobs = [];
  let selectedJob = null;
  let selectedJobId = "";
  let loading = false;
  let errorText = "";
  let pollTimer = null;
  let pollInFlight = false;

  const toTs = (value) => {
    const raw = String(value || "").trim();
    if (!raw) return "-";
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return raw;
    return date.toLocaleString();
  };

  const normalizedStatus = (value) => String(value || "idle").toLowerCase();

  const sortJobs = (items) =>
    [...(Array.isArray(items) ? items : [])].sort((a, b) => {
      const aTime = Date.parse(String(a?.created_at || a?.started_at || a?.finished_at || "")) || 0;
      const bTime = Date.parse(String(b?.created_at || b?.started_at || b?.finished_at || "")) || 0;
      return bTime - aTime;
    });

  const refreshSelectedJob = async () => {
    if (!selectedJobId) {
      selectedJob = null;
      return;
    }
    try {
      selectedJob = await getPlaygroundJob(selectedJobId);
      errorText = "";
    } catch (error) {
      selectedJob = null;
      errorText = `Unable to load job details: ${error.message}`;
    }
  };

  const refreshJobs = async () => {
    try {
      const payload = await listPlaygroundJobs();
      jobs = sortJobs(payload?.jobs || []);
      if (!selectedJobId && jobs.length) {
        selectedJobId = String(jobs[0]?.job_id || "");
      }
      if (selectedJobId && !jobs.some((job) => String(job?.job_id || "") === selectedJobId)) {
        selectedJobId = jobs.length ? String(jobs[0]?.job_id || "") : "";
      }
      await refreshSelectedJob();
      errorText = "";
    } catch (error) {
      jobs = [];
      selectedJob = null;
      selectedJobId = "";
      errorText = `Unable to load compute jobs: ${error.message}`;
    }
  };

  const pollOnce = async () => {
    if (pollInFlight) return;
    pollInFlight = true;
    loading = true;
    try {
      await refreshJobs();
    } finally {
      pollInFlight = false;
      loading = false;
    }
  };

  const stopPolling = () => {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
  };

  const startPolling = () => {
    if (pollTimer || !active) return;
    void pollOnce();
    pollTimer = setInterval(() => {
      void pollOnce();
    }, 1400);
  };

  const selectJob = async (jobId) => {
    selectedJobId = String(jobId || "");
    await refreshSelectedJob();
  };

  $: if (active) {
    startPolling();
  } else {
    stopPolling();
  }

  onDestroy(() => {
    stopPolling();
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-compute-log">
  <article class="card compute-log-shell">
    <header class="compute-log-head">
      <h2>Compute Log</h2>
      <button class="btn btn-ghost btn-small" type="button" disabled={loading} on:click={() => void pollOnce()}>Refresh</button>
    </header>

    <div class="compute-log-grid">
      <section class="compute-log-jobs">
        {#if !jobs.length}
          <p class="muted">No jobs yet.</p>
        {:else}
          <div class="job-list">
            {#each jobs as job}
              <button
                type="button"
                class={`job-item compute-log-job ${selectedJobId === String(job.job_id || "") ? "result-item active" : ""}`.trim()}
                on:click={() => void selectJob(job.job_id)}
              >
                <div class="compute-log-job-row">
                  <span class="compute-log-job-id">{String(job.job_id || "-").slice(0, 20)}</span>
                  <StatusChip value={normalizedStatus(job.status)} />
                </div>
                <div class="job-row">
                  <span>{toTs(job.started_at || job.created_at)}</span>
                  <span>{String(job._job_kind || job.kind || "job")}</span>
                </div>
              </button>
            {/each}
          </div>
        {/if}
      </section>

      <section class="compute-log-detail">
        {#if selectedJob}
          <div class="compute-log-meta">
            <div class="viewer-kv-row">
              <span class="viewer-k">job</span>
              <span class="viewer-v">{String(selectedJob.job_id || "-")}</span>
            </div>
            <div class="viewer-kv-row">
              <span class="viewer-k">status</span>
              <span class="viewer-v">{normalizedStatus(selectedJob.status)}</span>
            </div>
            <div class="viewer-kv-row">
              <span class="viewer-k">started</span>
              <span class="viewer-v">{toTs(selectedJob.started_at || selectedJob.created_at)}</span>
            </div>
            <div class="viewer-kv-row">
              <span class="viewer-k">finished</span>
              <span class="viewer-v">{toTs(selectedJob.finished_at)}</span>
            </div>
          </div>
          <pre class="mono-scroll compute-log-text">{String(selectedJob.log_tail || selectedJob.error || "No log output yet.")}</pre>
        {:else}
          <p class="muted">Select a job to inspect its compute log.</p>
        {/if}
      </section>
    </div>

    {#if errorText}
      <div class="inline-error">{errorText}</div>
    {/if}
  </article>
</section>
