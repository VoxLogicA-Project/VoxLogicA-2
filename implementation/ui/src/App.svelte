<script>
  import { onMount } from "svelte";
  import { getCapabilities, getVersion, sendClientLogBatch } from "$lib/api/client.js";
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
  let clientLoggerInstalled = false;
  let clientLogQueue = [];
  let clientLogFlushTimer = null;
  let clientLogInFlight = false;
  const clientLogMaxQueue = 300;
  const clientLogBatchSize = 40;

  let playgroundTabRef;

  const toLogMessage = (args) =>
    args
      .map((arg) => {
        if (arg instanceof Error) {
          return arg.stack || arg.message || String(arg);
        }
        if (typeof arg === "string") return arg;
        try {
          return JSON.stringify(arg);
        } catch {
          return String(arg);
        }
      })
      .join(" ");

  const flushClientLogQueue = async () => {
    if (clientLogInFlight || !clientLogQueue.length) return;
    clientLogInFlight = true;
    const batch = clientLogQueue.splice(0, clientLogBatchSize);
    try {
      await sendClientLogBatch(batch);
    } catch {
      clientLogQueue = [...batch, ...clientLogQueue].slice(-clientLogMaxQueue);
    } finally {
      clientLogInFlight = false;
      if (clientLogQueue.length) {
        if (clientLogFlushTimer) clearTimeout(clientLogFlushTimer);
        clientLogFlushTimer = setTimeout(flushClientLogQueue, 800);
      }
    }
  };

  const enqueueClientLog = (level, args, payload = null) => {
    clientLogQueue.push({
      level,
      message: toLogMessage(args),
      source: "browser-console",
      url: window.location.href,
      ts: new Date().toISOString(),
      user_agent: navigator.userAgent,
      payload,
    });
    if (clientLogQueue.length > clientLogMaxQueue) {
      clientLogQueue = clientLogQueue.slice(-clientLogMaxQueue);
    }
    if (clientLogFlushTimer) clearTimeout(clientLogFlushTimer);
    clientLogFlushTimer = setTimeout(flushClientLogQueue, 250);
  };

  const installClientLogger = () => {
    if (clientLoggerInstalled || typeof window === "undefined" || typeof window.WebSocket === "undefined") {
      return;
    }
    clientLoggerInstalled = true;

    const levels = ["log", "info", "warn", "error", "debug"];
    for (const level of levels) {
      const original = console[level] ? console[level].bind(console) : console.log.bind(console);
      console[level] = (...args) => {
        original(...args);
        enqueueClientLog(level, args);
      };
    }

    window.addEventListener("error", (event) => {
      enqueueClientLog("error", [event.message || "window error"], {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
      });
    });
    window.addEventListener("unhandledrejection", (event) => {
      const reason = event.reason;
      enqueueClientLog("error", ["unhandledrejection", reason instanceof Error ? reason.stack || reason.message : reason], null);
    });
  };

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
    installClientLogger();
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
