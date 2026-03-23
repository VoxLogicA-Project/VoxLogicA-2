<script>
  import { onDestroy, onMount } from "svelte";
  import { getCapabilities, getVersion, sendClientLogBatch } from "$lib/api/client.js";
  import StartTab from "$lib/components/tabs/StartTab.svelte";
  import ComputeLogTab from "$lib/components/tabs/ComputeLogTab.svelte";
  import StartTechnicalTab from "$lib/components/tabs/StartTechnicalTab.svelte";
  import DreamTab from "$lib/components/tabs/DreamTab.svelte";
  import GraphTab from "$lib/components/tabs/GraphTab.svelte";
  import PlaygroundTab from "$lib/components/tabs/PlaygroundTab.svelte";
  import ResultsTab from "$lib/components/tabs/ResultsTab.svelte";
  import GalleryTab from "$lib/components/tabs/GalleryTab.svelte";
  import QualityTab from "$lib/components/tabs/QualityTab.svelte";
  import {
    readPersistedAppState,
    readPersistedStartProgram,
    updatePersistedAppState,
  } from "$lib/utils/ui-persistence.js";

  const tabs = [
    { id: "start", label: "Start" },
    { id: "compute-log", label: "Compute Log" },
    { id: "dream", label: "Oneiric Trace" },
    { id: "graph", label: "Compute Graph" },
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
  const AUTOMATION_BRIDGE_NAME = "__VOXLOGICA_AUTOMATION__";
  let appPersistenceReady = false;

  const selectTab = (tabId) => {
    activeTab = String(tabId || "start");
    tabsMenuOpen = false;
  };

  const restorePersistedAppState = () => {
    const persisted = readPersistedAppState();
    const persistedTab = String(persisted?.activeTab || "").trim();
    if (tabs.some((tab) => tab.id === persistedTab)) {
      activeTab = persistedTab;
    }
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

  const getTabSnapshot = () => tabs.map((tab) => ({ ...tab, active: activeTab === tab.id }));

  const getAppStateSnapshot = () => ({
    activeTab,
    tabs: getTabSnapshot(),
    tabsMenuOpen: Boolean(tabsMenuOpen),
    buildStamp,
    capabilities: { ...(capabilities || {}) },
    start: startTabRef && typeof startTabRef.getAutomationState === "function" ? startTabRef.getAutomationState() : null,
  });

  const selectTabById = (tabId) => {
    const normalized = String(tabId || "").trim();
    if (!tabs.some((tab) => tab.id === normalized)) {
      return {
        ok: false,
        error: `Unknown tab: ${normalized || "<empty>"}`,
        state: getAppStateSnapshot(),
      };
    }
    selectTab(normalized);
    return {
      ok: true,
      state: getAppStateSnapshot(),
    };
  };

  const loadProgramInStartTab = async (code, runAfterLoad = false) => {
    activeTab = "start";
    tabsMenuOpen = false;
    if (startTabRef && typeof startTabRef.loadProgram === "function") {
      await startTabRef.loadProgram(code, runAfterLoad);
      return {
        ok: true,
        state: getAppStateSnapshot(),
      };
    }
    return {
      ok: false,
      error: "Start tab is not ready.",
      state: getAppStateSnapshot(),
    };
  };

  const selectStartSymbol = async (token) => {
    activeTab = "start";
    tabsMenuOpen = false;
    if (startTabRef && typeof startTabRef.selectSymbol === "function") {
      return await startTabRef.selectSymbol(token);
    }
    return {
      ok: false,
      error: "Start tab is not ready.",
      state: getAppStateSnapshot(),
    };
  };

  const publishAutomationBridge = () => {
    if (typeof window === "undefined") return;
    window[AUTOMATION_BRIDGE_NAME] = {
      version: 1,
      actionAreas: {
        browser: ["open_page", "inspect_page", "focus_app", "close_browser"],
        ui: ["inspect_app_state", "select_app_tab", "click_element", "focus_element", "read_element_text"],
        program: ["read_program", "set_program", "click_variable"],
        runtime: [
          "inspect_runtime_state",
          "list_playground_jobs",
          "get_playground_job",
          "kill_playground_job",
          "get_program_symbols",
          "get_program_graph",
          "resolve_program_value",
          "resolve_program_value_page",
        ],
      },
      getAppState: () => getAppStateSnapshot(),
      selectTab: (tabId) => selectTabById(tabId),
      getProgram: () => {
        if (startTabRef && typeof startTabRef.getProgramText === "function") {
          return startTabRef.getProgramText();
        }
        return readPersistedStartProgram("");
      },
      loadProgram: async (code, runAfterLoad = false) => await loadProgramInStartTab(code, runAfterLoad),
      selectStartSymbol: async (token) => await selectStartSymbol(token),
    };
  };

  const removeAutomationBridge = () => {
    if (typeof window === "undefined") return;
    delete window[AUTOMATION_BRIDGE_NAME];
  };

  $: if (typeof window !== "undefined") {
    publishAutomationBridge();
  }

  $: if (appPersistenceReady) {
    updatePersistedAppState({ activeTab });
  }

  onMount(async () => {
    restorePersistedAppState();
    appPersistenceReady = true;
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

    publishAutomationBridge();
  });

  onDestroy(() => {
    removeAutomationBridge();
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
    <DreamTab active={activeTab === "dream"} />
    <GraphTab active={activeTab === "graph"} />
    <PlaygroundTab active={activeTab === "playground"} {capabilities} />
    <ResultsTab active={activeTab === "results"} {capabilities} />
    <GalleryTab active={activeTab === "gallery"} on:load={onGalleryLoad} />
    <QualityTab active={activeTab === "quality"} {capabilities} />
  </main>
</div>
