<script>
  import { onMount } from "svelte";
  import { fmtBytes } from "$lib/utils/format.js";
  import { inspectStoreResult, inspectStoreResultPage, listStoreResults } from "$lib/api/client.js";

  export let active = false;
  export let capabilities = {};

  let statusFilter = "";
  let search = "";
  let records = [];
  let summaryText = "";
  let errorText = "";
  let currentNodeId = "";
  let currentPath = "";

  let viewerContainer;
  let viewer = null;
  let searchTimer = null;

  const ensureViewer = () => {
    if (viewer) return;

    const ctor = window.VoxResultViewer?.ResultViewer;
    if (typeof ctor === "function") {
      viewer = new ctor(viewerContainer, {
        onNavigate: (path) => {
          if (!currentNodeId) return;
          openRecord(currentNodeId, path || "");
        },
        fetchPage: ({ nodeId, path, offset, limit }) =>
          inspectStoreResultPage({
            nodeId: nodeId || currentNodeId,
            path: path || "",
            offset: Number(offset || 0),
            limit: Number(limit || 64),
          }),
        onStatusClick: (record) => {
          if (!record || (record.status !== "failed" && record.status !== "killed")) return;
          const lines = [
            `node: ${record.node_id || "-"}`,
            `path: ${record.path || "/"}`,
            `status: ${record.status || "failed"}`,
            record.error ? `error: ${record.error}` : "",
          ].filter(Boolean);
          viewer.setError(lines.join("\n"));
        },
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

  const openRecord = async (nodeId, path = "") => {
    ensureViewer();
    currentNodeId = nodeId;
    currentPath = path;
    viewer.setLoading(`Loading ${nodeId}${path || ""} ...`);

    try {
      const payload = await inspectStoreResult(nodeId, path);
      viewer.renderRecord(payload);
      errorText = "";
    } catch (error) {
      viewer.setError(`Failed loading result ${nodeId}: ${error.message}`);
    }
  };

  const refresh = async () => {
    ensureViewer();

    if (capabilities.store_results_viewer === false) {
      records = [];
      summaryText = "Store results viewer unavailable on this backend.";
      errorText = "";
      viewer.setError("Store results endpoint unavailable.");
      return;
    }

    try {
      const payload = await listStoreResults({
        limit: 200,
        statusFilter,
        nodeFilter: search.trim(),
      });

      if (!payload.available) {
        records = [];
        summaryText = payload.error || "Store results unavailable.";
        viewer.setError(payload.error || "Store results unavailable.");
        return;
      }

      records = payload.records || [];
      const summary = payload.summary || {};
      summaryText = `${Number(summary.total || 0)} total | ${Number(summary.materialized || 0)} materialized | ${Number(summary.failed || 0)} failed`;
      errorText = "";

      if (currentNodeId && records.some((record) => record.node_id === currentNodeId)) {
        await openRecord(currentNodeId, currentPath);
        return;
      }

      if (records.length) {
        await openRecord(records[0].node_id, "");
      } else {
        currentNodeId = "";
        currentPath = "";
        viewer.renderRecord(null);
      }
    } catch (error) {
      records = [];
      summaryText = `Unable to load results: ${error.message}`;
      errorText = "";
      viewer.setError(`Unable to load result list: ${error.message}`);
    }
  };

  const onSearchInput = () => {
    if (searchTimer) {
      clearTimeout(searchTimer);
    }
    searchTimer = setTimeout(() => {
      refresh();
    }, 260);
  };

  const onSearchKeydown = (event) => {
    if (event.key === "Enter") {
      refresh();
    }
  };

  $: if (active) {
    refresh();
  }

  onMount(() => {
    ensureViewer();
    refresh();

    return () => {
      if (searchTimer) {
        clearTimeout(searchTimer);
      }
      if (viewer && typeof viewer.destroy === "function") {
        viewer.destroy();
      }
    };
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-results">
  <div class="grid-two">
    <article class="card">
      <div class="card-header">
        <h2>Stored Results</h2>
        <button id="refreshResultsBtn" class="btn btn-ghost btn-small" type="button" on:click={refresh}>Refresh</button>
      </div>

      <div class="row gap-s controls">
        <label class="inline-control" for="resultStatusFilter">Status</label>
        <select id="resultStatusFilter" class="select" bind:value={statusFilter} on:change={refresh}>
          <option value="">all</option>
          <option value="materialized">materialized</option>
          <option value="failed">failed</option>
        </select>
        <input
          id="resultSearchInput"
          class="input"
          type="search"
          placeholder="Filter by node id"
          bind:value={search}
          on:input={onSearchInput}
          on:keydown={onSearchKeydown}
        />
      </div>

      <p id="resultListMeta" class="muted">{summaryText}</p>
      {#if errorText}
        <div class="inline-error">{errorText}</div>
      {/if}

      <div id="resultList" class="job-list">
        {#if !records.length}
          <div class="muted">No stored results found for current runtime.</div>
        {:else}
          {#each records as record}
            <article class={`job-item result-item ${currentNodeId === record.node_id ? "active" : ""}`}>
              <div class="job-row">
                <strong>{record.node_id.slice(0, 16)}</strong>
                <span class={`chip ${record.status}`}>{record.status}</span>
              </div>
              <div class="job-row">
                <span>{record.runtime_version || "-"}</span>
                <span>{fmtBytes(Number(record.payload_bytes || 0))}</span>
              </div>
              <div class="job-row">
                <span>{record.updated_at || "-"}</span>
                <button class="btn btn-ghost btn-small" type="button" on:click={() => openRecord(record.node_id, "")}>Inspect</button>
              </div>
            </article>
          {/each}
        {/if}
      </div>
    </article>

    <article class="card">
      <h2>Result Inspector</h2>
      <p class="muted">
        View values only from persisted store entries. Server-side save/export is intentionally disabled.
      </p>
      <div id="resultInspector" class="result-inspector" bind:this={viewerContainer}></div>
    </article>
  </div>
</section>
