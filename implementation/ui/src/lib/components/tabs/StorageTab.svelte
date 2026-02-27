<script>
  import { onMount } from "svelte";
  import { getStorageStats } from "$lib/api/client.js";
  import { fmtBytes } from "$lib/utils/format.js";
  import BarList from "$lib/components/shared/BarList.svelte";

  export let active = false;

  let stats = null;
  let summary = null;
  let disk = null;
  let meta = "";
  let error = "";

  const refresh = async () => {
    try {
      const payload = await getStorageStats();
      stats = payload;
      if (!payload.available) {
        error = payload.error || "Storage stats unavailable.";
        meta = error;
        summary = null;
        disk = null;
        return;
      }

      error = "";
      summary = payload.summary || {};
      disk = payload.disk || {};
      meta = `${payload.db_path} | last update ${summary.last_update_at || "-"}`;
    } catch (err) {
      error = `Failed loading storage stats: ${err.message}`;
      meta = error;
      summary = null;
      disk = null;
    }
  };

  $: if (active) {
    refresh();
  }

  onMount(() => {
    refresh();
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-storage">
  <div class="grid-two">
    <article class="card">
      <div class="card-header">
        <h2>Cache Footprint</h2>
        <button id="refreshStorageBtn" class="btn btn-ghost btn-small" type="button" on:click={refresh}>Refresh</button>
      </div>

      <div class="stats-grid">
        <div class="stat">
          <span class="label">Cached Records</span>
          <strong id="sTotal">{summary ? Number(summary.total_records || 0) : "-"}</strong>
        </div>
        <div class="stat">
          <span class="label">Materialized</span>
          <strong id="sMaterialized">{summary ? Number(summary.materialized_records || 0) : "-"}</strong>
        </div>
        <div class="stat">
          <span class="label">Failed</span>
          <strong id="sFailed">{summary ? Number(summary.failed_records || 0) : "-"}</strong>
        </div>
        <div class="stat">
          <span class="label">Average Payload</span>
          <strong id="sAvgPayload">{summary ? fmtBytes(Number(summary.avg_payload_bytes || 0)) : "-"}</strong>
        </div>
        <div class="stat">
          <span class="label">Total Payload</span>
          <strong id="sTotalPayload">{summary ? fmtBytes(Number(summary.total_payload_bytes || 0)) : "-"}</strong>
        </div>
        <div class="stat">
          <span class="label">Database Size</span>
          <strong id="sDbSize">{disk ? fmtBytes(Number(disk.db_bytes || 0) + Number(disk.wal_bytes || 0)) : "-"}</strong>
        </div>
      </div>

      <p id="storageMeta" class="muted">{meta}</p>
      {#if error}
        <div class="inline-error">{error}</div>
      {/if}
    </article>

    <article class="card">
      <h2>Payload Size Distribution</h2>
      <div id="payloadBuckets" class="bars">
        <BarList
          items={stats?.payload_buckets || []}
          valueField="count"
          labelField="bucket"
          formatter={(value) => `${value}`}
        />
      </div>
      <h3 class="subheading">Runtime Versions</h3>
      <div id="runtimeVersions" class="bars">
        <BarList
          items={stats?.runtime_versions || []}
          valueField="count"
          labelField="runtime_version"
          formatter={(value) => `${value}`}
        />
      </div>
    </article>
  </div>
</section>
