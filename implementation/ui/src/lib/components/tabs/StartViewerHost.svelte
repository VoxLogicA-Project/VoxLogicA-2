<script>
  import { onDestroy } from "svelte";
  import { createViewerAdapter } from "$lib/components/tabs/viewers/viewerAdapters.js";

  export let contract = null;

  let hostEl = null;
  let adapter = null;
  let adapterKey = "";
  let applySeq = 0;

  // Keep one stable host per viewer slot. Contracts can change freely; the
  // host swaps adapters only when the viewer family changes.
  const applyContract = async (nextContract) => {
    if (!hostEl) return;
    const seq = applySeq + 1;
    applySeq = seq;

    const nextKey = String(nextContract?.adapterKey || "");
    if (!nextContract || !nextKey) {
      if (adapter && typeof adapter.destroy === "function") {
        adapter.destroy();
      }
      adapter = null;
      adapterKey = "";
      hostEl.replaceChildren();
      return;
    }

    if (!adapter || nextKey !== adapterKey) {
      if (adapter && typeof adapter.destroy === "function") {
        adapter.destroy();
      }
      adapter = createViewerAdapter(hostEl, nextContract);
      adapterKey = nextKey;
    }

    if (!adapter || typeof adapter.update !== "function") return;
    await adapter.update(nextContract);
    if (seq !== applySeq) return;
  };

  $: if (hostEl) {
    void applyContract(contract);
  }

  onDestroy(() => {
    applySeq += 1;
    if (adapter && typeof adapter.destroy === "function") {
      adapter.destroy();
    }
  });
</script>

<div bind:this={hostEl} class="start-viewer-host"></div>