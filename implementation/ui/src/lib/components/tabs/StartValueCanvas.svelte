<script>
  import StartViewerHost from "$lib/components/tabs/StartViewerHost.svelte";
  import { buildLeafViewerContract } from "$lib/components/tabs/viewers/viewerContracts.js";

  export let record = null;
  export let label = "";
  export let sourceVariable = "";
  export let level = 0;

  export let collectionRecord = () => false;
  export let recordDescriptor = (value) => value?.descriptor || { vox_type: "unavailable", summary: {} };
  export let recordType = (value) => String(recordDescriptor(value)?.vox_type || "unavailable").toLowerCase();
  export let recordPath = (value) => String(value?.path || "");
  export let previewText = () => "value";
  export let typeLabelFromDescriptor = () => "value";

  export let pageForRecord = () => null;
  export let pageErrorForRecord = () => "";
  export let pageLoadingForRecord = () => false;
  export let pagePollingForRecord = () => false;
  export let loadRecordPage = async () => null;
  export let collectionItemsForPage = (page) => (Array.isArray(page?.items) ? page.items : []);
  export let collectionSelectionFor = () => ({ selectedIndex: 0, selectedAbsoluteIndex: 0, selectedPath: "" });
  export let setCollectionSelection = () => {};
  export let loadCollectionPrev = async () => null;
  export let loadCollectionNext = async () => null;
  export let nestedRecordFromItem = (_record, item) => item;
  export let pathRecordFor = () => null;
  export let pathRecordLoadingFor = () => false;
  export let pathRecordErrorFor = () => "";
  export let pathRecordPollingFor = () => false;
  export let loadPathRecord = async () => null;

  export let recordPages = {};
  export let recordPagePointers = {};
  export let recordPagesLoading = {};
  export let recordPagesErrors = {};
  export let collectionSelections = {};
  export let expandedCollectionStages = {};
  export let pathRecords = {};
  export let pathRecordsLoading = {};
  export let pathRecordsErrors = {};
  export let setCollectionStageExpanded = () => {};

  const MAX_DEPTH = 7;
  const DEFAULT_LIMIT = 18;
  const ACTIVE_COLLECTION_ITEM_STATES = new Set(["not_loaded", "queued", "blocked", "running", "persisting"]);
  const COLLECTION_PENDING_STATES = new Set(["not_loaded", "queued", "blocked", "running", "persisting", "pending", "missing"]);
  let stageExpanded = false;

  const stageExpansionKeyFor = (recordValue, recordPathValue, recordSourceVariable) => {
    const normalizedSource = String(recordSourceVariable || "").trim();
    const normalizedPath = String(recordPathValue || "").trim();
    const nodeId = String(recordValue?.node_id || "").trim();
    if (normalizedSource && normalizedPath) return `${normalizedSource}:${normalizedPath}`;
    if (normalizedSource && nodeId) return `${normalizedSource}:node:${nodeId}`;
    if (nodeId && normalizedPath) return `node:${nodeId}:${normalizedPath}`;
    if (nodeId) return `node:${nodeId}`;
    return "";
  };

  const safeText = (value) => {
    if (value === null || value === undefined) return "null";
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  };

  const collectionDescriptor = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "").toLowerCase();
    return voxType === "sequence" || voxType === "mapping";
  };

  const concreteDescriptor = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "").trim().toLowerCase();
    return Boolean(voxType) && voxType !== "unavailable" && voxType !== "error";
  };

  const descriptorPriority = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "").trim().toLowerCase();
    if (!voxType || voxType === "unavailable" || voxType === "error") return 0;
    let score = 10;
    if (collectionDescriptor(descriptor)) score += 10;
    const summary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
    if (summary.length !== null && summary.length !== undefined && summary.length !== "") score += 6;
    if (Array.isArray(summary.size) && summary.size.length) score += 6;
    if (summary.value !== undefined) score += 4;
    const render = descriptor?.render && typeof descriptor.render === "object" ? descriptor.render : {};
    if (String(render?.kind || "").trim()) score += 8;
    return score;
  };

  const recordPriority = (value) => {
    if (!value || typeof value !== "object") return -1;
    const descriptor = value?.descriptor && typeof value.descriptor === "object" ? value.descriptor : null;
    const state = normalizeCollectionItemState(value?.state || value?.status, descriptor);
    if (state === "failed" || String(value?.error || "").trim()) return 100;
    const descriptorScore = descriptorPriority(descriptor);
    if (descriptorScore > 0) return 50 + descriptorScore;
    if (state === "persisting") return 40;
    if (state === "running") return 35;
    if (state === "blocked") return 30;
    if (state === "queued") return 25;
    return 10;
  };

  const preferRecord = (...records) => {
    let best = null;
    let bestScore = -1;
    for (const record of records) {
      const score = recordPriority(record);
      if (score > bestScore) {
        best = record;
        bestScore = score;
      }
    }
    return best;
  };

  const descriptorNeedsExplicitDetail = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "").trim().toLowerCase();
    if (!voxType || voxType === "unavailable" || voxType === "error") return true;
    if (collectionDescriptor(descriptor)) return false;
    if (["integer", "number", "boolean", "null", "string", "bytes"].includes(voxType)) return false;
    const render = descriptor?.render && typeof descriptor.render === "object" ? descriptor.render : {};
    if (String(render?.kind || "").trim()) return false;
    return true;
  };

  const normalizeCollectionItemState = (rawState, descriptor = null) => {
    const normalized = String(rawState || "").trim().toLowerCase();
    if (["ready", "queued", "blocked", "running", "persisting", "failed", "not_loaded"].includes(normalized)) {
      return normalized;
    }
    if (["materialized", "computed", "completed", "cached"].includes(normalized)) return "ready";
    if (["pending", "missing"].includes(normalized)) return "not_loaded";
    if (["error", "killed"].includes(normalized)) return "failed";
    const voxType = String(descriptor?.vox_type || "").toLowerCase();
    if (!voxType || voxType === "unavailable") return "not_loaded";
    return "ready";
  };

  const itemStateClass = (item) => effectiveStateForItem(item);

  const itemStateLabel = (item) => {
    const state = effectiveStateForItem(item);
    return state.replaceAll("_", " ");
  };

  const itemStateDetails = (item) => {
    const details = [];
    const error = String(item?.error || "").trim();
    const blockedOn = String(item?.blocked_on || "").trim();
    const stateReason = String(item?.state_reason || "").trim();
    if (blockedOn) details.push(`blocked on ${blockedOn}`);
    if (stateReason) details.push(stateReason);
    if (error) details.push(error);
    return details.join(" · ");
  };

  const resolvedItemRecord = (item) => {
    if (!item || !sourceVariable) return null;
    const itemPath = String(item?.path || "");
    if (!itemPath) return null;
    const resolved = pathRecordFor(sourceVariable, itemPath);
    return resolved && typeof resolved === "object" ? resolved : null;
  };

  const itemTargetPath = (item) => String(item?.path || "");

  const itemHasGraphTarget = (item) => {
    const targetPath = itemTargetPath(item);
    if (targetPath) return true;
    const resolved = resolvedItemRecord(item);
    if (String(resolved?.node_id || "").trim()) return true;
    return false;
  };

  const displayRecordForItem = (item) => {
    const itemPath = itemTargetPath(item);
    if (!itemPath) return null;
    const cached = itemPath === selectedPath ? selectedRecordDetail : resolvedItemRecord(item);
    return preferRecord(cached, item);
  };

  const effectiveDescriptorForItem = (item) => {
    const resolved = displayRecordForItem(item);
    if (resolved?.descriptor && typeof resolved.descriptor === "object") {
      return resolved.descriptor;
    }
    return item?.descriptor && typeof item.descriptor === "object" ? item.descriptor : { vox_type: "value", summary: {} };
  };

  const effectiveStateForItem = (item) => {
    if (!itemHasGraphTarget(item)) return "not_loaded";
    const resolved = displayRecordForItem(item);
    const targetPath = itemTargetPath(item);
    const itemDescriptor = effectiveDescriptorForItem(item);
    if (collectionDescriptor(itemDescriptor)) {
      const nestedRecord = nestedRecordFromItem(record, item);
      const nestedPath = recordPath(nestedRecord);
      const nestedPageReady = Boolean(pageForRecord(nestedRecord, nestedPath));
      const nestedPageBusy = pageLoadingForRecord(nestedRecord, nestedPath) || pagePollingForRecord(nestedRecord, nestedPath);
      const targetBusy =
        Boolean(sourceVariable) &&
        Boolean(targetPath) &&
        (pathRecordLoadingFor(sourceVariable, targetPath) || pathRecordPollingFor(sourceVariable, targetPath));
      if (String(selectedPath || "") === targetPath && (nestedPageBusy || targetBusy)) return "loading";
      if (nestedPageReady) return "ready";
    }
    if (resolved) {
      const materialization = String(resolved?.materialization || "").toLowerCase();
      const computeStatus = String(resolved?.compute_status || "").toLowerCase();
      if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") return "failed";
      if (concreteDescriptor(resolved?.descriptor)) return "ready";
      if ((materialization === "computed" || materialization === "cached") && resolved?.descriptor) return "ready";
      if (String(selectedPath || "") === targetPath && ["queued", "running", "persisting"].includes(computeStatus)) return "loading";
      if (["queued", "running", "persisting"].includes(computeStatus)) return computeStatus;
      if (["pending", "missing"].includes(materialization)) {
        return normalizeCollectionItemState(item?.state || item?.status, effectiveDescriptorForItem(item));
      }
    }
    return normalizeCollectionItemState(item?.state || item?.status, effectiveDescriptorForItem(item));
  };

  const pendingCollectionStateFor = (value) => {
    if (!value || typeof value !== "object") return "";
    const computeStatus = String(value?.compute_status || value?.status || "").trim().toLowerCase();
    if (COLLECTION_PENDING_STATES.has(computeStatus)) return computeStatus;
    const materialization = String(value?.materialization || "").trim().toLowerCase();
    if (COLLECTION_PENDING_STATES.has(materialization)) return materialization;
    return "";
  };

  const emptyCollectionMessage = (value, currentPage) => {
    const descriptor = recordDescriptor(value);
    const nextSummary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
    const length = Number(nextSummary?.length);
    if (Number.isFinite(length) && length <= 0) {
      return "This range has no values.";
    }
    if (currentPage && Number(currentPage?.offset || 0) > 0) {
      return "No values on this page.";
    }
    return "This collection has no values yet.";
  };

  const pageSummaryLabel = (currentPage, currentItems) => {
    if (!currentPage) return "Loading";
    const count = Array.isArray(currentItems) ? currentItems.length : 0;
    if (!count) return "Empty";
    const offset = Math.max(0, Number(currentPage?.offset || 0));
    return `${offset + 1}-${offset + count}`;
  };

  $: descriptor = recordDescriptor(record);
  $: voxType = recordType(record);
  $: summary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
  $: path = recordPath(record);
  $: isCollection = collectionRecord(record);
  $: render = descriptor?.render && typeof descriptor.render === "object" ? descriptor.render : {};
  $: renderKind = String(render?.kind || "").toLowerCase();
  $: renderPngUrl = String(render?.png_url || "");
  $: renderNiftiUrl = String(render?.nifti_url || "");
  $: renderLayers = Array.isArray(render?.layers) ? render.layers.filter((layer) => layer && typeof layer === "object") : [];
  $: leafViewerContract = buildLeafViewerContract({
    descriptor,
    summary,
    render,
    label: label || "value",
    fallbackText: previewText(descriptor),
  });

  $: page = (recordPages, recordPagePointers, isCollection ? pageForRecord(record, path) : null);
  $: loading = (recordPagesLoading, isCollection ? pageLoadingForRecord(record, path) : false);
  $: error = (recordPagesErrors, isCollection ? pageErrorForRecord(record, path) : "");
  $: items = isCollection ? collectionItemsForPage(page, voxType) : [];
  $: visibleItems = items.filter((item) => itemHasGraphTarget(item));
  $: selection = (
    collectionSelections,
    isCollection ? collectionSelectionFor(record, path) : { selectedIndex: 0, selectedAbsoluteIndex: 0, selectedPath: "" }
  );

  $: selectedIndex = (() => {
    if (!visibleItems.length) return 0;
    const selectedPath = String(selection?.selectedPath || "");
    const byPath = selectedPath ? visibleItems.findIndex((item) => String(item?.path || "") === selectedPath) : -1;
    if (byPath >= 0) return byPath;
    const firstReady = visibleItems.findIndex((item) => itemStateClass(item) === "ready");
    if (firstReady >= 0) return firstReady;
    const byAbsolute = Math.max(0, Number(selection?.selectedAbsoluteIndex || 0)) - Math.max(0, Number(page?.offset || 0));
    if (byAbsolute >= 0 && byAbsolute < visibleItems.length) return byAbsolute;
    const byIndex = Math.max(0, Number(selection?.selectedIndex || 0));
    return byIndex < visibleItems.length ? byIndex : 0;
  })();
  $: selectedItem = visibleItems.length ? visibleItems[selectedIndex] : null;
  $: selectedPath = String(selectedItem?.path || "");
  $: selectedRecordDetail = (pathRecords, selectedPath ? pathRecordFor(sourceVariable, selectedPath) : null);
  $: selectedDetailLoading = (pathRecordsLoading, selectedPath ? pathRecordLoadingFor(sourceVariable, selectedPath) : false);
  $: selectedDetailError = (pathRecordsErrors, selectedPath ? pathRecordErrorFor(sourceVariable, selectedPath) : "");
  $: selectedItemRecord = selectedItem ? nestedRecordFromItem(record, selectedItem) : null;
  $: selectedRecord = preferRecord(selectedRecordDetail, selectedItemRecord);
  $: selectedDescriptor =
    selectedRecord?.descriptor && typeof selectedRecord.descriptor === "object"
      ? selectedRecord.descriptor
      : selectedItem?.descriptor && typeof selectedItem.descriptor === "object"
        ? selectedItem.descriptor
        : { vox_type: "unavailable", summary: {} };

  $: selectedItemState = selectedItem ? itemStateClass(selectedItem) : "";
  $: stageExpansionKey = isCollection ? stageExpansionKeyFor(record, path, sourceVariable) : "";
  $: stageExpanded = Boolean(stageExpansionKey && expandedCollectionStages?.[stageExpansionKey]);

  $: if (isCollection && visibleItems.length) {
    const nextPath = String(visibleItems[selectedIndex]?.path || "");
    if (String(selection?.selectedPath || "") !== nextPath || Number(selection?.selectedIndex || 0) !== selectedIndex) {
      setCollectionSelection(record, path, {
        selectedIndex,
        selectedAbsoluteIndex: Math.max(0, Number(page?.offset || 0)) + selectedIndex,
        selectedPath: nextPath,
      });
    }
  }

  $: if (!isCollection) {
    stageExpanded = false;
  }

  $: if (record && isCollection && !page && !loading && !error && !pagePollingForRecord(record, path)) {
    void loadRecordPage(record, {
      path,
      offset: 0,
      limit: DEFAULT_LIMIT,
      sourceVariable,
    });
  }

  $: if (selectedRecord && level < MAX_DEPTH && collectionRecord(selectedRecord)) {
    const nestedPath = recordPath(selectedRecord);
    const nestedPage = pageForRecord(selectedRecord, nestedPath);
    const waitingForNestedDetail =
      Boolean(selectedPath) && !selectedRecordDetail && !selectedDetailError;
    if (
      !nestedPage &&
      !waitingForNestedDetail &&
      !pageLoadingForRecord(selectedRecord, nestedPath) &&
      !pagePollingForRecord(selectedRecord, nestedPath)
    ) {
      void loadRecordPage(selectedRecord, {
        path: nestedPath,
        offset: 0,
        limit: DEFAULT_LIMIT,
        sourceVariable,
      });
    }
  }

  $: if (
    isCollection &&
    selectedItem &&
    sourceVariable &&
    selectedPath &&
    !selectedRecordDetail &&
    !selectedDetailLoading &&
    !selectedDetailError &&
    descriptorNeedsExplicitDetail(selectedItem?.descriptor) &&
    !pathRecordPollingFor(sourceVariable, selectedPath)
  ) {
    void loadPathRecord({
      sourceVariable,
      path: selectedPath,
      enqueueFallback: true,
    });
  }

</script>

<div class="start-value-canvas">
  {#if !record}
    <div class="start-viewer-message">No value</div>
  {:else if !isCollection}
    <StartViewerHost contract={leafViewerContract} />
  {:else if isCollection}
    {#if level > 0 && !items.length && !loading && !error}
      {#if pagePollingForRecord(record, path) || pendingCollectionStateFor(record)}
        <div class="start-collection-stage-loading" aria-label="Waiting for values">
          <span></span>
        </div>
      {:else}
        <div class="start-viewer-message">{emptyCollectionMessage(record, page)}</div>
      {/if}
    {:else}
      <div class={`start-collection-shell ${level > 0 ? "is-nested" : ""} ${stageExpanded ? "is-stage-maximized" : ""}`.trim()}>
      <aside class="start-collection-index">
        <div class="start-collection-nav">
          <button
            class="btn btn-ghost btn-small"
            type="button"
            disabled={!page || Number(page?.offset || 0) <= 0 || loading}
            on:click={() => void loadCollectionPrev(record, path, sourceVariable)}
          >
            Prev
          </button>
          <div class="start-collection-nav-meta">
            {#if page}
              <span>{pageSummaryLabel(page, items)}</span>
            {:else}
              <span>Loading</span>
            {/if}
          </div>
          <button
            class="btn btn-ghost btn-small"
            type="button"
            disabled={!page || !page?.has_more || loading}
            on:click={() => void loadCollectionNext(record, path, sourceVariable)}
          >
            Next
          </button>
        </div>
        {#if loading && !items.length}
          <div class="start-collection-loading">
            {#each Array(6) as _, idx}
              <span style={`--row-index:${idx}`}></span>
            {/each}
          </div>
        {:else if pagePollingForRecord(record, path) && !items.length}
          <div class="start-collection-loading" aria-label="Waiting for values">
            {#each Array(6) as _, idx}
              <span style={`--row-index:${idx}`}></span>
            {/each}
          </div>
        {:else if error}
          <div class="viewer-error">{error}</div>
        {:else if visibleItems.length}
          <div class="start-collection-list">
            {#each visibleItems as item, itemIndex}
              {@const itemDescriptor = effectiveDescriptorForItem(item)}
              {@const itemState = itemStateClass(item)}
              {@const itemLabel = itemStateLabel(item)}
              {@const itemDetails = itemStateDetails(item)}
              <button
                class={`start-collection-item start-collection-item--${itemState} ${selectedIndex === itemIndex ? "is-selected" : ""}`.trim()}
                type="button"
                title={`${String(item?.label || `[${Number(page?.offset || 0) + itemIndex}]`)} (${typeLabelFromDescriptor(itemDescriptor)}) · ${itemLabel}${itemDetails ? ` · ${itemDetails}` : ""}`}
                on:click={() => {
                  const nextPath = String(item?.path || "");
                  setCollectionSelection(record, path, {
                    selectedIndex: itemIndex,
                    selectedAbsoluteIndex: Math.max(0, Number(page?.offset || 0)) + itemIndex,
                    selectedPath: nextPath,
                  });
                  if (
                    sourceVariable &&
                    nextPath &&
                    ACTIVE_COLLECTION_ITEM_STATES.has(itemState) &&
                    descriptorNeedsExplicitDetail(itemDescriptor)
                  ) {
                    void loadPathRecord({
                      sourceVariable,
                      path: nextPath,
                      enqueueFallback: true,
                      force: true,
                    });
                  }
                }}
              >
                <span class="start-collection-item-index">
                  {item?.label || `[${Number(page?.offset || 0) + itemIndex}]`}
                </span>
                <span class={`start-collection-item-state start-collection-item-state--${itemState}`.trim()}>
                  {itemLabel}
                </span>
                <span class="start-collection-item-preview">{previewText(itemDescriptor)}</span>
              </button>
            {/each}
          </div>
        {:else}
          <div class="start-viewer-message">{emptyCollectionMessage(record, page)}</div>
        {/if}
      </aside>

      <section class="start-collection-stage">
        {#if selectedItem}
          <header class="start-collection-stage-head">
            <div class="start-collection-stage-meta">
              <span class="start-collection-stage-label">{selectedItem?.label || label || "value"}</span>
              <span class={`start-collection-stage-status start-collection-stage-status--${selectedItemState}`.trim()}>{selectedItemState.replaceAll("_", " ") || "pending"}</span>
            </div>
            <button
              class="btn btn-ghost btn-small start-collection-stage-expand"
              type="button"
              on:click={() => {
                if (!stageExpansionKey) return;
                setCollectionStageExpanded(stageExpansionKey, !stageExpanded);
              }}
            >
              {stageExpanded ? "Restore" : "Maximize"}
            </button>
          </header>
          {#key `${selectedPath}|${selectedDetailLoading ? "loading" : "idle"}|${selectedDetailError}`.trim()}
            <div class="start-collection-stage-body">
              {#if selectedDetailError}
                <div class="viewer-error">{selectedDetailError}</div>
              {:else if selectedDetailLoading && !selectedRecordDetail}
                <div class="start-collection-stage-loading">
                  <span></span>
                </div>
              {:else if level >= MAX_DEPTH}
                <div class="start-pure-array">{previewText(selectedDescriptor)}</div>
              {:else}
                <svelte:self
                  record={selectedRecord}
                  label={selectedItem?.label || label}
                  {sourceVariable}
                  level={level + 1}
                  {collectionRecord}
                  {recordDescriptor}
                  {recordType}
                  {recordPath}
                  {previewText}
                  {typeLabelFromDescriptor}
                  {pageForRecord}
                  {pageErrorForRecord}
                  {pageLoadingForRecord}
                  {pagePollingForRecord}
                  {loadRecordPage}
                  {collectionItemsForPage}
                  {collectionSelectionFor}
                  {setCollectionSelection}
                  {loadCollectionPrev}
                  {loadCollectionNext}
                  {nestedRecordFromItem}
                  {pathRecordFor}
                  {pathRecordLoadingFor}
                  {pathRecordErrorFor}
                  {pathRecordPollingFor}
                  {loadPathRecord}
                  {recordPages}
                  {recordPagePointers}
                  {recordPagesLoading}
                  {recordPagesErrors}
                  {collectionSelections}
                  {expandedCollectionStages}
                  {pathRecords}
                  {pathRecordsLoading}
                  {pathRecordsErrors}
                  {setCollectionStageExpanded}
                />
              {/if}
            </div>
          {/key}
        {:else}
          <div class="start-viewer-message">{emptyCollectionMessage(record, page)}</div>
        {/if}
      </section>
      </div>
    {/if}
  {/if}
</div>

<style>
  .start-collection-stage-meta {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    min-width: 0;
  }

  .start-collection-stage-status {
    flex: 0 0 auto;
    padding: 0.16rem 0.46rem;
    border-radius: 999px;
    border: 1px solid rgba(171, 187, 213, 0.72);
    background: rgba(248, 250, 253, 0.92);
    color: #6b7d99;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .start-collection-stage-status--ready {
    color: #1d7b55;
    border-color: rgba(91, 201, 146, 0.58);
    background: rgba(228, 248, 239, 0.92);
  }

  .start-collection-stage-status--loading,
  .start-collection-stage-status--running,
  .start-collection-stage-status--queued,
  .start-collection-stage-status--persisting,
  .start-collection-stage-status--not_loaded,
  .start-collection-stage-status--blocked {
    color: #b47a2e;
    border-color: rgba(236, 177, 92, 0.62);
    background: rgba(251, 240, 222, 0.92);
  }

  .start-collection-stage-status--failed {
    color: #8b3040;
    border-color: rgba(205, 94, 111, 0.36);
    background: rgba(255, 241, 244, 0.96);
  }

  .start-overlay-image-shell {
    position: relative;
    width: min(100%, 900px);
    aspect-ratio: 1 / 1;
    min-height: 280px;
    border-radius: 18px;
    overflow: hidden;
    border: 1px solid rgba(83, 97, 120, 0.16);
    background:
      radial-gradient(circle at 14% 18%, rgba(63, 116, 255, 0.1), transparent 38%),
      linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(238, 244, 251, 0.92));
    box-shadow:
      inset 0 1px rgba(255, 255, 255, 0.8),
      0 18px 42px rgba(16, 28, 45, 0.12);
  }

  .start-overlay-image-layer {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
    opacity: var(--layer-opacity, 1);
  }

  .start-overlay-image-layer.is-base {
    mix-blend-mode: normal;
    filter: saturate(1.02) contrast(1.02);
  }

  .start-overlay-image-layer.is-overlay {
    mix-blend-mode: screen;
    filter: saturate(1.14) contrast(1.05);
  }
</style>
