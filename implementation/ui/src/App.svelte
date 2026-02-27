<script>
  import { onMount } from "svelte";
  import { getCapabilities, getVersion } from "$lib/api/client.js";
  import PlaygroundTab from "$lib/components/tabs/PlaygroundTab.svelte";
  import ResultsTab from "$lib/components/tabs/ResultsTab.svelte";
  import GalleryTab from "$lib/components/tabs/GalleryTab.svelte";
  import QualityTab from "$lib/components/tabs/QualityTab.svelte";
  import StorageTab from "$lib/components/tabs/StorageTab.svelte";

  const tabs = [
    { id: "playground", label: "Playground" },
    { id: "results", label: "Results Explorer" },
    { id: "gallery", label: "Example Gallery" },
    { id: "quality", label: "Test Intelligence" },
    { id: "storage", label: "Storage Stats" },
  ];

  let activeTab = "playground";
  let capabilities = {};
  let buildStamp = "Loading...";

  let playgroundTabRef;

  const connectLiveReload = () => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}/livereload`);
    socket.onmessage = (event) => {
      if (event.data === "reload") {
        window.location.reload();
      }
    };
    socket.onclose = () => {
      setTimeout(connectLiveReload, 2000);
    };
  };

  const onGalleryLoad = async (event) => {
    const code = String(event.detail?.code || "");
    const run = Boolean(event.detail?.run);
    activeTab = "playground";
    if (playgroundTabRef && typeof playgroundTabRef.loadProgram === "function") {
      await playgroundTabRef.loadProgram(code, run);
    }
  };

  onMount(async () => {
    try {
      const [caps, version] = await Promise.all([getCapabilities(), getVersion()]);
      capabilities = caps || {};
      buildStamp = `v${version.version || "unknown"}`;
    } catch {
      buildStamp = "version unavailable";
    }

    if (import.meta.env.PROD) {
      connectLiveReload();
    }
  });
</script>

<div class="background-layer"></div>
<div class="shell">
  <header class="topbar">
    <div>
      <p class="eyebrow">VoxLogicA Serve</p>
      <h1>VoxLogicA Studio Console</h1>
    </div>
    <div class="topbar-meta">
      <span id="buildStamp" class="chip">{buildStamp}</span>
    </div>
  </header>

  <nav class="tabbar" aria-label="Main pages">
    {#each tabs as tab}
      <button class={`tab ${activeTab === tab.id ? "active" : ""}`.trim()} type="button" on:click={() => (activeTab = tab.id)}>
        {tab.label}
      </button>
    {/each}
  </nav>

  <main class="content">
    <PlaygroundTab bind:this={playgroundTabRef} active={activeTab === "playground"} {capabilities} />
    <ResultsTab active={activeTab === "results"} {capabilities} />
    <GalleryTab active={activeTab === "gallery"} on:load={onGalleryLoad} />
    <QualityTab active={activeTab === "quality"} {capabilities} />
    <StorageTab active={activeTab === "storage"} />
  </main>
</div>
