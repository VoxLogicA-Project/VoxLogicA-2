<script>
  export let record = null;
  export let label = "";
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
  export let loadRecordPage = async () => null;
  export let collectionItemsForPage = (page) => (Array.isArray(page?.items) ? page.items : []);
  export let collectionSelectionFor = () => ({ selectedIndex: 0, selectedPath: "" });
  export let setCollectionSelection = () => {};
  export let loadCollectionPrev = async () => null;
  export let loadCollectionNext = async () => null;
  export let nestedRecordFromItem = (_record, item) => item;

  const MAX_DEPTH = 7;
  const DEFAULT_LIMIT = 18;

  const safeText = (value) => {
    if (value === null || value === undefined) return "null";
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  };

  $: descriptor = recordDescriptor(record);
  $: voxType = recordType(record);
  $: summary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
  $: path = recordPath(record);
  $: isCollection = collectionRecord(record);

  $: page = isCollection ? pageForRecord(record, path) : null;
  $: loading = isCollection ? pageLoadingForRecord(record, path) : false;
  $: error = isCollection ? pageErrorForRecord(record, path) : "";
  $: items = isCollection ? collectionItemsForPage(page, voxType) : [];
  $: selection = isCollection ? collectionSelectionFor(record, path) : { selectedIndex: 0, selectedPath: "" };

  $: selectedIndex = (() => {
    if (!items.length) return 0;
    const selectedPath = String(selection?.selectedPath || "");
    const byPath = selectedPath ? items.findIndex((item) => String(item?.path || "") === selectedPath) : -1;
    if (byPath >= 0) return byPath;
    const byIndex = Math.max(0, Number(selection?.selectedIndex || 0));
    return byIndex < items.length ? byIndex : 0;
  })();
  $: selectedItem = items.length ? items[selectedIndex] : null;
  $: selectedRecord = selectedItem ? nestedRecordFromItem(record, selectedItem) : null;
  $: selectedDescriptor =
    selectedItem?.descriptor && typeof selectedItem.descriptor === "object" ? selectedItem.descriptor : { vox_type: "unavailable", summary: {} };

  $: if (isCollection && items.length) {
    const nextPath = String(items[selectedIndex]?.path || "");
    if (String(selection?.selectedPath || "") !== nextPath || Number(selection?.selectedIndex || 0) !== selectedIndex) {
      setCollectionSelection(record, path, {
        selectedIndex,
        selectedPath: nextPath,
      });
    }
  }

  $: if (record && isCollection && !page && !loading && !error) {
    void loadRecordPage(record, {
      path,
      offset: 0,
      limit: DEFAULT_LIMIT,
    });
  }

  $: if (selectedRecord && level < MAX_DEPTH && collectionRecord(selectedRecord)) {
    const nestedPath = recordPath(selectedRecord);
    if (!pageForRecord(selectedRecord, nestedPath) && !pageLoadingForRecord(selectedRecord, nestedPath)) {
      void loadRecordPage(selectedRecord, {
        path: nestedPath,
        offset: 0,
        limit: DEFAULT_LIMIT,
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
  {:else if (voxType === "image2d" || voxType === "volume3d") && descriptor?.render?.png_url}
    <div class="start-value-centered">
      <img class="start-pure-image" src={descriptor.render.png_url} alt={`${label || "value"} preview`} />
    </div>
  {:else if isCollection}
    <div class={`start-collection-shell ${level > 0 ? "is-nested" : ""}`}>
    <aside class="start-collection-index">
      <div class="start-collection-nav">
        <button
          class="btn btn-ghost btn-small"
          type="button"
          disabled={!page || Number(page?.offset || 0) <= 0 || loading}
          on:click={() => void loadCollectionPrev(record, path)}
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
          on:click={() => void loadCollectionNext(record, path)}
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
            {@const itemDescriptor = item?.descriptor && typeof item.descriptor === "object" ? item.descriptor : { vox_type: "value", summary: {} }}
            <button
              class={`start-collection-item ${selectedIndex === itemIndex ? "is-selected" : ""}`.trim()}
              type="button"
              title={`${String(item?.label || `[${Number(page?.offset || 0) + itemIndex}]`)} (${typeLabelFromDescriptor(itemDescriptor)})`}
              on:click={() =>
                setCollectionSelection(record, path, {
                  selectedIndex: itemIndex,
                  selectedPath: String(item?.path || ""),
                })}
            >
              <span class="start-collection-item-index">
                {item?.label || `[${Number(page?.offset || 0) + itemIndex}]`}
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
        </header>
        <div class="start-collection-stage-body">
          {#if level >= MAX_DEPTH}
            <div class="start-pure-array">{previewText(selectedDescriptor)}</div>
          {:else}
            <svelte:self
              record={selectedRecord}
              label={selectedItem?.label || label}
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
              {loadRecordPage}
              {collectionItemsForPage}
              {collectionSelectionFor}
              {setCollectionSelection}
              {loadCollectionPrev}
              {loadCollectionNext}
              {nestedRecordFromItem}
            />
          {/if}
        </div>
      {:else}
        <div class="start-viewer-message">No selected value</div>
      {/if}
    </section>
    </div>
  {:else}
    <div class="start-value-centered">
      <div class="start-pure-array">{previewText(descriptor)}</div>
    </div>
  {/if}
</div>
