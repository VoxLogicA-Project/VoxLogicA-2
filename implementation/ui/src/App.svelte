<script>
  import { onMount } from "svelte";
  import { getCapabilities, getVersion, sendClientLogBatch } from "$lib/api/client.js";
  import StartTab from "$lib/components/tabs/StartTab.svelte";
  import ComputeLogTab from "$lib/components/tabs/ComputeLogTab.svelte";
  import StartTechnicalTab from "$lib/components/tabs/StartTechnicalTab.svelte";
  import PlaygroundTab from "$lib/components/tabs/PlaygroundTab.svelte";
  import ResultsTab from "$lib/components/tabs/ResultsTab.svelte";
  import GalleryTab from "$lib/components/tabs/GalleryTab.svelte";
  import QualityTab from "$lib/components/tabs/QualityTab.svelte";

  const tabs = [
    { id: "start", label: "Start" },
    { id: "compute-log", label: "Compute Log" },
    { id: "start-tech", label: "Start Technical" },
    { id: "playground", label: "Playground" },
    { id: "results", label: "Results Explorer" },
    { id: "gallery", label: "Example Gallery" },
    { id: "quality", label: "Test Intelligence" },
  ];

  let activeTab = "start";
  let tabsMenuOpen = false;
  let capabilities = {};
  let buildStamp = "Loading...";
  let startTabRef;
  let clientLoggerInstalled = false;
  let clientLogQueue = [];
  let clientLogFlushTimer = null;
  let clientLogInFlight = false;
  const clientLogMaxQueue = 300;
  const clientLogBatchSize = 40;

  const selectTab = (tabId) => {
    activeTab = String(tabId || "start");
    tabsMenuOpen = false;
  };

  const toggleTabsMenu = () => {
    tabsMenuOpen = !tabsMenuOpen;
  };

  const closeTabsMenu = () => {
    tabsMenuOpen = false;
  };

  const handleWindowKeyDown = (event) => {
    if (String(event?.key || "") === "Escape") {
      tabsMenuOpen = false;
    }
  };

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
    const firstArg = Array.isArray(args) && args.length ? String(args[0] || "") : "";
    if (firstArg.startsWith("[api.request]")) {
      return;
    }
    if (firstArg.startsWith("[start-tab.resolve]")) {
      return;
    }
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
    activeTab = "start";
    tabsMenuOpen = false;
    if (startTabRef && typeof startTabRef.loadProgram === "function") {
      await startTabRef.loadProgram(code, run);
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
<svelte:window on:keydown={handleWindowKeyDown} />
<div class="shell">
  <header class="topbar">
    <div class="topbar-left">
      <button
        class={`btn btn-ghost btn-small topbar-hamburger ${tabsMenuOpen ? "is-open" : ""}`.trim()}
        type="button"
        aria-controls="main-pages-drawer"
        aria-expanded={tabsMenuOpen}
        aria-label="Toggle navigation menu"
        on:click={toggleTabsMenu}
      >
        <span class="topbar-hamburger-icon" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </span>
        <span class="topbar-hamburger-label">Menu</span>
      </button>
      <div class="topbar-title">
        <h1>VoxLogicA</h1>
      </div>
    </div>
    <div class="topbar-meta">
      <span id="buildStamp" class="chip">{buildStamp}</span>
    </div>
  </header>

  <button
    type="button"
    class={`app-drawer-backdrop ${tabsMenuOpen ? "is-open" : ""}`.trim()}
    aria-label="Close navigation menu"
    on:click={closeTabsMenu}
  ></button>
  <aside id="main-pages-drawer" class={`app-drawer ${tabsMenuOpen ? "is-open" : ""}`.trim()} aria-hidden={!tabsMenuOpen}>
    <div class="app-drawer-head">
      <h2>Pages</h2>
      <button class="btn btn-ghost btn-small" type="button" aria-label="Close menu" on:click={closeTabsMenu}>Close</button>
    </div>
    <nav class="app-drawer-nav" aria-label="Main pages">
      {#each tabs as tab}
        <button class={`app-drawer-item ${activeTab === tab.id ? "active" : ""}`.trim()} type="button" on:click={() => selectTab(tab.id)}>
          {tab.label}
        </button>
      {/each}
    </nav>
  </aside>

  <main class="content">
    <StartTab bind:this={startTabRef} active={activeTab === "start"} {capabilities} />
    <ComputeLogTab active={activeTab === "compute-log"} />
    <StartTechnicalTab active={activeTab === "start-tech"} {capabilities} />
    <PlaygroundTab active={activeTab === "playground"} {capabilities} />
    <ResultsTab active={activeTab === "results"} {capabilities} />
    <GalleryTab active={activeTab === "gallery"} on:load={onGalleryLoad} />
    <QualityTab active={activeTab === "quality"} {capabilities} />
  </main>
</div>
