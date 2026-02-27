<script>
  import { onDestroy, onMount } from "svelte";
  import {
    getTestingReport,
    getTestingJob,
    killTestingJob,
    listTestingJobs,
    startTestingJob,
  } from "$lib/api/client.js";
  import { fmtBytes, fmtPercent, fmtSeconds, median } from "$lib/utils/format.js";
  import StatusChip from "$lib/components/shared/StatusChip.svelte";
  import BarList from "$lib/components/shared/BarList.svelte";

  export let active = false;
  export let capabilities = {};

  let testRunStatus = "idle";
  let controlsBusy = false;
  let currentTestJobId = "";
  let testRunLog = "";

  let recentJobs = [];
  let recentJobsError = "";

  let junitSummary = {};
  let coverageSummary = {};
  let coverageLowestModules = [];
  let failedCases = [];
  let allCases = [];

  let perfSummaryText = "No performance report yet.";
  let perfChartSrc = "";
  let primitiveChartSrc = "";
  let primitiveBars = [];
  let primitiveBarsMessage = "No primitive benchmark report yet.";

  let refreshTimer = null;
  let pollTimer = null;

  const setUnavailable = (message) => {
    controlsBusy = false;
    testRunStatus = "unavailable";
    testRunLog = message;
  };

  const refreshReport = async () => {
    try {
      const report = await getTestingReport();
      const junit = report?.junit || {};
      const coverage = report?.coverage || {};
      const performance = report?.performance || {};

      junitSummary = junit.summary || {};
      coverageSummary = coverage.summary || {};
      coverageLowestModules = coverage.lowest_modules || [];
      failedCases = junit.failed_cases || [];
      allCases = junit.test_cases || [];

      if (performance.available) {
        const speedRatio = Number(performance.speed_ratio || 0);
        const vox1 = Number(performance.vox1_median_s || 0);
        const vox2 = Number(performance.vox2_median_s || 0);
        const vox1Cpu = Number(performance.vox1_cpu_median_s || 0);
        const vox2Cpu = Number(performance.vox2_cpu_median_s || 0);
        const vox1Mem = Number(performance.vox1_ru_maxrss_delta_median_bytes || 0);
        const vox2Mem = Number(performance.vox2_ru_maxrss_delta_median_bytes || 0);

        let telemetrySummary = "";
        const telemetry = performance.test_metrics || {};
        if (telemetry.available) {
          const tests = telemetry.tests || [];
          telemetrySummary =
            ` | perf tests: ${Number(telemetry.count || tests.length)} ` +
            `| median util: ${fmtPercent(median(tests.map((item) => Number(item.cpu_utilization))))} ` +
            `| median heap: ${fmtBytes(median(tests.map((item) => Number(item.python_heap_peak_bytes))))} ` +
            `| median rss delta: ${fmtBytes(median(tests.map((item) => Number(item.ru_maxrss_delta_bytes))))}`;
        }

        perfSummaryText =
          `vox1 median: ${fmtSeconds(vox1)} (cpu ${fmtSeconds(vox1Cpu)}, rss ${fmtBytes(vox1Mem)}) ` +
          `| vox2 median: ${fmtSeconds(vox2)} (cpu ${fmtSeconds(vox2Cpu)}, rss ${fmtBytes(vox2Mem)}) ` +
          `| ratio vox1/vox2: ${speedRatio.toFixed(2)}x${telemetrySummary}`;
        perfChartSrc = `/api/v1/testing/performance/chart?t=${Date.now()}`;

        const primitive = performance.primitive_benchmarks || {};
        if (primitive.available) {
          primitiveBars = primitive.cases || [];
          primitiveBarsMessage = "";
          primitiveChartSrc = primitive.svg_available ? `/api/v1/testing/performance/primitive-chart?t=${Date.now()}` : "";
        } else {
          primitiveBars = [];
          primitiveBarsMessage = primitive.reason || "No primitive benchmark report yet.";
          primitiveChartSrc = "";
        }
      } else {
        perfSummaryText = "No performance report yet. Run `./tests/run-tests.sh` with perf tests enabled.";
        perfChartSrc = "";
        primitiveChartSrc = "";
        primitiveBars = [];
        primitiveBarsMessage = "No primitive benchmark report yet.";
      }
    } catch (error) {
      perfSummaryText = `Failed loading report: ${error.message}`;
    }
  };

  const refreshJobs = async () => {
    try {
      const payload = await listTestingJobs();
      recentJobs = payload.jobs || [];
      recentJobsError = "";
    } catch (error) {
      recentJobs = [];
      recentJobsError = `Failed to load test jobs: ${error.message}`;
    }
  };

  const pollCurrentJob = async () => {
    if (!currentTestJobId) return;

    try {
      const payload = await getTestingJob(currentTestJobId);
      testRunStatus = payload.status;
      testRunLog = payload.log_tail || "";
      if (payload.status === "running") {
        return;
      }

      controlsBusy = false;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      await Promise.all([refreshReport(), refreshJobs()]);
    } catch (error) {
      controlsBusy = false;
      testRunStatus = "failed";
      testRunLog = `Unable to poll test job: ${error.message}`;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }
  };

  const beginTestRun = async (profile, includePerf) => {
    if (capabilities.testing_jobs === false) {
      setUnavailable("Testing jobs API unavailable. Restart `./voxlogica serve` from latest code.");
      return;
    }

    try {
      controlsBusy = true;
      testRunStatus = "running";
      testRunLog = "";

      const payload = await startTestingJob({ profile, includePerf });
      currentTestJobId = payload.job_id;
      testRunLog = payload.log_tail || "";

      if (pollTimer) {
        clearInterval(pollTimer);
      }
      pollTimer = setInterval(pollCurrentJob, 1200);
      await Promise.all([pollCurrentJob(), refreshJobs()]);
    } catch (error) {
      controlsBusy = false;
      testRunStatus = "failed";
      testRunLog = `Unable to start test run: ${error.message}`;
    }
  };

  const killCurrentRun = async () => {
    if (!currentTestJobId) return;

    try {
      const payload = await killTestingJob(currentTestJobId);
      controlsBusy = false;
      testRunStatus = payload.status;
      testRunLog = payload.log_tail || "";
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      await refreshJobs();
    } catch (error) {
      testRunLog = `Unable to kill current test job: ${error.message}`;
    }
  };

  const killJobById = async (jobId) => {
    try {
      await killTestingJob(jobId);
      if (currentTestJobId === jobId) {
        currentTestJobId = "";
        controlsBusy = false;
        testRunStatus = "killed";
      }
      await refreshJobs();
    } catch (error) {
      recentJobsError = `Unable to kill ${jobId}: ${error.message}`;
    }
  };

  const startActivePolling = () => {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }

    refreshTimer = setInterval(() => {
      if (!active) return;
      refreshReport();
      refreshJobs();
      pollCurrentJob();
    }, 15000);
  };

  const refreshNow = async () => {
    await Promise.all([refreshReport(), refreshJobs()]);
  };

  $: if (active) {
    refreshNow();
    startActivePolling();
  }

  $: if (capabilities.testing_jobs === false) {
    setUnavailable("This backend does not expose testing jobs. Restart server from latest commit.");
  }

  onMount(async () => {
    await refreshNow();
    startActivePolling();
  });

  onDestroy(() => {
    if (refreshTimer) {
      clearInterval(refreshTimer);
    }
    if (pollTimer) {
      clearInterval(pollTimer);
    }
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-quality">
  <div class="grid-two">
    <article class="card">
      <div class="card-header">
        <h2>Test &amp; Coverage Overview</h2>
        <div class="row gap-s">
          <button id="refreshQualityBtn" class="btn btn-ghost btn-small" type="button" on:click={refreshNow}>Refresh</button>
          <StatusChip value={testRunStatus} />
        </div>
      </div>

      <div class="row controls">
        <button id="runQuickTestsBtn" class="btn btn-ghost btn-small" type="button" disabled={controlsBusy || capabilities.testing_jobs === false} on:click={() => beginTestRun("quick", false)}>Run Quick</button>
        <button id="runFullTestsBtn" class="btn btn-primary btn-small" type="button" disabled={controlsBusy || capabilities.testing_jobs === false} on:click={() => beginTestRun("full", true)}>Run Full</button>
        <button id="runPerfTestsBtn" class="btn btn-ghost btn-small" type="button" disabled={controlsBusy || capabilities.testing_jobs === false} on:click={() => beginTestRun("perf", true)}>Run Perf</button>
        <button id="killTestRunBtn" class="btn btn-danger btn-small" type="button" disabled={!controlsBusy} on:click={killCurrentRun}>Kill Test Run</button>
      </div>

      <div class="stats-grid">
        <div class="stat"><span class="label">Total Tests</span><strong id="qTotalTests">{Number(junitSummary.total || 0)}</strong></div>
        <div class="stat"><span class="label">Passed</span><strong id="qPassed">{Number(junitSummary.passed || 0)}</strong></div>
        <div class="stat"><span class="label">Failed</span><strong id="qFailed">{Number(junitSummary.failed || 0)}</strong></div>
        <div class="stat"><span class="label">Skipped</span><strong id="qSkipped">{Number(junitSummary.skipped || 0)}</strong></div>
        <div class="stat"><span class="label">Duration</span><strong id="qDuration">{fmtSeconds(Number(junitSummary.duration_s || 0))}</strong></div>
        <div class="stat"><span class="label">Line Coverage</span><strong id="qCoverage">{Number(coverageSummary.line_percent || 0).toFixed(2)}%</strong></div>
      </div>

      <h3 class="subheading">Lowest Coverage Packages</h3>
      <div id="coverageBars" class="bars">
        <BarList items={coverageLowestModules} valueField="line_percent" labelField="name" formatter={(value) => `${Number(value).toFixed(2)}%`} />
      </div>
    </article>

    <article class="card">
      <h2>VoxLogicA-1 vs VoxLogicA-2 Performance</h2>
      {#if perfChartSrc}
        <img id="perfChart" class="perf-chart" alt="Performance comparison chart" src={perfChartSrc} />
      {/if}
      <div id="perfSummary" class="muted">{perfSummaryText}</div>

      <h3 class="subheading">Primitive Benchmark Histogram</h3>
      {#if primitiveChartSrc}
        <img id="primitivePerfChart" class="perf-chart" alt="Primitive benchmark histogram" src={primitiveChartSrc} />
      {/if}
      <div id="primitivePerfBars" class="bars">
        {#if primitiveBars.length}
          <BarList items={primitiveBars} valueField="speed_ratio" labelField="name" formatter={(value) => `${Number(value).toFixed(3)}x`} />
        {:else}
          <div class="muted">{primitiveBarsMessage}</div>
        {/if}
      </div>
    </article>
  </div>

  <div class="grid-two">
    <article class="card">
      <h2>Test Run Logs</h2>
      <pre id="testRunLog" class="mono-scroll">{testRunLog}</pre>
      <h3 class="subheading">Recent Test Jobs</h3>
      <div id="recentTestJobs" class="job-list">
        {#if recentJobsError}
          <div class="muted">{recentJobsError}</div>
        {:else if !recentJobs.length}
          <div class="muted">No test jobs started from UI yet.</div>
        {:else}
          {#each recentJobs as job}
            <article class="job-item">
              <div class="job-row">
                <strong>{String(job.job_id || "").slice(0, 12)}</strong>
                <span class={`chip ${job.status}`}>{job.status}</span>
              </div>
              <div class="job-row">
                <span>{job.profile}</span>
                <span>{job.include_perf ? "with perf" : "no perf"}</span>
              </div>
              <div class="job-row">
                <span>{job.created_at || "-"}</span>
                {#if job.status === "running"}
                  <button class="btn btn-danger btn-small" type="button" on:click={() => killJobById(job.job_id)}>Kill</button>
                {/if}
              </div>
            </article>
          {/each}
        {/if}
      </div>
    </article>

    <article class="card">
      <h2>Test Titles</h2>
      <div id="testTitles" class="table-like muted">
        {#if !allCases.length}
          <div class="muted">No junit report loaded.</div>
        {:else}
          {#each allCases.slice(0, 200) as item}
            <article class="table-like-row">
              <div class="name">{item.id}</div>
              <div class="detail">status={item.status} | time={fmtSeconds(Number(item.time_s || 0))}</div>
            </article>
          {/each}
        {/if}
      </div>
    </article>
  </div>

  <article class="card">
    <h2>Failing Test Cases</h2>
    <div id="failedCases" class="table-like muted">
      {#if !failedCases.length}
        <div class="muted">No failures in current report.</div>
      {:else}
        {#each failedCases as item}
          <article class="table-like-row">
            <div class="name">{item.id}</div>
            <div class="detail">{item.message || "Failure without message"}</div>
          </article>
        {/each}
      {/if}
    </div>
  </article>
</section>
