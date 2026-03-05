<script>
  import { onDestroy, onMount } from "svelte";
  import { getProgramSymbols, resolvePlaygroundValue, resolvePlaygroundValuePage } from "$lib/api/client.js";
  import { buildExecutionLogRows } from "$lib/utils/logs.js";
  import VoxCodeEditor from "$lib/components/editor/VoxCodeEditor.svelte";
  import StartValueCanvas from "$lib/components/tabs/StartValueCanvas.svelte";

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

  let viewer = null;

  let captionVariable = "-";
  let statusValue = "idle";
  let statusText = "Write code and run to compute a value.";
  let errorText = "";
  let pendingLogSummary = "No execution log yet.";
  let pendingLogRows = [];
  let pendingLogRaw = "";
  let pendingLogJobId = "";
  let symbolStatuses = {};
  let symbolMaterializations = {};
  let materializedRecords = {};
  let symbolTypeHints = {};
  let editorSymbolTypes = {};
  let selectedVisualSymbols = [];
  let viewerSupportsMultiValue = false;
  let viewerRecords = [];
  let viewerMode = "empty";
  let viewerMessage = "";
  let viewerErrorMessage = "";
  let recordPages = {};
  let recordPagePointers = {};
  let recordPagesLoading = {};
  let recordPagesErrors = {};
  let collectionSelections = {};
  let recordPagePollTimers = {};
  let pathRecords = {};
  let pathRecordsLoading = {};
  let pathRecordsErrors = {};
  let pathRecordPollTimers = {};
  let resolutionActivityRows = [];
  let resolutionActivitySummary = "No resolution activity yet.";
  let activitySeenKeys = new Set();
  let dreamNodeIds = [];
  let dreamVisible = false;
  let dreamDissolving = false;
  let pendingDreamCleanup = null;
  let currentPath = "";
  let pendingPoll = null;
  let pendingPollTicks = 0;
  let pendingSave = null;
  let pendingProbe = null;
  let probeToken = 0;
  let resolveTraceSeq = 0;
  let resolveRequestSeq = 0;
  let resolveInFlight = false;
  let maximizedViewerIndex = -1;
  const MAX_PENDING_POLL_TICKS = 45;
  const COLLECTION_PAGE_SIZE = 18;

  let loadToken = 0;

  const TYPE_LABELS = {
    integer: "number",
    number: "number",
    boolean: "boolean",
    string: "text",
    bytes: "bytes",
    ndarray: "array",
    image2d: "image",
    volume3d: "volume",
    mapping: "object",
    sequence: "collection",
    unavailable: "pending",
    error: "error",
  };

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

  const statusRank = {
    idle: 0,
    queued: 1,
    running: 2,
    persisting: 2,
    computed: 3,
    failed: 4,
  };

  const normalizeStatus = (status) => {
    const normalized = String(status || "").trim().toLowerCase();
    if (normalized === "cached" || normalized === "completed") return "computed";
    if (normalized === "pending" || normalized === "missing") return "queued";
    if (normalized === "killed") return "failed";
    if (normalized in statusRank) return normalized;
    return "idle";
  };

  const normalizeMaterialization = (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) return "unresolved";
    if (normalized === "missing") return "pending";
    if (normalized === "materialized" || normalized === "completed") return "computed";
    if (normalized === "killed") return "failed";
    if (["cached", "computed", "pending", "queued", "running", "persisting", "failed", "unresolved"].includes(normalized)) {
      return normalized;
    }
    return "unresolved";
  };

  const typeLabelFromDescriptor = (descriptor) => {
    const rawType = String(descriptor?.vox_type || "").trim().toLowerCase();
    if (!rawType) return "value";
    return TYPE_LABELS[rawType] || rawType.replaceAll("_", " ");
  };

  const symbolTypeLabel = (name) => {
    const key = String(name || "");
    if (!key) return "value";
    const hinted = String(symbolTypeHints?.[key] || "").trim().toLowerCase();
    if (hinted) return TYPE_LABELS[hinted] || hinted.replaceAll("_", " ");
    const record = materializedRecords?.[key];
    return typeLabelFromDescriptor(record?.descriptor);
  };

  const symbolTypeTitle = (name) => `${String(name || "")} (${symbolTypeLabel(name)})`;

  const statusLabel = (name) => {
    const key = String(name || "");
    if (!key) return "idle";
    if (materializedRecords?.[key]?.descriptor) return "computed";
    const status = normalizeStatus(symbolStatuses?.[key] || "idle");
    const materialization = normalizeMaterialization(symbolMaterializations?.[key] || "unresolved");
    if (materialization === "computed") return "computed";
    if (materialization === "failed") return "failed";
    if (["queued", "running", "persisting", "failed", "computed"].includes(status)) return status;
    if (["queued", "running", "persisting"].includes(materialization)) return materialization;
    return "idle";
  };

  const shortNodeId = (nodeId) => {
    const text = String(nodeId || "");
    if (!text) return "";
    if (text.length <= 20) return text;
    return `${text.slice(0, 10)}…${text.slice(-6)}`;
  };

  const traceResolve = (event, details = {}) => {
    try {
      console.info("[start-tab.resolve]", {
        event,
        ...details,
      });
    } catch {
      // best-effort instrumentation only
    }
  };

  const clearResolutionActivity = () => {
    resolutionActivityRows = [];
    resolutionActivitySummary = "No resolution activity yet.";
    activitySeenKeys = new Set();
  };

  const syncSymbolStatuses = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      next[name] = normalizeStatus(symbolStatuses?.[name] || "idle");
    }
    symbolStatuses = next;
  };

  const syncSymbolMaterializations = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      next[name] = normalizeMaterialization(symbolMaterializations?.[name] || "unresolved");
    }
    symbolMaterializations = next;
  };

  const syncMaterializedRecords = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      if (materializedRecords?.[name]) {
        next[name] = materializedRecords[name];
      }
    }
    materializedRecords = next;
  };

  const syncSymbolTypeHints = (symbols) => {
    const next = {};
    for (const name of Object.keys(symbols || {})) {
      const hinted = String(symbolTypeHints?.[name] || "").trim();
      if (hinted) {
        next[name] = hinted;
      } else if (materializedRecords?.[name]?.descriptor?.vox_type) {
        next[name] = String(materializedRecords[name].descriptor.vox_type);
      }
    }
    symbolTypeHints = next;
  };

  const ensureSelectedVisualSymbols = () => {
    const names = Object.keys(symbolTable || {});
    const retained = selectedVisualSymbols.filter((name) => names.includes(name));
    if (retained.length) {
      selectedVisualSymbols = retained;
      return;
    }
    if (primaryVariable && names.includes(primaryVariable)) {
      selectedVisualSymbols = [primaryVariable];
      return;
    }
    if (names.length) {
      selectedVisualSymbols = [names[0]];
      return;
    }
    selectedVisualSymbols = [];
  };

  const setSymbolMaterialization = (name, materialization) => {
    const symbolName = String(name || "").trim();
    if (!symbolName || !symbolTable?.[symbolName]) return;
    symbolMaterializations = {
      ...symbolMaterializations,
      [symbolName]: normalizeMaterialization(materialization),
    };
  };

  const setSymbolStatus = (name, status) => {
    const symbolName = String(name || "").trim();
    if (!symbolName || !symbolTable?.[symbolName]) return;
    const nextStatus = normalizeStatus(status);
    const currentStatus = normalizeStatus(symbolStatuses?.[symbolName] || "idle");
    if (statusRank[nextStatus] < statusRank[currentStatus] && currentStatus !== "failed") return;
    symbolStatuses = { ...symbolStatuses, [symbolName]: nextStatus };
  };

  const symbolNamesByNodeId = () => {
    const byNode = {};
    for (const [name, node] of Object.entries(symbolTable || {})) {
      if (!node) continue;
      const key = String(node);
      if (!byNode[key]) byNode[key] = [];
      byNode[key].push(String(name));
    }
    return byNode;
  };

  const pushResolutionActivity = ({
    variableName = "",
    nodeId = "",
    operator = "",
    status = "running",
    cacheSource = "",
    durationS = 0,
  } = {}) => {
    const normalizedStatus = normalizeStatus(status);
    const key = `${variableName}|${nodeId}|${operator}|${normalizedStatus}|${cacheSource}|${Number(durationS || 0).toFixed(6)}`;
    if (activitySeenKeys.has(key)) return;
    activitySeenKeys.add(key);
    const nextRow = {
      ts: new Date().toISOString(),
      variableName: String(variableName || ""),
      nodeId: String(nodeId || ""),
      operator: String(operator || ""),
      status: normalizedStatus,
      cacheSource: String(cacheSource || ""),
      durationS: Number(durationS || 0),
    };
    resolutionActivityRows = [nextRow, ...resolutionActivityRows].slice(0, 180);
    resolutionActivitySummary = `events ${resolutionActivityRows.length} | computed ${Object.values(symbolStatuses).filter((s) => s === "computed").length}/${Object.keys(symbolTable || {}).length || 0}`;
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
    if (pendingPoll) {
      clearInterval(pendingPoll);
      pendingPoll = null;
    }
    pendingPollTicks = 0;
  };

  const isTimeoutError = (error) => /timed out/i.test(String(error?.message || error || ""));

  const ensurePendingPoll = ({ traceId = 0, variable = "", path = "" } = {}) => {
    if (pendingPoll) return;
    traceResolve("poll-start", {
      traceId,
      variable,
      path: path || "/",
    });
    pendingPollTicks = 0;
    pendingPoll = setInterval(() => {
      if (resolveInFlight) return;
      pendingPollTicks += 1;
      if (pendingPollTicks > MAX_PENDING_POLL_TICKS) {
        traceResolve("poll-timeout", {
          traceId,
          variable,
          path: path || "/",
          ticks: pendingPollTicks,
        });
        stopPoll();
        statusValue = "idle";
        statusText = `${variable} is still computing in the background. Click Run to refresh status.`;
        errorText = "";
        dissolveDream();
        return;
      }
      traceResolve("poll-tick", {
        traceId,
        variable,
        path: path || "/",
        ticks: pendingPollTicks,
      });
      void resolvePrimaryValue({ enqueue: false, path: currentPath, background: true });
    }, 1000);
  };

  const stopProbe = () => {
    if (!pendingProbe) return;
    clearTimeout(pendingProbe);
    pendingProbe = null;
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

  const probeOneSymbolStatus = async (symbolName, token) => {
    if (!symbolName || token !== probeToken) return;
    try {
      const payload = await resolvePlaygroundValue({
        program: programText,
        variable: symbolName,
        path: "",
        enqueue: false,
      });
      if (token !== probeToken) return;
      const materialization = normalizeStatus(payload?.materialization || "");
      const computeStatus = normalizeStatus(payload?.compute_status || "");
      if (materialization === "computed") {
        setSymbolStatus(symbolName, "computed");
        setSymbolMaterialization(symbolName, payload?.materialization || "cached");
        if (payload?.descriptor?.vox_type) {
          symbolTypeHints = {
            ...symbolTypeHints,
            [symbolName]: String(payload.descriptor.vox_type),
          };
        }
        pushResolutionActivity({
          variableName: symbolName,
          nodeId: String(payload?.node_id || ""),
          operator: "probe",
          status: "computed",
          cacheSource: "store",
          durationS: 0,
        });
        return;
      }
      if (computeStatus === "failed" || materialization === "failed") {
        setSymbolStatus(symbolName, "failed");
        setSymbolMaterialization(symbolName, "failed");
        return;
      }
      if (["queued", "running", "persisting"].includes(computeStatus)) {
        setSymbolStatus(symbolName, computeStatus);
        setSymbolMaterialization(symbolName, payload?.materialization || computeStatus);
        return;
      }
      setSymbolStatus(symbolName, "idle");
      setSymbolMaterialization(symbolName, "unresolved");
    } catch {
      // Ignore per-symbol probe failures; interactive click still drives execution.
    }
  };

  const scheduleSymbolProbe = () => {
    stopProbe();
    if (statusValue === "running" || pendingPoll) return;
    const names = Object.keys(symbolTable || {});
    if (!names.length || symbolDiagnostics.length) return;
    const token = probeToken + 1;
    probeToken = token;
    pendingProbe = setTimeout(() => {
      pendingProbe = null;
      if (token !== probeToken || statusValue === "running" || pendingPoll) return;
      const safeNames = Object.keys(symbolTable || {});
      const batchSize = 4;
      const run = async () => {
        for (let idx = 0; idx < safeNames.length; idx += batchSize) {
          if (token !== probeToken || statusValue === "running" || pendingPoll) return;
          const batch = safeNames.slice(idx, idx + batchSize);
          await Promise.all(batch.map((name) => probeOneSymbolStatus(name, token)));
        }
      };
      void run();
    }, 120);
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

  const recordDescriptor = (record) =>
    record?.descriptor && typeof record.descriptor === "object" ? record.descriptor : { vox_type: "unavailable", summary: {} };

  const recordType = (record) => String(recordDescriptor(record)?.vox_type || "unavailable").toLowerCase();

  const collectionRecord = (record) => ["sequence", "mapping"].includes(recordType(record));

  const recordLabel = (record, index = 0) => {
    const nodeId = String(record?.node_id || "");
    const names = symbolNamesByNodeId()[nodeId] || [];
    const selected = names.find((name) => selectedVisualSymbols.includes(name));
    return selected || names[0] || (selectedVisualSymbols[index] ? selectedVisualSymbols[index] : `value ${index + 1}`);
  };

  const recordPath = (record) => String(record?.path || "");

  const pageKey = (record, path = "") => `${String(record?.node_id || "")}:${String(path || recordPath(record) || "/")}`;

  const pageCacheKey = (record, path = "", offset = 0, limit = COLLECTION_PAGE_SIZE) =>
    `${pageKey(record, path)}@${Math.max(0, Number(offset || 0))}:${Math.max(1, Number(limit || COLLECTION_PAGE_SIZE))}`;

  const pageForRecord = (record, path = "") => {
    const baseKey = pageKey(record, path);
    const pointer = recordPagePointers?.[baseKey];
    if (pointer && recordPages?.[pointer]) {
      return recordPages[pointer];
    }
    const prefix = `${baseKey}@`;
    const fallbackCacheKey = Object.keys(recordPages || {}).find((cacheKey) => cacheKey.startsWith(prefix));
    if (!fallbackCacheKey) return null;
    return recordPages[fallbackCacheKey] || null;
  };

  const pageErrorForRecord = (record, path = "") => recordPagesErrors?.[pageKey(record, path)] || "";

  const pageLoadingForRecord = (record, path = "") => {
    const baseKey = pageKey(record, path);
    const pointer = recordPagePointers?.[baseKey];
    if (pointer && recordPagesLoading?.[pointer]) return true;
    const prefix = `${baseKey}@`;
    return Object.keys(recordPagesLoading || {}).some((cacheKey) => cacheKey.startsWith(prefix));
  };

  const pagePollingForRecord = (record, path = "") => Boolean(recordPagePollTimers?.[pageKey(record, path)]);

  const collectionSelectionFor = (record, path = "") => {
    const key = pageKey(record, path);
    const selection = collectionSelections?.[key];
    if (selection && typeof selection === "object") {
      return {
        selectedIndex: Math.max(0, Number(selection.selectedIndex || 0)),
        selectedAbsoluteIndex: Math.max(0, Number(selection.selectedAbsoluteIndex || 0)),
        selectedPath: String(selection.selectedPath || ""),
      };
    }
    return { selectedIndex: 0, selectedAbsoluteIndex: 0, selectedPath: "" };
  };

  const setCollectionSelection = (record, path = "", selection = {}) => {
    const key = pageKey(record, path);
    const nextIndex = Math.max(0, Number(selection?.selectedIndex || 0));
    const nextAbsoluteIndex = Math.max(0, Number(selection?.selectedAbsoluteIndex || selection?.selectedIndex || 0));
    const nextPath = String(selection?.selectedPath || "");
    const current = collectionSelections?.[key];
    if (
      current &&
      Number(current.selectedIndex || 0) === nextIndex &&
      Number(current.selectedAbsoluteIndex || 0) === nextAbsoluteIndex &&
      String(current.selectedPath || "") === nextPath
    ) {
      return;
    }
    collectionSelections = {
      ...collectionSelections,
      [key]: {
        selectedIndex: nextIndex,
        selectedAbsoluteIndex: nextAbsoluteIndex,
        selectedPath: nextPath,
      },
    };
  };

  const collectionItemsForPage = (page, voxType = "sequence") => {
    const items = Array.isArray(page?.items) ? page.items : [];
    if (String(voxType || "").toLowerCase() === "mapping") {
      return [...items].sort((left, right) => String(left?.label || "").localeCompare(String(right?.label || "")));
    }
    return items;
  };

  const nestedRecordFromItem = (parentRecord, item) => ({
    ...parentRecord,
    node_id: String(item?.node_id || parentRecord?.node_id || ""),
    path: String(item?.path || parentRecord?.path || ""),
    status: String(item?.status || parentRecord?.status || ""),
    descriptor:
      item?.descriptor && typeof item.descriptor === "object"
        ? item.descriptor
        : {
            vox_type: "unavailable",
            summary: {
              reason: "Value preview unavailable.",
            },
            navigation: {
              path: String(item?.path || parentRecord?.path || ""),
              pageable: false,
              can_descend: false,
              default_page_size: COLLECTION_PAGE_SIZE,
              max_page_size: 256,
            },
          },
  });

  const sourceVariableForRecord = (record, index = 0) => {
    const nodeId = String(record?.node_id || "");
    if (nodeId) {
      const names = symbolNamesByNodeId()[nodeId] || [];
      const selected = names.find((name) => selectedVisualSymbols.includes(name));
      if (selected) return selected;
      if (names[0]) return names[0];
    }
    if (selectedVisualSymbols[index]) return selectedVisualSymbols[index];
    return primaryVariable || "";
  };

  const pathRecordKey = (sourceVariable = "", path = "") => `${String(sourceVariable || "")}:${String(path || "/")}`;

  const pathRecordFor = (sourceVariable = "", path = "") => pathRecords?.[pathRecordKey(sourceVariable, path)] || null;

  const pathRecordLoadingFor = (sourceVariable = "", path = "") => Boolean(pathRecordsLoading?.[pathRecordKey(sourceVariable, path)]);

  const pathRecordErrorFor = (sourceVariable = "", path = "") => String(pathRecordsErrors?.[pathRecordKey(sourceVariable, path)] || "");

  const pathRecordPollingFor = (sourceVariable = "", path = "") => Boolean(pathRecordPollTimers?.[pathRecordKey(sourceVariable, path)]);

  const cachePathRecord = (sourceVariable = "", path = "", payload = null) => {
    if (!sourceVariable || !payload) return;
    const key = pathRecordKey(sourceVariable, path);
    pathRecords = {
      ...pathRecords,
      [key]: payload,
    };
  };

  const clearPathRecordPoll = (key = "") => {
    const pollKey = String(key || "");
    const timer = pathRecordPollTimers?.[pollKey];
    if (timer) {
      clearTimeout(timer);
    }
    const next = { ...pathRecordPollTimers };
    delete next[pollKey];
    pathRecordPollTimers = next;
  };

  const schedulePathRecordPoll = ({ sourceVariable = "", path = "", delayMs = 850 } = {}) => {
    const variableName = String(sourceVariable || "").trim();
    const targetPath = String(path || "");
    if (!variableName) return;
    const key = pathRecordKey(variableName, targetPath);
    if (pathRecordPollTimers?.[key]) return;
    const timer = setTimeout(() => {
      clearPathRecordPoll(key);
      void loadPathRecord({
        sourceVariable: variableName,
        path: targetPath,
        enqueueFallback: false,
        force: true,
      });
    }, Math.max(250, Number(delayMs || 850)));
    pathRecordPollTimers = {
      ...pathRecordPollTimers,
      [key]: timer,
    };
  };

  const loadPathRecord = async ({ sourceVariable = "", path = "", enqueueFallback = true, force = false } = {}) => {
    const variableName = String(sourceVariable || "").trim();
    const targetPath = String(path || "");
    if (!variableName) return null;
    const key = pathRecordKey(variableName, targetPath);
    if (!force && pathRecords?.[key]) return pathRecords[key];
    if (pathRecordsLoading?.[key]) return null;
    clearPathRecordPoll(key);

    pathRecordsLoading = { ...pathRecordsLoading, [key]: true };
    const nextErrors = { ...pathRecordsErrors };
    delete nextErrors[key];
    pathRecordsErrors = nextErrors;

    const tryResolve = async (enqueueFlag) =>
      resolvePlaygroundValue({
        program: programText,
        variable: variableName,
        path: targetPath,
        enqueue: enqueueFlag,
      });

    try {
      const first = await tryResolve(false);
      const firstMaterialization = String(first?.materialization || "").toLowerCase();
      const firstStatus = String(first?.compute_status || "").toLowerCase();
      const firstMaterialized = (firstMaterialization === "computed" || firstMaterialization === "cached") && Boolean(first?.descriptor);
      const firstPending = ["pending", "missing", "queued", "running", "persisting"].includes(firstMaterialization) ||
        ["queued", "running", "persisting"].includes(firstStatus);
      if (firstMaterialized) {
        cachePathRecord(variableName, targetPath, first);
        clearPathRecordPoll(key);
        return first;
      }

      if (enqueueFallback && ["pending", "missing"].includes(firstMaterialization) && !["failed", "killed"].includes(firstStatus)) {
        const second = await tryResolve(true);
        const secondMaterialization = String(second?.materialization || "").toLowerCase();
        const secondStatus = String(second?.compute_status || "").toLowerCase();
        const secondMaterialized = (secondMaterialization === "computed" || secondMaterialization === "cached") && Boolean(second?.descriptor);
        if (secondMaterialized) {
          cachePathRecord(variableName, targetPath, second);
          clearPathRecordPoll(key);
          return second;
        }
        const secondPending = ["pending", "missing", "queued", "running", "persisting"].includes(secondMaterialization) ||
          ["queued", "running", "persisting"].includes(secondStatus);
        if (secondPending) {
          schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
          return null;
        }
      }

      if (firstPending) {
        schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 900 });
        return null;
      }

      if (first?.descriptor && !["pending", "missing"].includes(firstMaterialization)) {
        cachePathRecord(variableName, targetPath, first);
        clearPathRecordPoll(key);
        return first;
      }
      return null;
    } catch (error) {
      if (isTimeoutError(error)) {
        schedulePathRecordPoll({ sourceVariable: variableName, path: targetPath, delayMs: 1100 });
        return pathRecords?.[key] || null;
      }
      pathRecordsErrors = {
        ...pathRecordsErrors,
        [key]: String(error?.message || error || "Unable to load value."),
      };
      return null;
    } finally {
      const nextLoading = { ...pathRecordsLoading };
      delete nextLoading[key];
      pathRecordsLoading = nextLoading;
    }
  };

  const resolveViewerRecordsTypes = () => {
    const next = { ...symbolTypeHints };
    for (const record of viewerRecords) {
      const nodeId = String(record?.node_id || "");
      const type = String(record?.descriptor?.vox_type || "").trim();
      if (!nodeId || !type) continue;
      const names = symbolNamesByNodeId()[nodeId] || [];
      for (const name of names) {
        next[name] = type;
      }
    }
    symbolTypeHints = next;
  };

  const clearRecordPagePoll = (baseKey = "") => {
    const key = String(baseKey || "");
    const timer = recordPagePollTimers?.[key];
    if (timer) {
      clearTimeout(timer);
    }
    const next = { ...recordPagePollTimers };
    delete next[key];
    recordPagePollTimers = next;
  };

  const scheduleRecordPagePoll = (record, { path = "", offset = 0, limit = COLLECTION_PAGE_SIZE, delayMs = 900 } = {}) => {
    const resolvedPath = String(path || recordPath(record) || "");
    const baseKey = pageKey(record, resolvedPath);
    if (recordPagePollTimers?.[baseKey]) return;
    const timer = setTimeout(() => {
      clearRecordPagePoll(baseKey);
      void loadRecordPage(record, {
        path: resolvedPath,
        offset,
        limit,
        enqueueFallback: false,
        force: true,
      });
    }, Math.max(250, Number(delayMs || 900)));
    recordPagePollTimers = {
      ...recordPagePollTimers,
      [baseKey]: timer,
    };
  };

  const loadRecordPage = async (
    record,
    { path = "", offset = 0, limit = COLLECTION_PAGE_SIZE, enqueueFallback = true, force = false } = {},
  ) => {
    if (!record || !collectionRecord(record)) return null;
    const descriptor = recordDescriptor(record);
    const navigation = descriptor?.navigation && typeof descriptor.navigation === "object" ? descriptor.navigation : {};
    if (!navigation.pageable) return null;
    const resolvedPath = String(path || navigation.path || record.path || "");
    const baseKey = pageKey(record, resolvedPath);
    const resolvedLimit = Math.max(1, Number(limit || navigation.default_page_size || COLLECTION_PAGE_SIZE));
    const resolvedOffset = Math.max(0, Number(offset || 0));
    const cacheKey = pageCacheKey(record, resolvedPath, resolvedOffset, resolvedLimit);
    if (!force && recordPages?.[cacheKey]) {
      recordPagePointers = {
        ...recordPagePointers,
        [baseKey]: cacheKey,
      };
      return recordPages[cacheKey];
    }
    if (recordPagesLoading?.[cacheKey]) return null;
    recordPagesLoading = { ...recordPagesLoading, [cacheKey]: true };
    const nextErrors = { ...recordPagesErrors };
    delete nextErrors[baseKey];
    recordPagesErrors = nextErrors;
    try {
      const requestPage = async (enqueueFlag) =>
        resolvePlaygroundValuePage({
          program: programText,
          nodeId: String(record?.node_id || ""),
          variable: "",
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          enqueue: enqueueFlag,
        });

      let payload = await requestPage(false);
      let page =
        payload?.page && typeof payload.page === "object"
          ? payload.page
          : { offset: resolvedOffset, limit: resolvedLimit, items: [], has_more: false, next_offset: null };
      const pendingStatuses = new Set(["queued", "running", "persisting", "pending", "missing"]);
      const payloadMaterialization = String(payload?.materialization || "").toLowerCase();
      const payloadStatus = String(payload?.compute_status || "").toLowerCase();
      const expectedLength = Number(descriptor?.summary?.length || 0);
      const likelyPending = pendingStatuses.has(payloadMaterialization) || pendingStatuses.has(payloadStatus);
      const needsFallback = !Array.isArray(page?.items) || page.items.length === 0;

      if (enqueueFallback && needsFallback && (likelyPending || expectedLength > 0)) {
        payload = await requestPage(true);
        page =
          payload?.page && typeof payload.page === "object"
            ? payload.page
            : { offset: resolvedOffset, limit: resolvedLimit, items: [], has_more: false, next_offset: null };
      }

      const pageMaterialization = String(payload?.materialization || "").toLowerCase();
      const pageStatus = String(payload?.compute_status || "").toLowerCase();
      const pagePending = pendingStatuses.has(pageMaterialization) || pendingStatuses.has(pageStatus);
      if (pagePending && (!Array.isArray(page?.items) || page.items.length === 0)) {
        scheduleRecordPagePoll(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          delayMs: 950,
        });
      } else {
        clearRecordPagePoll(baseKey);
      }

      const previousPage = recordPages?.[cacheKey];
      const incomingItems = Array.isArray(page?.items) ? page.items : [];
      const keepPrevious = Boolean(previousPage) && pagePending && incomingItems.length === 0;
      const effectivePage = keepPrevious ? previousPage : page;
      recordPages = { ...recordPages, [cacheKey]: effectivePage };
      recordPagePointers = { ...recordPagePointers, [baseKey]: cacheKey };
      const items = collectionItemsForPage(effectivePage, recordType(record));
      const currentSelection = collectionSelectionFor(record, resolvedPath);
      let selectedIndex = Math.max(0, Number(currentSelection?.selectedIndex || 0));
      let selectedAbsoluteIndex = Math.max(0, Number(currentSelection?.selectedAbsoluteIndex || 0));
      let selectedPath = String(currentSelection?.selectedPath || "");
      if (!items.length) {
        selectedIndex = 0;
        selectedAbsoluteIndex = 0;
        selectedPath = "";
      } else {
        const byAbsolute = selectedAbsoluteIndex >= resolvedOffset ? selectedAbsoluteIndex - resolvedOffset : -1;
        if (byAbsolute >= 0 && byAbsolute < items.length) {
          selectedIndex = byAbsolute;
          selectedAbsoluteIndex = resolvedOffset + byAbsolute;
        } else {
          const byPathIndex = selectedPath ? items.findIndex((item) => String(item?.path || "") === selectedPath) : -1;
          if (byPathIndex >= 0) {
            selectedIndex = byPathIndex;
            selectedAbsoluteIndex = resolvedOffset + byPathIndex;
          } else if (selectedIndex >= items.length) {
            selectedIndex = 0;
            selectedAbsoluteIndex = resolvedOffset;
          } else {
            selectedAbsoluteIndex = resolvedOffset + selectedIndex;
          }
        }
        if (selectedIndex >= items.length) {
          const byAbsolute = selectedAbsoluteIndex >= resolvedOffset ? selectedAbsoluteIndex - resolvedOffset : -1;
          selectedIndex = byAbsolute >= 0 && byAbsolute < items.length ? byAbsolute : 0;
        }
        selectedPath = String(items[selectedIndex]?.path || "");
      }
      setCollectionSelection(record, resolvedPath, { selectedIndex, selectedAbsoluteIndex, selectedPath });
      return effectivePage;
    } catch (error) {
      if (isTimeoutError(error)) {
        scheduleRecordPagePoll(record, {
          path: resolvedPath,
          offset: resolvedOffset,
          limit: resolvedLimit,
          delayMs: 1100,
        });
        const cached = recordPages?.[cacheKey] || null;
        if (cached) {
          recordPagePointers = { ...recordPagePointers, [baseKey]: cacheKey };
        }
        return cached;
      }
      recordPagesErrors = {
        ...recordPagesErrors,
        [baseKey]: String(error?.message || error || "Unable to load collection values."),
      };
      return null;
    } finally {
      const nextLoading = { ...recordPagesLoading };
      delete nextLoading[cacheKey];
      recordPagesLoading = nextLoading;
    }
  };

  const loadCollectionPrev = async (record, path = "") => {
    const page = pageForRecord(record, path);
    if (!page) return null;
    const offset = Math.max(0, Number(page.offset || 0));
    const limit = Math.max(1, Number(page.limit || COLLECTION_PAGE_SIZE));
    if (offset <= 0) return page;
    return loadRecordPage(record, {
      path,
      offset: Math.max(0, offset - limit),
      limit,
    });
  };

  const loadCollectionNext = async (record, path = "") => {
    const page = pageForRecord(record, path);
    if (!page || page.next_offset === null || page.next_offset === undefined) return page;
    const limit = Math.max(1, Number(page.limit || COLLECTION_PAGE_SIZE));
    return loadRecordPage(record, {
      path,
      offset: Math.max(0, Number(page.next_offset || 0)),
      limit,
    });
  };

  const ensureRecordPages = () => {
    for (const record of viewerRecords) {
      if (!collectionRecord(record)) continue;
      const path = recordPath(record);
      if (pageForRecord(record, path) || pageLoadingForRecord(record, path)) continue;
      void loadRecordPage(record, { path, offset: 0, limit: COLLECTION_PAGE_SIZE });
    }
  };

  const previewText = (descriptor) => {
    const voxType = String(descriptor?.vox_type || "value").toLowerCase();
    const summary = descriptor?.summary && typeof descriptor.summary === "object" ? descriptor.summary : {};
    if (["integer", "number", "boolean", "null"].includes(voxType)) return `${summary.value ?? ""}`;
    if (voxType === "string") return String(summary.value || "");
    if (voxType === "bytes") return `${Number(summary.length || 0)} bytes`;
    if (voxType === "mapping" || voxType === "sequence") return `${Number(summary.length || 0)} values`;
    if (voxType === "ndarray") return Array.isArray(summary.shape) ? summary.shape.join(" x ") : "array";
    if (voxType === "image2d" || voxType === "volume3d") return Array.isArray(summary.size) ? summary.size.join(" x ") : "image";
    if (summary && typeof summary.reason === "string") return summary.reason;
    return TYPE_LABELS[voxType] || voxType || "value";
  };

  const collectDreamIds = (payload = {}) => {
    const ids = [];
    const seen = new Set();
    const add = (value) => {
      const text = String(value || "").trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      ids.push(text);
    };
    add(symbolTable?.[primaryVariable]);
    add(payload?.node_id);
    add(payload?.job_id);
    const arrays = [payload?.ancestor_ids, payload?.ancestor_node_ids, payload?.pending_node_ids, payload?.node_ids];
    for (const list of arrays) {
      if (!Array.isArray(list)) continue;
      for (const item of list) add(item);
    }
    const rows = Array.isArray(pendingLogRows) ? pendingLogRows : [];
    for (const row of rows) add(row?.node_id);
    const logTail = String(payload?.log_tail || "");
    for (const match of logTail.matchAll(/"node_id"\s*:\s*"([^"]+)"/g)) {
      add(match?.[1]);
    }
    return ids.slice(0, 18);
  };

  const activateDream = (payload = {}) => {
    if (pendingDreamCleanup) {
      clearTimeout(pendingDreamCleanup);
      pendingDreamCleanup = null;
    }
    dreamVisible = true;
    dreamDissolving = false;
    const ids = collectDreamIds(payload);
    dreamNodeIds = ids.length ? ids : [String(symbolTable?.[primaryVariable] || primaryVariable || "node")];
  };

  const dissolveDream = () => {
    if (!dreamVisible) return;
    dreamDissolving = true;
    if (pendingDreamCleanup) clearTimeout(pendingDreamCleanup);
    pendingDreamCleanup = setTimeout(() => {
      dreamVisible = false;
      dreamDissolving = false;
      dreamNodeIds = [];
      pendingDreamCleanup = null;
    }, 1200);
  };

  const ensureViewer = () => {
    if (viewer) return;
    viewer = {
      setLoading: (message) => {
        viewerMode = "loading";
        viewerMessage = String(message || "Loading...");
        viewerErrorMessage = "";
      },
      setError: (message) => {
        viewerMode = "error";
        viewerErrorMessage = String(message || "Viewer error");
        viewerMessage = "";
      },
      renderRecord: (record) => {
        viewerRecords = record ? [record] : [];
        viewerMode = record ? "value" : "empty";
        viewerErrorMessage = "";
        viewerMessage = "";
        resolveViewerRecordsTypes();
        ensureRecordPages();
      },
      renderRecords: (records) => {
        viewerRecords = (Array.isArray(records) ? records : []).filter((item) => !!item);
        viewerMode = viewerRecords.length ? "value" : "empty";
        viewerErrorMessage = "";
        viewerMessage = "";
        resolveViewerRecordsTypes();
        ensureRecordPages();
      },
      destroy: () => {},
    };
    viewerSupportsMultiValue = true;
  };

  const resetViewer = () => {
    for (const timer of Object.values(recordPagePollTimers || {})) {
      clearTimeout(timer);
    }
    for (const timer of Object.values(pathRecordPollTimers || {})) {
      clearTimeout(timer);
    }
    viewerRecords = [];
    viewerMode = "empty";
    viewerMessage = "";
    viewerErrorMessage = "";
    recordPages = {};
    recordPagePointers = {};
    recordPagesLoading = {};
    recordPagesErrors = {};
    collectionSelections = {};
    recordPagePollTimers = {};
    pathRecords = {};
    pathRecordsLoading = {};
    pathRecordsErrors = {};
    pathRecordPollTimers = {};
    maximizedViewerIndex = -1;
  };

  const renderSelectedRecords = ({ fallbackRecord = null } = {}) => {
    ensureViewer();
    const selectedRecords = selectedVisualSymbols.map((name) => materializedRecords?.[name]).filter((row) => !!row);
    if (selectedRecords.length > 1 && viewerSupportsMultiValue && typeof viewer?.renderRecords === "function") {
      viewer.renderRecords(selectedRecords);
      return true;
    }
    const fallback =
      materializedRecords?.[primaryVariable] ||
      selectedRecords[selectedRecords.length - 1] ||
      fallbackRecord ||
      null;
    if (fallback && typeof viewer?.renderRecord === "function") {
      viewer.renderRecord(fallback);
      return true;
    }
    return false;
  };

  const toggleMaximizedViewer = (index = -1) => {
    const next = Number(index);
    if (!Number.isInteger(next) || next < 0) {
      maximizedViewerIndex = -1;
      return;
    }
    maximizedViewerIndex = maximizedViewerIndex === next ? -1 : next;
  };

  const resolveContextMatches = (request = {}) =>
    String(request?.variable || "") === String(primaryVariable || "") &&
    String(request?.path || "") === String(currentPath || "") &&
    String(request?.program || "") === String(programText || "");

  const applyFailure = (payload, variableName) => {
    const message = String(payload?.error || "Unable to inspect value.");
    statusValue = "failed";
    statusText = message;
    captionVariable = variableName || "-";
    errorText = message;
    dissolveDream();
    setSymbolMaterialization(variableName, "failed");
    if (materializedRecords?.[variableName]) {
      const next = { ...materializedRecords };
      delete next[variableName];
      materializedRecords = next;
    }
    const failureRecord = {
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
    };
    const renderedFromSelection = renderSelectedRecords({
      fallbackRecord: selectedVisualSymbols.includes(variableName) ? failureRecord : null,
    });
    if (!renderedFromSelection) {
      viewer.renderRecord(failureRecord);
    }
  };

  const clearPendingLogs = () => {
    pendingLogSummary = "No execution log yet.";
    pendingLogRows = [];
    pendingLogRaw = "";
    pendingLogJobId = "";
  };

  const applyPendingLogs = (payload, state) => {
    const cacheSummary =
      payload?.cache_summary && typeof payload.cache_summary === "object"
        ? payload.cache_summary
        : payload?.diagnostics?.cache_summary && typeof payload.diagnostics.cache_summary === "object"
          ? payload.diagnostics.cache_summary
          : {};
    const { raw, summaryText, rows } = buildExecutionLogRows({
      log_tail: payload?.log_tail || "",
      result: { execution: { cache_summary: cacheSummary } },
    });
    if (raw) {
      pendingLogSummary = summaryText;
    } else if (state === "queued") {
      pendingLogSummary = "Queued: waiting for a value worker slot.";
    } else if (state === "persisting") {
      pendingLogSummary = "Computed: waiting for persistence to finish.";
    } else {
      pendingLogSummary = "Execution started. Waiting for log events...";
    }
    pendingLogRows = rows;
    pendingLogRaw = raw;
    pendingLogJobId = payload?.job_id ? String(payload.job_id) : "";

    const namesByNode = symbolNamesByNodeId();
    for (const entry of [...rows].reverse()) {
      if (!entry || entry.event !== "playground.node") continue;
      const eventNodeId = String(entry.node_id || "");
      const mappedNames = namesByNode[eventNodeId] || [];
      const eventStatus = normalizeStatus(entry.status || state);
      if (mappedNames.length) {
        for (const name of mappedNames) {
          setSymbolStatus(name, eventStatus);
          pushResolutionActivity({
            variableName: name,
            nodeId: eventNodeId,
            operator: String(entry.operator || ""),
            status: eventStatus,
            cacheSource: String(entry.cache_source || ""),
            durationS: Number(entry.duration_s || 0),
          });
        }
      } else {
        pushResolutionActivity({
          variableName: "",
          nodeId: eventNodeId,
          operator: String(entry.operator || ""),
          status: eventStatus,
          cacheSource: String(entry.cache_source || ""),
          durationS: Number(entry.duration_s || 0),
        });
      }
    }
  };

  const applyPending = (payload, variableName) => {
    const state = String(payload?.compute_status || payload?.materialization || "running");
    traceResolve("pending", {
      variable: variableName,
      state,
      materialization: String(payload?.materialization || ""),
      computeStatus: String(payload?.compute_status || ""),
      jobId: String(payload?.job_id || ""),
      path: String(payload?.path || currentPath || "/"),
    });
    statusValue = "running";
    statusText = `Computing ${variableName} (${state})`;
    captionVariable = variableName || "-";
    errorText = "";
    activateDream(payload);
    setSymbolStatus(variableName, state);
    setSymbolMaterialization(variableName, payload?.materialization || state);
    applyPendingLogs(payload, state);
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
    traceResolve("materialized", {
      variable: variableName,
      materialization,
      computeStatus: String(payload?.compute_status || ""),
      nodeId: String(payload?.node_id || ""),
      path: String(payload?.path || currentPath || "/"),
      voxType: String(descriptor?.vox_type || ""),
    });
    statusValue = "completed";
    statusText = `Computed ${variableName}`;
    captionVariable = variableName || "-";
    errorText = "";
    dissolveDream();
    setSymbolStatus(variableName, "computed");
    setSymbolMaterialization(variableName, materialization);
    if (descriptor?.vox_type) {
      symbolTypeHints = {
        ...symbolTypeHints,
        [variableName]: String(descriptor.vox_type),
      };
    }
    materializedRecords = {
      ...materializedRecords,
      [variableName]: payload,
    };
    if (!selectedVisualSymbols.includes(variableName)) {
      selectedVisualSymbols = [...selectedVisualSymbols, variableName];
    }
    ensureSelectedVisualSymbols();
    renderSelectedRecords({ fallbackRecord: payload });
    pushResolutionActivity({
      variableName,
      nodeId: String(payload?.node_id || ""),
      operator: "result",
      status: "computed",
      cacheSource: materialization === "cached" ? "store" : "runtime",
      durationS: 0,
    });
    clearPendingLogs();
  };

  const resolvePrimaryValue = async ({ enqueue = true, path = "", background = false } = {}) => {
    ensureViewer();
    const traceId = resolveTraceSeq + 1;
    resolveTraceSeq = traceId;
    if (!primaryVariable) {
      traceResolve("skip-no-primary", { traceId, enqueue, path });
      statusValue = "idle";
      statusText = "Define at least one variable to inspect.";
      captionVariable = "-";
      dissolveDream();
      viewer.renderRecord(null);
      return { state: "idle", reason: "no-primary" };
    }
    if (symbolDiagnostics.length) {
      traceResolve("skip-diagnostics", {
        traceId,
        enqueue,
        path,
        variable: primaryVariable,
        diagnostics: symbolDiagnostics.length,
      });
      statusValue = "failed";
      statusText = "Fix static diagnostics before computing.";
      captionVariable = primaryVariable;
      dissolveDream();
      viewer.setError(statusText);
      errorText = statusText;
      setSymbolStatus(primaryVariable, "failed");
      return { state: "failed", reason: "diagnostics" };
    }

    if (enqueue) {
      // User-driven resolve takes priority over passive symbol probes.
      probeToken += 1;
      stopProbe();
    }

    currentPath = String(path || "");
    const request = {
      seq: resolveRequestSeq + 1,
      variable: String(primaryVariable || ""),
      path: String(currentPath || ""),
      program: String(programText || ""),
    };
    resolveRequestSeq = request.seq;
    resolveInFlight = true;
    traceResolve("start", {
      traceId,
      enqueue,
      background,
      variable: request.variable,
      path: currentPath || "/",
      statusBefore: statusValue,
    });
    statusValue = "running";
    statusText = `Computing ${primaryVariable}...`;
    captionVariable = primaryVariable;
    errorText = "";
    setSymbolStatus(primaryVariable, "running");
    const preserveCurrentView = Boolean(background && viewerMode === "value" && viewerRecords.length);
    if (!preserveCurrentView) {
      viewer.setLoading(`Computing ${primaryVariable}${currentPath ? ` @ ${currentPath}` : ""}...`);
    }

    try {
      const requestStarted = performance?.now ? performance.now() : Date.now();
      traceResolve("request-dispatch", {
        traceId,
        enqueue,
        background,
        variable: request.variable,
        path: currentPath || "/",
      });
      const payload = await resolvePlaygroundValue({
        program: request.program,
        variable: request.variable,
        path: currentPath,
        enqueue,
      });
      if (request.seq !== resolveRequestSeq || !resolveContextMatches(request)) {
        traceResolve("response-stale", {
          traceId,
          enqueue,
          background,
          variable: request.variable,
          path: request.path || "/",
        });
        return { state: "stale", reason: "superseded" };
      }
      const requestElapsedMs = (performance?.now ? performance.now() : Date.now()) - requestStarted;
      traceResolve("response", {
        traceId,
        enqueue,
        background,
        variable: request.variable,
        path: currentPath || "/",
        elapsedMs: Number(requestElapsedMs.toFixed(1)),
        materialization: String(payload?.materialization || ""),
        computeStatus: String(payload?.compute_status || ""),
        nodeId: String(payload?.node_id || ""),
        jobId: String(payload?.job_id || ""),
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
        traceResolve("branch-materialized", {
          traceId,
          variable: request.variable,
          path: currentPath || "/",
          materialization,
          computeStatus,
        });
        stopPoll();
        applyMaterialized(payload, request.variable);
        return { state: "computed", reason: "materialized" };
      }

      if (materialization === "failed" || computeStatus === "failed" || computeStatus === "killed") {
        traceResolve("branch-failed", {
          traceId,
          variable: request.variable,
          path: currentPath || "/",
          materialization,
          computeStatus,
          error: String(payload?.error || ""),
        });
        stopPoll();
        setSymbolStatus(request.variable, "failed");
        applyFailure(payload, request.variable);
        return { state: "failed", reason: "materialization-failed" };
      }

      if (isPending) {
        const hasProgressSignal =
          materialization === "pending" ||
          ["queued", "running", "persisting"].includes(computeStatus) ||
          Boolean(payload?.request_enqueued) ||
          Boolean(payload?.job_id);
        traceResolve("branch-pending", {
          traceId,
          variable: request.variable,
          path: currentPath || "/",
          materialization,
          computeStatus,
          hasProgressSignal,
          pollActive: Boolean(pendingPoll),
        });
        applyPending(payload, request.variable);
        if (!hasProgressSignal) {
          stopPoll();
          statusValue = "idle";
          statusText = `${request.variable} is not ready yet. Click Run or click the tag again to refresh.`;
          setSymbolStatus(request.variable, "idle");
          setSymbolMaterialization(request.variable, materialization || "unresolved");
          dissolveDream();
          return { state: "idle", reason: "no-progress" };
        }
        ensurePendingPoll({ traceId, variable: request.variable, path: currentPath || "/" });
        return { state: "pending", reason: "in-progress" };
      }

      traceResolve("branch-unexpected", {
        traceId,
        variable: request.variable,
        path: currentPath || "/",
        materialization,
        computeStatus,
      });
      stopPoll();
      setSymbolStatus(request.variable, "failed");
      applyFailure(
        {
          ...payload,
          error: payload?.error || "Unexpected materialization state.",
        },
        request.variable,
      );
      return { state: "failed", reason: "unexpected-state" };
    } catch (error) {
      if (request.seq !== resolveRequestSeq || !resolveContextMatches(request)) {
        traceResolve("request-error-stale", {
          traceId,
          enqueue,
          variable: request.variable,
          path: request.path || "/",
          message: String(error?.message || error || "unknown"),
        });
        return { state: "stale", reason: "superseded" };
      }
      traceResolve("request-error", {
        traceId,
        enqueue,
        background,
        variable: request.variable,
        path: currentPath || "/",
        message: String(error?.message || error || "unknown"),
      });
      if (isTimeoutError(error)) {
        applyPending(
          {
            materialization: "pending",
            compute_status: "running",
            path: currentPath || "/",
            log_tail: pendingLogRaw || "",
            job_id: pendingLogJobId || "",
          },
          request.variable,
        );
        ensurePendingPoll({ traceId, variable: request.variable, path: currentPath || "/" });
        return { state: "pending", reason: "request-timeout" };
      }
      stopPoll();
      setSymbolStatus(request.variable, "failed");
      applyFailure({ error: error.message || "Request failed." }, request.variable);
      return { state: "failed", reason: "request-error" };
    } finally {
      if (request.seq === resolveRequestSeq) {
        resolveInFlight = false;
      }
    }
  };

  const refreshSymbols = async () => {
    if (capabilities.playground_symbols === false) {
      probeToken += 1;
      stopProbe();
      symbolTable = {};
      symbolDiagnostics = [];
      symbolStatuses = {};
      symbolMaterializations = {};
      materializedRecords = {};
      symbolTypeHints = {};
      selectedVisualSymbols = [];
      primaryVariable = "";
      captionVariable = "-";
      statusValue = "failed";
      statusText = "Program symbol API unavailable on this backend.";
      errorText = statusText;
      resetViewer();
      return;
    }

    const token = loadToken + 1;
    loadToken = token;

    const source = String(programText || "").trim();
    if (!source) {
      probeToken += 1;
      stopProbe();
      symbolTable = {};
      symbolDiagnostics = [];
      symbolStatuses = {};
      symbolMaterializations = {};
      materializedRecords = {};
      symbolTypeHints = {};
      selectedVisualSymbols = [];
      primaryVariable = "";
      captionVariable = "-";
      statusValue = "idle";
      statusText = "Write code and run to compute a value.";
      errorText = "";
      resetViewer();
      return;
    }

    try {
      const payload = await getProgramSymbols(programText);
      if (token !== loadToken) return;
      const available = payload?.available !== false;
      symbolTable = available ? payload.symbol_table || {} : {};
      symbolDiagnostics = payload?.diagnostics || [];
      syncSymbolStatuses(symbolTable);
      syncSymbolMaterializations(symbolTable);
      syncMaterializedRecords(symbolTable);
      syncSymbolTypeHints(symbolTable);
      primaryVariable = inferPrimaryVariable(programText, symbolTable);
      ensureSelectedVisualSymbols();
      captionVariable = primaryVariable || "-";
      renderSelectedRecords();
      if (symbolDiagnostics.length) {
        statusValue = "failed";
        statusText = "Static diagnostics detected.";
        errorText = statusText;
      } else if (primaryVariable) {
        statusValue = "idle";
        statusText = `Ready to compute ${primaryVariable}.`;
        errorText = "";
        scheduleSymbolProbe();
      }
    } catch (error) {
      if (token !== loadToken) return;
      probeToken += 1;
      stopProbe();
      symbolTable = {};
      symbolDiagnostics = [{ code: "E_SYMBOLS", message: error.message || "Unable to refresh symbols." }];
      symbolStatuses = {};
      symbolMaterializations = {};
      materializedRecords = {};
      symbolTypeHints = {};
      selectedVisualSymbols = [];
      primaryVariable = "";
      captionVariable = "-";
      statusValue = "failed";
      statusText = "Unable to refresh symbols.";
      errorText = statusText;
      resetViewer();
    }
  };

  const handleEditorChange = async (event) => {
    programText = String(event?.detail?.value ?? programText ?? "");
    schedulePersist();
    resolveRequestSeq += 1;
    resolveInFlight = false;
    probeToken += 1;
    stopProbe();
    stopPoll();
    resetViewer();
    clearResolutionActivity();
    await refreshSymbols();
  };

  const resolveCurrentPreferCache = async () => {
    const variableName = String(primaryVariable || "");
    if (!variableName) return { state: "idle", reason: "no-primary" };
    if (materializedRecords?.[variableName]) {
      renderSelectedRecords();
    }
    const cachedAttempt = await resolvePrimaryValue({ enqueue: false, path: "", background: true });
    if (cachedAttempt?.state === "computed" || cachedAttempt?.state === "pending" || cachedAttempt?.state === "failed") {
      return cachedAttempt;
    }
    if (cachedAttempt?.state === "stale") {
      return cachedAttempt;
    }
    if (materializedRecords?.[variableName]) {
      return { state: "computed", reason: "local-cache" };
    }
    return resolvePrimaryValue({ enqueue: true, path: "" });
  };

  const handleEditorSymbolClick = async (event) => {
    const token = String(event?.detail?.token || "");
    if (!token || !symbolTable[token]) return;
    traceResolve("symbol-click", {
      token,
      from: primaryVariable,
      currentStatus: statusValue,
      knownStatus: normalizeStatus(symbolStatuses?.[token] || "idle"),
    });
    primaryVariable = token;
    captionVariable = token;
    selectedVisualSymbols = [token];
    stopPoll();
    resolveRequestSeq += 1;
    const rendered = renderSelectedRecords();
    if (!rendered) viewer.renderRecord(null);
    currentPath = "";
    await resolveCurrentPreferCache();
  };

  const runPrimary = async () => {
    traceResolve("run-primary", {
      variable: primaryVariable,
      status: statusValue,
      path: currentPath || "/",
    });
    resolveRequestSeq += 1;
    resolveInFlight = false;
    stopPoll();
    await refreshSymbols();
    ensureSelectedVisualSymbols();
    await resolvePrimaryValue({ enqueue: true, path: "" });
  };

  const handleVisualTagClick = async (symbolName, event) => {
    const name = String(symbolName || "");
    if (!name || !symbolTable?.[name]) return;
    const additive = Boolean(event?.metaKey || event?.ctrlKey || event?.shiftKey);
    if (additive) {
      if (selectedVisualSymbols.includes(name)) {
        if (selectedVisualSymbols.length > 1) {
          selectedVisualSymbols = selectedVisualSymbols.filter((entry) => entry !== name);
          ensureSelectedVisualSymbols();
          renderSelectedRecords();
          return;
        }
      } else {
        selectedVisualSymbols = [...selectedVisualSymbols, name];
      }
    } else {
      selectedVisualSymbols = [name];
    }
    primaryVariable = name;
    captionVariable = name;
    currentPath = "";
    stopPoll();
    resolveRequestSeq += 1;
    ensureSelectedVisualSymbols();
    const rendered = renderSelectedRecords();
    if (!rendered) viewer.renderRecord(null);
    await resolveCurrentPreferCache();
  };

  $: editorSymbolTypes = Object.fromEntries(
    Object.keys(symbolTable || {}).map((name) => [name, symbolTypeLabel(name)]),
  );

  $: if (maximizedViewerIndex >= viewerRecords.length || viewerMode !== "value") {
    maximizedViewerIndex = -1;
  }

  $: if (viewerMode === "value" && viewerRecords.length) {
    ensureRecordPages();
  }

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

    await refreshSymbols();
  });

  onDestroy(() => {
    stopPoll();
    stopProbe();
    probeToken += 1;
    if (pendingSave) clearTimeout(pendingSave);
    if (pendingDreamCleanup) clearTimeout(pendingDreamCleanup);
    for (const timer of Object.values(recordPagePollTimers || {})) {
      clearTimeout(timer);
    }
    for (const timer of Object.values(pathRecordPollTimers || {})) {
      clearTimeout(timer);
    }
    if (viewer && typeof viewer.destroy === "function") {
      viewer.destroy();
    }
  });
</script>

<section class={`panel ${active ? "active" : ""}`} id="tab-start">
  <article class="card start-prime-shell">
    <header class="start-prime-head">
      <div class="start-prime-heading">
        <p class="start-prime-eyebrow">Minimal Logic Workspace</p>
        <h2>Start</h2>
      </div>
    </header>

    <div class="start-prime-grid">
      <section class="start-prime-editor">
        <div class="start-prime-editor-frame">
          <VoxCodeEditor
            ariaLabel="Start tab code editor"
            bind:value={programText}
            symbols={symbolTable}
            symbolStatuses={symbolStatuses}
            selectedSymbols={selectedVisualSymbols}
            symbolTypes={editorSymbolTypes}
            diagnostics={symbolDiagnostics}
            autocompleteEnabled={true}
            completionProvider={provideEditorCompletions}
            completionBuiltins={COMPLETION_BUILTINS}
            on:change={handleEditorChange}
            on:symbolclick={handleEditorSymbolClick}
          />
        </div>
      </section>

      <section class="start-prime-visual">
        <div class="start-viewer-wrap start-prime-viewer-wrap">
          <div class={`start-pure-viewer ${dreamVisible ? "is-under-dream" : ""}`}>
            {#if viewerMode === "error"}
              <div class="viewer-error">{viewerErrorMessage || "Unable to visualize value."}</div>
            {:else if viewerMode === "loading" && !dreamVisible}
              <div class="start-viewer-message">{viewerMessage || "Computing..."}</div>
            {:else if viewerMode === "value" && viewerRecords.length}
              <div
                class={`start-value-grid ${viewerRecords.length > 1 ? "is-multi" : "is-single"} ${maximizedViewerIndex >= 0 ? "has-maximized" : ""} ${dreamVisible ? "is-materializing" : ""}`.trim()}
              >
                {#each viewerRecords as record, index (`${record?.node_id || "value"}-${index}`)}
                  {#if maximizedViewerIndex < 0 || maximizedViewerIndex === index}
                    {@const descriptor = recordDescriptor(record)}
                    <article
                      class={`start-value-card ${["integer", "number", "boolean", "null", "string", "bytes"].includes(recordType(record)) ? "is-centered-value" : ""} ${maximizedViewerIndex === index ? "is-maximized" : ""}`.trim()}
                      title={`${recordLabel(record, index)} (${typeLabelFromDescriptor(descriptor)})`}
                    >
                      <header class="start-value-card-head">
                        {#if viewerRecords.length > 1 || maximizedViewerIndex === index}
                          <span class="start-value-card-label">{recordLabel(record, index)}</span>
                        {:else}
                          <span class="start-value-card-label"></span>
                        {/if}
                        <button
                          class="btn btn-ghost btn-small start-value-card-expand"
                          type="button"
                          on:click={() => toggleMaximizedViewer(index)}
                        >
                          {maximizedViewerIndex === index ? "Restore" : "Expand"}
                        </button>
                      </header>
                      <StartValueCanvas
                        {record}
                        label={recordLabel(record, index)}
                        sourceVariable={sourceVariableForRecord(record, index)}
                        level={0}
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
                    </article>
                  {/if}
                {/each}
              </div>
            {:else}
              <div class="start-viewer-message">Run or click a variable</div>
            {/if}
          </div>

          {#if dreamVisible}
            <div class={`start-compute-dream ${dreamDissolving ? "is-dissolving" : ""}`}>
              <div class="start-compute-mist"></div>
              <div class="start-compute-ids">
                {#each dreamNodeIds as nodeId, nodeIndex (`${nodeId}-${nodeIndex}`)}
                  <span class="start-compute-id" style={`--node-index:${nodeIndex}`}>{shortNodeId(nodeId)}</span>
                {/each}
              </div>
            </div>
          {/if}
        </div>

        <div class="start-prime-controls">
          <footer class="start-caption">
            <span class="start-caption-main">{captionVariable}</span>
          </footer>
          <div class="start-value-tag-row">
            {#if !Object.keys(symbolTable || {}).length}
              <span class="start-value-tag start-value-tag--empty">No visualizable symbols yet</span>
            {:else}
              {#each Object.keys(symbolTable || {}) as symbolName}
                <button
                  class={`start-value-tag start-value-tag--${statusLabel(symbolName)} ${selectedVisualSymbols.includes(symbolName) ? "is-selected" : ""}`.trim()}
                  type="button"
                  title={symbolTypeTitle(symbolName)}
                  on:click={(event) => void handleVisualTagClick(symbolName, event)}
                >
                  <span class="start-value-tag-name">{symbolName}</span>
                  <span class="start-value-tag-dot" aria-hidden="true"></span>
                </button>
              {/each}
            {/if}
          </div>
          <div class="start-prime-action-row">
            <span class={`start-run-state start-run-state--${statusValue}`} aria-hidden="true"></span>
            <button class="btn btn-primary btn-small" type="button" on:click={runPrimary}>Run</button>
          </div>
        </div>
      </section>
    </div>

    {#if diagnosticsText()}
      <div class="inline-error">{diagnosticsText()}</div>
    {:else if errorText}
      <div class="inline-error">{errorText}</div>
    {/if}
  </article>
</section>
