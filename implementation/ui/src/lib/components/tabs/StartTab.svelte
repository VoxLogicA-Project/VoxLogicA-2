<script>
  import { onDestroy, onMount } from "svelte";
  import { getProgramSymbols, resolvePlaygroundValue, resolvePlaygroundValuePage } from "$lib/api/client.js";
  import { summarizeDescriptor } from "$lib/utils/playground-value.js";
  import VoxCodeEditor from "$lib/components/editor/VoxCodeEditor.svelte";
  import StatusChip from "$lib/components/shared/StatusChip.svelte";

  export let active = false;
  export let capabilities = {};

  const STORAGE_KEY = "voxlogica.start.program.v1";
  const PRIMARY_VARIABLE_PREFERENCES = ["result", "output", "masks", "vi_sweep_masks"];
  const COMPLETION_BUILTINS = [
    "map",
    "for",
    "range",
    "subsequence",
    "dir",
    "ReadImage",
    "BinaryThreshold",
    "intensity",
    "percentiles",
    "smoothen",
    "grow",
    "touch",
    "not",
    "leq_sv",
    "geq_sv",
    "index",
  ];

  const DEFAULT_PROGRAM = `import "simpleitk"

dataset_root = "tests/data/datasets/BraTS_2019_HGG"
k = 10
hi_thr = 0.93
vi_thr_start = 83
vi_thr_stop = 92
vi_ticks = range(vi_thr_start, vi_thr_stop)
to_thr(tick) = tick / 100
vi_thresholds = map(to_thr, vi_ticks)
all_flair_paths = dir(dataset_root, "*_flair.nii.gz", true, true)
flair_paths = subsequence(all_flair_paths, 0, k)
read_image(path) = ReadImage(path)
to_intensity(img) = intensity(img)
preprocess_flair(flair) =
  let background = touch(leq_sv(0.1, flair), border) in
  let brain = not(background) in
  percentiles(flair, brain, 0)
sweep_case(pflair) =
  let hyper_intense = smoothen(geq_sv(hi_thr, pflair), 5.0) in
  for vi_thr in vi_thresholds do
    let very_intense = smoothen(geq_sv(vi_thr, pflair), 2.0) in
    grow(hyper_intense, very_intense)
flair_images = map(read_image, flair_paths)
flair_intensities = map(to_intensity, flair_images)
pflair_images = map(preprocess_flair, flair_intensities)
vi_sweep_masks = map(sweep_case, pflair_images)`;

  let programText = DEFAULT_PROGRAM;
  let symbolTable = {};
  let symbolDiagnostics = [];
  let primaryVariable = "";

  let splitPercent = 62;
  let resizeActive = false;

  let viewerContainer;
  let viewer = null;

  let captionVariable = "-";
  let captionType = "-";
  let statusValue = "idle";
  let statusText = "Write code and run to materialize a result.";
  let errorText = "";
  let currentPath = "";
  let pendingPoll = null;
  let pendingSave = null;

  let initialized = false;
  let loadToken = 0;

  const diagnosticsText = () => {
    const rows = Array.isArray(symbolDiagnostics) ? symbolDiagnostics : [];
    if (!rows.length) return "";
    return rows
      .map((diag) => {
        const code = diag?.code ? `[${diag.code}] ` : "";
        const message = diag?.message ? String(diag.message) : "Static error";
        const location = diag?.location ? ` @ ${diag.location}` : "";
        return `${code}${message}${location}`;
      })
      .join("\n");
  };

  const provideEditorCompletions = ({ prefix = "" }) => {
    const base = new Map();
    for (const keyword of ["import", "let", "in", "for", "do", "true", "false"]) {
      base.set(keyword, {
        label: keyword,
        insertText: keyword,
        kind: "keyword",
        detail: "language keyword",
      });
    }
    for (const builtin of COMPLETION_BUILTINS) {
      base.set(builtin, {
        label: builtin,
        insertText: builtin,
        kind: "primitive",
        detail: "known primitive",
      });
    }
    for (const symbol of Object.keys(symbolTable || {})) {
      base.set(symbol, {
        label: symbol,
        insertText: symbol,
        kind: "symbol",
        detail: "declared symbol",
      });
    }
    const prefixLower = String(prefix || "").toLowerCase();
    return Array.from(base.values())
      .filter((item) => !prefixLower || item.label.toLowerCase().startsWith(prefixLower))
      .sort((a, b) => String(a.label).localeCompare(String(b.label)));
  };

  const stopPoll = () => {
    if (!pendingPoll) return;
    clearInterval(pendingPoll);
    pendingPoll = null;
  };

  const schedulePersist = () => {
    if (pendingSave) clearTimeout(pendingSave);
    pendingSave = setTimeout(() => {
      try {
        window.localStorage.setItem(STORAGE_KEY, String(programText || ""));
      } catch {
        // ignore persistence errors in restricted browser contexts
      }
    }, 180);
  };

  const inferPrimaryVariable = (sourceText, symbols) => {
    const symbolKeys = Object.keys(symbols || {});
    if (!symbolKeys.length) return "";

    for (const preferred of PRIMARY_VARIABLE_PREFERENCES) {
      if (symbolKeys.includes(preferred)) return preferred;
    }

    const declarationMatches = [...String(sourceText || "").matchAll(/^\s*([A-Za-z_.$][A-Za-z0-9_.$]*)\s*=/gm)];
    for (let idx = declarationMatches.length - 1; idx >= 0; idx -= 1) {
      const candidate = String(declarationMatches[idx]?.[1] || "");
      if (candidate && symbols[candidate]) return candidate;
    }

    return symbolKeys[symbolKeys.length - 1] || symbolKeys[0] || "";
  };

  const ensureViewer = () => {
    if (viewer || !viewerContainer) return;

    const ctor = window.VoxResultViewer?.ResultViewer;
    if (typeof ctor === "function") {
      viewer = new ctor(viewerContainer, {
        onNavigate: (path) => {
          currentPath = String(path || "");
          void resolvePrimaryValue({ enqueue: false, path: currentPath });
        },
        fetchPage: ({ nodeId, path, offset, limit }) =>
          resolvePlaygroundValuePage({
            program: programText,
            nodeId: nodeId || "",
            variable: primaryVariable,
            path: path || "",
            offset: Number(offset || 0),
            limit: Number(limit || 64),
            enqueue: true,
          }),
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

  const applyFailure = (payload, variableName) => {
    const message = String(payload?.error || "Unable to inspect value.");
    statusValue = "failed";
    statusText = message;
    captionVariable = variableName || "-";
    captionType = "error";
    errorText = message;
    viewer.renderRecord({
      available: false,
      node_id: payload?.node_id || "",
      status: "failed",
      path: payload?.path || "/",
      error: message,
      descriptor: {
        vox_type: "string",
        format_version: "voxpod/1",
        summary: { value: message, length: message.length, truncated: false },
        navigation: {
          path: payload?.path || "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
  };

  const applyPending = (payload, variableName) => {
    const state = String(payload?.compute_status || payload?.materialization || "running");
    statusValue = "running";
    statusText = `Computing ${variableName} (${state})`;
    captionVariable = variableName || "-";
    captionType = payload?.descriptor?.vox_type || "in-progress";
    errorText = "";
    viewer.renderRecord({
      ...payload,
      available: false,
      status: state,
      path: payload?.path || currentPath || "/",
      descriptor:
        payload?.descriptor && typeof payload.descriptor === "object"
          ? payload.descriptor
          : {
              vox_type: "unavailable",
              format_version: "voxpod/1",
              summary: { reason: `status=${state}` },
              navigation: {
                path: payload?.path || currentPath || "/",
                pageable: false,
                can_descend: false,
                default_page_size: 64,
                max_page_size: 512,
              },
            },
    });
  };

  const applyMaterialized = (payload, variableName) => {
    const descriptor = payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : null;
    const materialization = String(payload?.materialization || payload?.status || "materialized");
    viewer.renderRecord(payload);
    statusValue = "completed";
    statusText = descriptor ? summarizeDescriptor(descriptor) : `Computed ${variableName}`;
    captionVariable = variableName || "-";
    captionType = descriptor?.vox_type || "value";
    errorText = "";
    if (materialization === "cached") {
      statusText = `${statusText} (cached)`;
    }
  };

  const resolvePrimaryValue = async ({ enqueue = true, path = "" } = {}) => {
    ensureViewer();
    if (!primaryVariable) {
      statusValue = "idle";
      statusText = "Define at least one variable to inspect.";
      captionVariable = "-";
      captionType = "-";
      viewer.renderRecord(null);
      return;
    }
    if (symbolDiagnostics.length) {
      statusValue = "failed";
      statusText = "Fix static diagnostics before execution.";
      captionVariable = primaryVariable;
      captionType = "error";
      viewer.setError(statusText);
      errorText = statusText;
      return;
    }

    currentPath = String(path || "");
    statusValue = "running";
    statusText = `Resolving ${primaryVariable}...`;
    captionVariable = primaryVariable;
    captionType = "in-progress";
    errorText = "";
    viewer.setLoading(`Resolving ${primaryVariable}${currentPath ? ` @ ${currentPath}` : ""}...`);

    try {
      const payload = await resolvePlaygroundValue({
        program: programText,
        variable: primaryVariable,
        path: currentPath,
        enqueue,
      });

      const materialization = String(payload?.materialization || "");
      const computeStatus = String(payload?.compute_status || "");
      const descriptor = payload?.descriptor && typeof payload.descriptor === "object" ? payload.descriptor : null;
      const isMaterialized = (materialization === "cached" || materialization === "computed") && !!descriptor;
      const isPending =
        materialization === "pending" ||
        materialization === "missing" ||
        computeStatus === "queued" ||
        computeStatus === "running" ||
        computeStatus === "persisting";

      if (isMaterialized) {
        stopPoll();
        applyMaterialized(payload, primaryVariable);
        return;
      }

      if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") {
        stopPoll();
        applyFailure(payload, primaryVariable);
        return;
      }

      if (isPending) {
        applyPending(payload, primaryVariable);
        if (!pendingPoll) {
          pendingPoll = setInterval(() => {
            void resolvePrimaryValue({ enqueue: false, path: currentPath });
          }, 1000);
        }
        return;
      }

      stopPoll();
      applyFailure(
        {
          ...payload,
          error: payload?.error || "Unexpected materialization state.",
        },
        primaryVariable,
      );
    } catch (error) {
      stopPoll();
      applyFailure({ error: error.message || "Request failed." }, primaryVariable);
    }
  };

  const refreshSymbols = async () => {
    if (capabilities.playground_symbols === false) {
      symbolTable = {};
      symbolDiagnostics = [];
      primaryVariable = "";
      captionVariable = "-";
      captionType = "-";
      statusValue = "failed";
      statusText = "Program symbol API unavailable on this backend.";
      errorText = statusText;
      return;
    }

    const token = loadToken + 1;
    loadToken = token;

    const source = String(programText || "").trim();
    if (!source) {
      symbolTable = {};
      symbolDiagnostics = [];
      primaryVariable = "";
      captionVariable = "-";
      captionType = "-";
      statusValue = "idle";
      statusText = "Write code and run to materialize a result.";
      errorText = "";
      return;
    }

    try {
      const payload = await getProgramSymbols(programText);
      if (token !== loadToken) return;
      const available = payload?.available !== false;
      symbolTable = available ? payload.symbol_table || {} : {};
      symbolDiagnostics = payload?.diagnostics || [];
      primaryVariable = inferPrimaryVariable(programText, symbolTable);
      captionVariable = primaryVariable || "-";
      if (symbolDiagnostics.length) {
        statusValue = "failed";
        statusText = "Static diagnostics detected.";
        errorText = statusText;
      } else if (primaryVariable) {
        statusValue = "idle";
        statusText = `Ready to resolve ${primaryVariable}.`;
        errorText = "";
      }
    } catch (error) {
      if (token !== loadToken) return;
      symbolTable = {};
      symbolDiagnostics = [{ code: "E_SYMBOLS", message: error.message || "Unable to refresh symbols." }];
      primaryVariable = "";
      captionVariable = "-";
      statusValue = "failed";
      statusText = "Unable to refresh symbols.";
      errorText = statusText;
    }
  };

  const handleEditorChange = async (event) => {
    programText = String(event?.detail?.value ?? programText ?? "");
    schedulePersist();
    await refreshSymbols();
  };

  const handleEditorSymbolClick = async (event) => {
    const token = String(event?.detail?.token || "");
    if (!token || !symbolTable[token]) return;
    primaryVariable = token;
    captionVariable = token;
    currentPath = "";
    await resolvePrimaryValue({ enqueue: true, path: "" });
  };

  const runPrimary = async () => {
    stopPoll();
    await refreshSymbols();
    await resolvePrimaryValue({ enqueue: true, path: "" });
  };

  const startResize = (event) => {
    event.preventDefault();
    const splitRoot = event.currentTarget?.parentElement;
    if (!(splitRoot instanceof HTMLElement)) return;

    const rect = splitRoot.getBoundingClientRect();
    resizeActive = true;

    const onPointerMove = (moveEvent) => {
      const y = moveEvent.clientY - rect.top;
      const next = Math.max(28, Math.min(82, (y / rect.height) * 100));
      splitPercent = Number(next.toFixed(2));
    };

    const onPointerUp = () => {
      resizeActive = false;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  };

  onMount(async () => {
    ensureViewer();
    try {
      const persisted = window.localStorage.getItem(STORAGE_KEY);
      if (persisted && String(persisted).trim()) {
        programText = persisted;
      }
    } catch {
      // ignore localStorage errors
    }

    initialized = true;
    await refreshSymbols();
  });

  onDestroy(() => {
    stopPoll();
    if (pendingSave) clearTimeout(pendingSave);
    if (viewer && typeof viewer.destroy === "function") {
      viewer.destroy();
    }
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-start">
  <article class="card start-shell">
    <div class="start-split" style={`grid-template-rows: minmax(220px, ${splitPercent}fr) 10px minmax(220px, ${100 - splitPercent}fr);`}>
      <section class="start-result">
        <header class="start-head">
          <h2>Start</h2>
          <div class="row gap-s">
            <StatusChip value={statusValue} />
            <button class="btn btn-primary btn-small" type="button" on:click={runPrimary}>Run</button>
          </div>
        </header>
        <div class="start-viewer-wrap">
          <div class="result-inspector start-viewer" bind:this={viewerContainer}></div>
        </div>
        <footer class="start-caption">
          <span class="start-caption-main">{captionVariable}</span>
          <span class="start-caption-type">{captionType}</span>
        </footer>
      </section>

      <div
        class={`start-divider ${resizeActive ? "is-active" : ""}`}
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize result and code panes"
        on:pointerdown={startResize}
      >
        <span></span>
      </div>

      <section class="start-editor">
        <VoxCodeEditor
          ariaLabel="Start tab code editor"
          bind:value={programText}
          symbols={symbolTable}
          diagnostics={symbolDiagnostics}
          autocompleteEnabled={true}
          completionProvider={provideEditorCompletions}
          completionBuiltins={COMPLETION_BUILTINS}
          on:change={handleEditorChange}
          on:symbolclick={handleEditorSymbolClick}
        />
      </section>
    </div>

    <p class="start-status muted">{statusText}</p>

    {#if diagnosticsText()}
      <div class="inline-error">{diagnosticsText()}</div>
    {:else if errorText}
      <div class="inline-error">{errorText}</div>
    {/if}
  </article>
</section>
