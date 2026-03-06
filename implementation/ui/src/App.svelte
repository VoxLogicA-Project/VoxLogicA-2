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
  let tabsMenuEl = null;
  let tabsMenuToggleEl = null;
  let capabilities = {};
  let buildStamp = "Loading...";
  let clientLoggerInstalled = false;
  let clientLogQueue = [];
  let clientLogFlushTimer = null;
  let clientLogInFlight = false;
  const clientLogMaxQueue = 300;
  const clientLogBatchSize = 40;

  let playgroundTabRef;

  const activeTabLabel = () => tabs.find((tab) => tab.id === activeTab)?.label || "Start";

  const selectTab = (tabId) => {
    activeTab = String(tabId || "start");
    tabsMenuOpen = false;
  };

  const toggleTabsMenu = () => {
    tabsMenuOpen = !tabsMenuOpen;
  };

  const handleWindowPointerDown = (event) => {
    if (!tabsMenuOpen) return;
    const target = event?.target;
    if (tabsMenuEl && target instanceof Node && tabsMenuEl.contains(target)) return;
    if (tabsMenuToggleEl && target instanceof Node && tabsMenuToggleEl.contains(target)) return;
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
<svelte:window on:pointerdown={handleWindowPointerDown} on:keydown={handleWindowKeyDown} />
<div class="shell">
  <header class="topbar">
    <div class="topbar-title">
      <h1>VoxLogicA Studio Console</h1>
    </div>
    <div class="topbar-meta">
      <button
        bind:this={tabsMenuToggleEl}
        class={`btn btn-ghost btn-small topbar-menu-toggle ${tabsMenuOpen ? "is-open" : ""}`.trim()}
        type="button"
        aria-controls="main-pages-menu"
        aria-expanded={tabsMenuOpen}
        on:click={toggleTabsMenu}
      >
        <span class="topbar-menu-toggle-label">{activeTabLabel()}</span>
        <span class="topbar-menu-toggle-icon" aria-hidden="true"></span>
      </button>
      <span id="buildStamp" class="chip">{buildStamp}</span>
    </div>
  </header>

  <nav
    id="main-pages-menu"
    bind:this={tabsMenuEl}
    class={`tabbar-menu ${tabsMenuOpen ? "is-open" : ""}`.trim()}
    aria-label="Main pages"
  >
    {#each tabs as tab}
      <button class={`tabbar-menu-item ${activeTab === tab.id ? "active" : ""}`.trim()} type="button" on:click={() => selectTab(tab.id)}>
        {tab.label}
      </button>
    {/each}
  </nav>

  <main class="content">
    <StartTab active={activeTab === "start"} {capabilities} />
    <ComputeLogTab active={activeTab === "compute-log"} />
    <StartTechnicalTab active={activeTab === "start-tech"} {capabilities} />
    <PlaygroundTab bind:this={playgroundTabRef} active={activeTab === "playground"} {capabilities} />
    <ResultsTab active={activeTab === "results"} {capabilities} />
    <GalleryTab active={activeTab === "gallery"} on:load={onGalleryLoad} />
    <QualityTab active={activeTab === "quality"} {capabilities} />
  </main>
</div>
