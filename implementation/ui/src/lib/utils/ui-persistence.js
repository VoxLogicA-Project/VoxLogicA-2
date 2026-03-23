const UI_STATE_STORAGE_KEY = "voxlogica.ui.state.v1";
const LEGACY_START_PROGRAM_STORAGE_KEY = "voxlogica.start.program.v1";
const UI_STATE_VERSION = 1;

const START_SPLIT_MIN = 0.32;
const START_SPLIT_MAX = 0.68;
const START_TECH_SPLIT_MIN = 28;
const START_TECH_SPLIT_MAX = 82;

const clampNumber = (value, min, max, fallback) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(max, Math.max(min, numeric));
};

const nonNegativeInt = (value, fallback = 0) => {
  const numeric = Math.floor(Number(value));
  if (!Number.isFinite(numeric) || numeric < 0) return fallback;
  return numeric;
};

const sanitizeStringArray = (value) => {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((entry) => String(entry || "").trim()).filter(Boolean))];
};

const sanitizeStringMap = (value) => {
  if (!value || typeof value !== "object") return {};
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, entry]) => [String(key || "").trim(), String(entry || "")])
      .filter(([key, entry]) => key && entry),
  );
};

const sanitizeCollectionSelections = (value) => {
  if (!value || typeof value !== "object") return {};
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, selection]) => {
        const normalizedKey = String(key || "").trim();
        if (!normalizedKey || !selection || typeof selection !== "object") return null;
        return [
          normalizedKey,
          {
            selectedIndex: nonNegativeInt(selection.selectedIndex, 0),
            selectedAbsoluteIndex: nonNegativeInt(selection.selectedAbsoluteIndex, 0),
            selectedPath: String(selection.selectedPath || ""),
          },
        ];
      })
      .filter(Boolean),
  );
};

const defaultEditorState = () => ({
  selectionStart: 0,
  selectionEnd: 0,
  scrollTop: 0,
  scrollLeft: 0,
});

const defaultStartState = () => ({
  programText: "",
  editor: defaultEditorState(),
  layout: {
    showCodePanel: true,
    showResultsPanel: true,
    showOperationsPanel: true,
    showOperationsHelp: false,
    splitRatio: 0.48,
  },
  viewer: {
    primaryVariable: "",
    currentPath: "",
    selectedVisualSymbols: [],
    maximizedViewerIndex: -1,
    collectionSelections: {},
    recordPagePointers: {},
  },
});

const defaultStartTechnicalState = () => ({
  splitPercent: 62,
});

const defaultUiState = () => ({
  version: UI_STATE_VERSION,
  app: {
    activeTab: "start",
  },
  start: defaultStartState(),
  startTechnical: defaultStartTechnicalState(),
});

const sanitizeEditorState = (value) => {
  const start = nonNegativeInt(value?.selectionStart, 0);
  const end = nonNegativeInt(value?.selectionEnd, start);
  return {
    selectionStart: Math.min(start, end),
    selectionEnd: Math.max(start, end),
    scrollTop: Math.max(0, Number(value?.scrollTop || 0) || 0),
    scrollLeft: Math.max(0, Number(value?.scrollLeft || 0) || 0),
  };
};

const sanitizeStartState = (value) => ({
  programText: String(value?.programText || ""),
  editor: sanitizeEditorState(value?.editor),
  layout: {
    showCodePanel: Boolean(value?.layout?.showCodePanel ?? true),
    showResultsPanel: Boolean(value?.layout?.showResultsPanel ?? true),
    showOperationsPanel: Boolean(value?.layout?.showOperationsPanel ?? true),
    showOperationsHelp: Boolean(value?.layout?.showOperationsHelp ?? false),
    splitRatio: clampNumber(value?.layout?.splitRatio, START_SPLIT_MIN, START_SPLIT_MAX, 0.48),
  },
  viewer: {
    primaryVariable: String(value?.viewer?.primaryVariable || ""),
    currentPath: String(value?.viewer?.currentPath || ""),
    selectedVisualSymbols: sanitizeStringArray(value?.viewer?.selectedVisualSymbols),
    maximizedViewerIndex: Number.isInteger(value?.viewer?.maximizedViewerIndex)
      ? Number(value.viewer.maximizedViewerIndex)
      : -1,
    collectionSelections: sanitizeCollectionSelections(value?.viewer?.collectionSelections),
    recordPagePointers: sanitizeStringMap(value?.viewer?.recordPagePointers),
  },
});

const sanitizeStartTechnicalState = (value) => ({
  splitPercent: clampNumber(value?.splitPercent, START_TECH_SPLIT_MIN, START_TECH_SPLIT_MAX, 62),
});

const sanitizeUiState = (value) => {
  const defaults = defaultUiState();
  return {
    version: UI_STATE_VERSION,
    app: {
      activeTab: String(value?.app?.activeTab || defaults.app.activeTab),
    },
    start: sanitizeStartState(value?.start),
    startTechnical: sanitizeStartTechnicalState(value?.startTechnical),
  };
};

const readLegacyStartProgram = () => {
  if (typeof window === "undefined") return "";
  try {
    return String(window.localStorage.getItem(LEGACY_START_PROGRAM_STORAGE_KEY) || "");
  } catch {
    return "";
  }
};

const applyLegacyMigration = (state) => {
  const migrated = sanitizeUiState(state);
  if (migrated.start.programText.trim()) return migrated;
  const legacyProgram = readLegacyStartProgram();
  if (!legacyProgram.trim()) return migrated;
  return sanitizeUiState({
    ...migrated,
    start: {
      ...migrated.start,
      programText: legacyProgram,
    },
  });
};

export const readPersistedUiState = () => {
  if (typeof window === "undefined") return defaultUiState();
  try {
    const raw = window.localStorage.getItem(UI_STATE_STORAGE_KEY);
    if (!raw) return applyLegacyMigration(defaultUiState());
    return applyLegacyMigration(JSON.parse(raw));
  } catch {
    return applyLegacyMigration(defaultUiState());
  }
};

const writePersistedUiState = (value) => {
  if (typeof window === "undefined") return sanitizeUiState(value);
  const nextState = sanitizeUiState(value);
  try {
    window.localStorage.setItem(UI_STATE_STORAGE_KEY, JSON.stringify(nextState));
    window.localStorage.removeItem(LEGACY_START_PROGRAM_STORAGE_KEY);
  } catch {
    return nextState;
  }
  return nextState;
};

export const updatePersistedUiState = (updater) => {
  const current = readPersistedUiState();
  const next = typeof updater === "function" ? updater(current) : updater;
  return writePersistedUiState(next);
};

export const readPersistedAppState = () => readPersistedUiState().app;

export const updatePersistedAppState = (patch) =>
  updatePersistedUiState((state) => ({
    ...state,
    app: {
      ...state.app,
      ...(typeof patch === "function" ? patch(state.app) : patch),
    },
  }));

export const readPersistedStartState = () => readPersistedUiState().start;

export const updatePersistedStartState = (patch) =>
  updatePersistedUiState((state) => ({
    ...state,
    start: sanitizeStartState(
      typeof patch === "function"
        ? patch(state.start)
        : {
            ...state.start,
            ...(patch || {}),
          },
    ),
  }));

export const readPersistedStartProgram = (fallback = "") => {
  const programText = String(readPersistedStartState().programText || "");
  return programText || String(fallback || "");
};

export const writePersistedStartProgram = (programText) =>
  updatePersistedStartState((state) => ({
    ...state,
    programText: String(programText || ""),
  }));

export const readPersistedStartTechnicalState = () => readPersistedUiState().startTechnical;

export const updatePersistedStartTechnicalState = (patch) =>
  updatePersistedUiState((state) => ({
    ...state,
    startTechnical: sanitizeStartTechnicalState(
      typeof patch === "function"
        ? patch(state.startTechnical)
        : {
            ...state.startTechnical,
            ...(patch || {}),
          },
    ),
  }));

export {
  LEGACY_START_PROGRAM_STORAGE_KEY,
  UI_STATE_STORAGE_KEY,
  UI_STATE_VERSION,
};