<script>
  import StartMedicalVolume from "$lib/components/tabs/StartMedicalVolume.svelte";

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
  export let pathRecords = {};
  export let pathRecordsLoading = {};
  export let pathRecordsErrors = {};

  const MAX_DEPTH = 7;
  const DEFAULT_LIMIT = 18;
  let stageExpanded = false;

  const safeText = (value) => {
    if (value === null || value === undefined) return "null";
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  };

  const collectionDescriptor = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "").toLowerCase();
    return voxType === "sequence" || voxType === "mapping";
  };

  const collectionItemState = (item) => {
    const descriptor = item?.descriptor && typeof item.descriptor === "object" ? item.descriptor : {};
    const voxType = String(descriptor?.vox_type || "").toLowerCase();
    const status = String(item?.status || "").toLowerCase();
    if (["failed", "killed", "error"].includes(status) || voxType === "error") return "failed";
    if (["pending", "missing", "queued", "running", "persisting"].includes(status)) return "pending";
    if (!voxType || voxType === "unavailable") return "pending";
    return "materialized";
  };

  const itemStateLabel = (item) => {
    const state = effectiveStateForItem(item);
    const descriptor = effectiveDescriptorForItem(item);
    if (state === "materialized") return "ready";
    if (state === "failed") return "failed";
    if (state === "upstream") return "upstream";
    const rawStatus = String(item?.status || "").toLowerCase();
    if (rawStatus === "queued") return "queued";
    if (rawStatus === "persisting") return "persisting";
    if (rawStatus === "running") return "running";
    if (collectionDescriptor(descriptor)) return "upstream";
    if (rawStatus === "pending" || rawStatus === "missing") return "waiting";
    return "waiting";
  };

  const resolvedItemRecord = (item) => {
    if (!item || !sourceVariable) return null;
    const itemPath = String(item?.path || "");
    if (!itemPath) return null;
    const resolved = pathRecordFor(sourceVariable, itemPath);
    return resolved && typeof resolved === "object" ? resolved : null;
  };

  const effectiveDescriptorForItem = (item) => {
    const resolved = resolvedItemRecord(item);
    if (resolved?.descriptor && typeof resolved.descriptor === "object") {
      return resolved.descriptor;
    }
    return item?.descriptor && typeof item.descriptor === "object" ? item.descriptor : { vox_type: "value", summary: {} };
  };

  const effectiveStateForItem = (item) => {
    const resolved = resolvedItemRecord(item);
    if (resolved) {
      const materialization = String(resolved?.materialization || "").toLowerCase();
      const computeStatus = String(resolved?.compute_status || "").toLowerCase();
      if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") return "failed";
      if ((materialization === "computed" || materialization === "cached") && resolved?.descriptor) return "materialized";
      if (["pending", "missing"].includes(materialization) || ["queued", "running", "persisting"].includes(computeStatus)) {
        return collectionDescriptor(effectiveDescriptorForItem(item)) ? "upstream" : "pending";
      }
    }
    return collectionItemState(item);
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
  $: renderableImage = Boolean(renderPngUrl) && renderKind === "image2d";
  $: renderableVolume = Boolean(renderNiftiUrl) && renderKind === "medical-volume";
  $: renderableImageOverlay = renderKind === "image-overlay" && renderLayers.some((layer) => layer?.png_url);
  $: renderableVolumeOverlay = renderKind === "medical-overlay" && renderLayers.some((layer) => layer?.nifti_url);

  $: page = (recordPages, recordPagePointers, isCollection ? pageForRecord(record, path) : null);
  $: loading = (recordPagesLoading, isCollection ? pageLoadingForRecord(record, path) : false);
  $: error = (recordPagesErrors, isCollection ? pageErrorForRecord(record, path) : "");
  $: items = isCollection ? collectionItemsForPage(page, voxType) : [];
  $: selection = (
    collectionSelections,
    isCollection ? collectionSelectionFor(record, path) : { selectedIndex: 0, selectedAbsoluteIndex: 0, selectedPath: "" }
  );

  $: selectedIndex = (() => {
    if (!items.length) return 0;
    const byAbsolute = Math.max(0, Number(selection?.selectedAbsoluteIndex || 0)) - Math.max(0, Number(page?.offset || 0));
    if (byAbsolute >= 0 && byAbsolute < items.length) return byAbsolute;
    const selectedPath = String(selection?.selectedPath || "");
    const byPath = selectedPath ? items.findIndex((item) => String(item?.path || "") === selectedPath) : -1;
    if (byPath >= 0) return byPath;
    const byIndex = Math.max(0, Number(selection?.selectedIndex || 0));
    return byIndex < items.length ? byIndex : 0;
  })();
  $: selectedItem = items.length ? items[selectedIndex] : null;
  $: selectedPath = String(selectedItem?.path || "");
  $: selectedRecordDetail = (pathRecords, selectedPath ? pathRecordFor(sourceVariable, selectedPath) : null);
  $: selectedDetailLoading = (pathRecordsLoading, selectedPath ? pathRecordLoadingFor(sourceVariable, selectedPath) : false);
  $: selectedDetailError = (pathRecordsErrors, selectedPath ? pathRecordErrorFor(sourceVariable, selectedPath) : "");
  $: selectedRecord = selectedRecordDetail || (selectedItem ? nestedRecordFromItem(record, selectedItem) : null);
  $: selectedDescriptor =
    selectedRecord?.descriptor && typeof selectedRecord.descriptor === "object"
      ? selectedRecord.descriptor
      : selectedItem?.descriptor && typeof selectedItem.descriptor === "object"
        ? selectedItem.descriptor
        : { vox_type: "unavailable", summary: {} };

  $: if (isCollection && items.length) {
    const nextPath = String(items[selectedIndex]?.path || "");
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
    !pathRecordPollingFor(sourceVariable, selectedPath)
  ) {
    void loadPathRecord({
      sourceVariable,
      path: selectedPath,
      enqueueFallback: true,
    });
  }

  $: if (isCollection && sourceVariable && items.length) {
    const budget = Math.min(items.length, 10);
    const anchor = Math.max(0, Math.min(items.length - 1, Number(selectedIndex || 0)));
    const prefetchOrder = [anchor];
    for (let step = 1; prefetchOrder.length < budget && (anchor - step >= 0 || anchor + step < items.length); step += 1) {
      if (anchor + step < items.length) prefetchOrder.push(anchor + step);
      if (anchor - step >= 0 && prefetchOrder.length < budget) prefetchOrder.push(anchor - step);
    }
    for (const idx of prefetchOrder) {
      const item = items[idx];
      const itemPath = String(item?.path || "");
      if (!itemPath) continue;
      if (resolvedItemRecord(item)) continue;
      if (pathRecordLoadingFor(sourceVariable, itemPath)) continue;
      if (pathRecordPollingFor(sourceVariable, itemPath)) continue;
      const shouldEnqueue = idx === anchor || idx < Math.min(4, items.length);
      void loadPathRecord({
        sourceVariable,
        path: itemPath,
        enqueueFallback: shouldEnqueue,
      });
    }
  }
</script>

<div class="start-value-canvas">
  {#if !record}
    <div class="start-viewer-message">No value</div>
  {:else if ["integer", "number", "boolean", "null"].includes(voxType)}
    <div class="start-value-centered">
      <div class="start-pure-scalar">{safeText(summary.value)}</div>
    </div>
  {:else if voxType === "string"}
    <div class="start-value-centered start-value-centered--text">
      <pre class="start-pure-text">{safeText(summary.value)}</pre>
    </div>
  {:else if voxType === "bytes"}
    <div class="start-value-centered">
      <div class="start-pure-scalar">{Number(summary.length || 0)} bytes</div>
    </div>
  {:else if renderableImage}
    <div class="start-value-centered">
      <img class="start-pure-image" src={renderPngUrl} alt={`${label || "value"} preview`} />
    </div>
  {:else if renderableVolume}
    <div class="start-value-media-shell">
      <StartMedicalVolume niftiUrl={renderNiftiUrl} label={label || "value"} />
    </div>
  {:else if renderableImageOverlay}
    <div class="start-value-centered">
      <div class="start-overlay-image-shell" aria-label={`${label || "value"} overlay`}>
        {#each renderLayers as layer, layerIndex}
          {#if layer?.png_url && layer?.visible !== false}
            <img
              class={`start-overlay-image-layer ${layerIndex === 0 ? "is-base" : "is-overlay"}`.trim()}
              src={layer.png_url}
              alt={layer?.label || `Layer ${layerIndex + 1}`}
              style={`--layer-opacity:${Number.isFinite(Number(layer?.opacity)) ? Number(layer.opacity) : layerIndex === 0 ? 1 : 0.4}`}
            />
          {/if}
        {/each}
      </div>
    </div>
  {:else if renderableVolumeOverlay}
    <div class="start-value-media-shell">
      <StartMedicalVolume layers={renderLayers} label={label || "value"} />
    </div>
  {:else if isCollection}
    {#if level > 0 && !items.length && !loading && !error}
      <div class="start-viewer-message">No values yet</div>
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
              <span>
                {Number(page?.offset || 0) + (items.length ? 1 : 0)}-{Number(page?.offset || 0) + items.length}
              </span>
            {:else}
              <span>0-0</span>
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
        {:else if error}
          <div class="viewer-error">{error}</div>
        {:else if items.length}
          <div class="start-collection-list">
            {#each items as item, itemIndex}
              {@const itemDescriptor = effectiveDescriptorForItem(item)}
              <button
                class={`start-collection-item start-collection-item--${effectiveStateForItem(item)} ${selectedIndex === itemIndex ? "is-selected" : ""}`.trim()}
                type="button"
                title={`${String(item?.label || `[${Number(page?.offset || 0) + itemIndex}]`)} (${typeLabelFromDescriptor(itemDescriptor)}) · ${itemStateLabel(item)}`}
                on:click={() =>
                  setCollectionSelection(record, path, {
                    selectedIndex: itemIndex,
                    selectedAbsoluteIndex: Math.max(0, Number(page?.offset || 0)) + itemIndex,
                    selectedPath: String(item?.path || ""),
                  })}
              >
                <span class="start-collection-item-index">
                  {item?.label || `[${Number(page?.offset || 0) + itemIndex}]`}
                </span>
                <span class={`start-collection-item-state start-collection-item-state--${itemStateLabel(item)}`.trim()}>
                  {itemStateLabel(item)}
                </span>
                <span class="start-collection-item-preview">{previewText(itemDescriptor)}</span>
              </button>
            {/each}
          </div>
        {:else}
          <div class="start-viewer-message">No values yet</div>
        {/if}
      </aside>

      <section class="start-collection-stage">
        {#if selectedItem}
          <header class="start-collection-stage-head">
            <span class="start-collection-stage-label">{selectedItem?.label || label || "value"}</span>
            <button class="btn btn-ghost btn-small start-collection-stage-expand" type="button" on:click={() => (stageExpanded = !stageExpanded)}>
              {stageExpanded ? "Restore" : "Maximize"}
            </button>
          </header>
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
                {pathRecords}
                {pathRecordsLoading}
                {pathRecordsErrors}
              />
            {/if}
          </div>
        {:else}
          <div class="start-viewer-message">No selected value</div>
        {/if}
      </section>
      </div>
    {/if}
  {:else}
    <div class="start-value-centered">
      <div class="start-pure-array">{previewText(descriptor)}</div>
    </div>
  {/if}
</div>

<style>
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
