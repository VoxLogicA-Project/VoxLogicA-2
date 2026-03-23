import { beforeEach, describe, expect, it } from "vitest";

import {
  LEGACY_START_PROGRAM_STORAGE_KEY,
  UI_STATE_STORAGE_KEY,
  readPersistedStartProgram,
  readPersistedUiState,
  updatePersistedAppState,
  updatePersistedStartState,
} from "./ui-persistence.js";

describe("ui-persistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("migrates the legacy start program into the unified UI state", () => {
    window.localStorage.setItem(LEGACY_START_PROGRAM_STORAGE_KEY, "x = 1");

    expect(readPersistedStartProgram("")).toBe("x = 1");
    expect(readPersistedUiState().start.programText).toBe("x = 1");
  });

  it("writes app and start slices into the unified UI state key", () => {
    updatePersistedAppState({ activeTab: "gallery" });
    updatePersistedStartState({
      programText: "y = 2",
      layout: {
        showCodePanel: false,
        showResultsPanel: true,
        showOperationsPanel: false,
        showOperationsHelp: true,
        splitRatio: 0.7,
      },
      viewer: {
        primaryVariable: "y",
        currentPath: "/",
        selectedVisualSymbols: ["y", "y"],
        maximizedViewerIndex: 3,
        collectionSelections: {
          page0: { selectedIndex: 1, selectedAbsoluteIndex: 4, selectedPath: "/4" },
        },
        recordPagePointers: {
          base0: "cache0",
        },
      },
    });

    const raw = JSON.parse(window.localStorage.getItem(UI_STATE_STORAGE_KEY) || "{}");
    expect(raw.app?.activeTab).toBe("gallery");
    expect(raw.start?.programText).toBe("y = 2");
    expect(raw.start?.layout?.showCodePanel).toBe(false);
    expect(raw.start?.layout?.splitRatio).toBe(0.68);
    expect(raw.start?.viewer?.selectedVisualSymbols).toEqual(["y"]);
    expect(raw.start?.viewer?.recordPagePointers?.base0).toBe("cache0");
  });
});