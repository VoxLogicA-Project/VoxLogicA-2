import { cleanup, fireEvent, render, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

import StartTab from "./StartTab.svelte";
import { clearComputeActivity, ongoingComputeActivity, pushComputeActivity } from "$lib/stores/computeActivity.js";
import { UI_STATE_STORAGE_KEY } from "$lib/utils/ui-persistence.js";

const getProgramSymbolsMock = vi.fn();
const resolvePlaygroundValueMock = vi.fn();
const resolvePlaygroundValuePageMock = vi.fn();

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolveFn, rejectFn) => {
    resolve = resolveFn;
    reject = rejectFn;
  });
  return { promise, resolve, reject };
};

vi.mock("$lib/api/client.js", () => ({
  getProgramSymbols: (...args) => getProgramSymbolsMock(...args),
  resolvePlaygroundValue: (...args) => resolvePlaygroundValueMock(...args),
  resolvePlaygroundValuePage: (...args) => resolvePlaygroundValuePageMock(...args),
}));

describe("StartTab", () => {
  const persistedStartProgram = () => JSON.parse(window.localStorage.getItem(UI_STATE_STORAGE_KEY) || "{}");

  beforeEach(() => {
    window.localStorage.clear();
    clearComputeActivity();
    getProgramSymbolsMock.mockReset();
    resolvePlaygroundValueMock.mockReset();
    resolvePlaygroundValuePageMock.mockReset();
  });

  afterAll(async () => {
    cleanup();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("loads persisted code and resolves inferred primary variable", async () => {
    window.localStorage.setItem("voxlogica.start.program.v1", "a = 1\nb = a + 1");
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { a: "node-a", b: "node-b" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-b",
      path: "/",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: 2 },
        navigation: {
          path: "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });

    const { container, component } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });
    expect(resolvePlaygroundValueMock).not.toHaveBeenCalled();

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    const latest = resolvePlaygroundValueMock.mock.calls.at(-1)?.[0];
    expect(latest?.variable).toBe("b");
    expect(latest?.interaction?.intent).toBe("run-primary");
    await waitFor(() => {
      const captionName = container.querySelector(".start-caption-main");
      expect(captionName?.textContent || "").toContain("b");
      expect(container.textContent).toContain("2");
    });
  });

  it("persists edited code in unified UI state", async () => {
    vi.useFakeTimers();
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-x",
      path: "/",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: 1 },
        navigation: {
          path: "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });

    const { container, component } = render(StartTab, { active: true, capabilities: {} });
    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.value = "x = 3";
    await fireEvent.input(editor);
    vi.advanceTimersByTime(220);
    await waitFor(() => {
      expect(persistedStartProgram().start?.programText).toBe("x = 3");
    });

    vi.useRealTimers();
  });

  it("uses cache-first value refreshes while editing instead of enqueueing new work", async () => {
    vi.useFakeTimers();
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock
      .mockResolvedValueOnce({
        materialization: "computed",
        compute_status: "completed",
        node_id: "node-x",
        path: "/",
        descriptor: {
          vox_type: "integer",
          format_version: "voxpod/1",
          summary: { value: 1 },
          navigation: {
            path: "/",
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      })
      .mockResolvedValue({
        materialization: "pending",
        compute_status: "running",
        node_id: "node-x",
        path: "/",
      });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValueMock).toHaveBeenCalledTimes(1);
    });

    resolvePlaygroundValueMock.mockClear();

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();
    editor.value = "x = 2";
    await fireEvent.input(editor);

    vi.advanceTimersByTime(500);

    await waitFor(() => {
      expect(resolvePlaygroundValueMock).toHaveBeenCalled();
    });

    for (const [payload] of resolvePlaygroundValueMock.mock.calls) {
      expect(payload?.enqueue).toBe(false);
      expect(payload?.uiAwaited).toBe(false);
    }

    vi.useRealTimers();
  });

  it("restores persisted editor, layout, and viewer state from unified UI state", async () => {
    window.localStorage.setItem(
      UI_STATE_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        app: { activeTab: "start" },
        start: {
          programText: "a = 1\nb = a + 1",
          editor: {
            selectionStart: 2,
            selectionEnd: 5,
            scrollTop: 14,
            scrollLeft: 7,
          },
          layout: {
            showCodePanel: true,
            showResultsPanel: true,
            showOperationsPanel: false,
            showOperationsHelp: false,
            splitRatio: 0.61,
          },
          viewer: {
            primaryVariable: "a",
            currentPath: "/child",
            selectedVisualSymbols: ["a"],
            maximizedViewerIndex: -1,
            collectionSelections: {},
            recordPagePointers: {},
            expandedCollectionStages: { "a:/child": true },
          },
        },
      }),
    );

    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { a: "node-a", b: "node-b" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-a",
      path: "/child",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: 1 },
        navigation: {
          path: "/child",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });

    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
      expect(resolvePlaygroundValueMock).toHaveBeenCalled();
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor?.value).toBe("a = 1\nb = a + 1");

    const codeToggle = [...container.querySelectorAll(".start-pane-toggle")].find((button) =>
      String(button.textContent || "").includes("Code"),
    );
    const operationsToggle = [...container.querySelectorAll(".start-pane-toggle")].find((button) =>
      String(button.textContent || "").includes("Operations"),
    );
    expect(codeToggle?.getAttribute("aria-pressed")).toBe("true");
    expect(operationsToggle?.getAttribute("aria-pressed")).toBe("false");

    const grid = container.querySelector(".start-prime-grid");
    expect(grid?.getAttribute("style") || "").toContain("61.0%");

    const latestResolve = resolvePlaygroundValueMock.mock.calls.at(-1)?.[0];
    expect(latestResolve?.variable).toBe("a");
    expect(latestResolve?.path).toBe("/child");
  });

  it("panic reset clears persisted Start state and restores the default program", async () => {
    const originalConfirm = window.confirm;
    window.confirm = vi.fn(() => true);

    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-x",
      path: "/",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: 3 },
        navigation: {
          path: "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.value = "x = 3";
    await fireEvent.input(editor);
    await waitFor(() => {
      expect(persistedStartProgram().start?.programText).toBe("x = 3");
    });

    const panicButton = container.querySelector(".start-panic-reset");
    expect(panicButton).not.toBeNull();
    await fireEvent.click(panicButton);

    await waitFor(() => {
      const persisted = persistedStartProgram();
      expect(window.confirm).toHaveBeenCalled();
      expect(container.querySelector(".vx-editor__textarea")?.value || "").toContain('import "simpleitk"');
      expect(persisted.start?.programText || "").toContain('import "simpleitk"');
      expect(persisted.start?.viewer?.expandedCollectionStages || {}).toEqual({});
      expect(persisted.start?.viewer?.currentPath || "").toBe("");
    });

    window.confirm = originalConfirm;
  });

  it("debounces edit-time symbol refresh and only sends the latest editor state", async () => {
    vi.useFakeTimers();
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    getProgramSymbolsMock.mockClear();

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.value = "x = 1";
    await fireEvent.input(editor);
    editor.value = "x = 2";
    await fireEvent.input(editor);
    editor.value = "x = 9";
    await fireEvent.input(editor);

    expect(getProgramSymbolsMock).not.toHaveBeenCalled();

    vi.advanceTimersByTime(159);
    expect(getProgramSymbolsMock).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalledTimes(1);
    });
    expect(getProgramSymbolsMock.mock.calls[0][0]).toBe("x = 9");

    vi.useRealTimers();
  });

  it("surfaces static diagnostics and blocks value resolve", async () => {
    getProgramSymbolsMock.mockImplementation(async (program) => ({
      available: true,
      symbol_table: String(program || "").trim() === "x =" ? { x: "node-x" } : { a: "node-a", b: "node-b" },
      diagnostics:
        String(program || "").trim() === "x ="
          ? [{ code: "E_PARSE", message: "Unexpected token", location: "line 1, column 1" }]
          : [],
    }));

    const { container, unmount } = render(StartTab, { active: true, capabilities: {} });
    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();
    editor.value = "x =";
    await fireEvent.input(editor);

    await waitFor(() => {
      expect(getProgramSymbolsMock.mock.calls.length).toBeGreaterThanOrEqual(1);
      expect(editor.value).toBe("x =");
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    expect(resolvePlaygroundValueMock).not.toHaveBeenCalled();
    expect(container.querySelector(".inline-error")).toBeNull();
  });

  it("promotes run-time parse failures into editor diagnostics instead of inline request errors", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });
    const error = new Error("Unexpected token");
    error.detail = {
      message: "Unexpected token",
      diagnostics: [{ code: "E_PARSE", message: "Unexpected token", location: "line 1, column 3" }],
    };
    error.diagnostics = error.detail.diagnostics;
    resolvePlaygroundValueMock.mockRejectedValue(error);

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(container.querySelector('.vx-editor__line--error[data-line="1"]')).not.toBeNull();
      expect(container.querySelector(".inline-error")).toBeNull();
      expect(container.textContent || "").toContain("Unexpected token");
    });
  });

  it("keeps the last computed value visible but marks it stale while editing through diagnostics", async () => {
    getProgramSymbolsMock.mockImplementation(async (program) => {
      const source = String(program || "").trim();
      if (source === "a = 1\nb = a +") {
        return {
          available: false,
          symbol_table: {},
          diagnostics: [{ code: "E_PARSE", message: "Unexpected token", location: "line 2, column 7" }],
        };
      }
      return {
        available: true,
        symbol_table: { a: "node-a", b: "node-b" },
        diagnostics: [],
      };
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-b",
      path: "/",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: 2 },
        navigation: {
          path: "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(container.textContent).toContain("2");
    });

    resolvePlaygroundValueMock.mockClear();

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();
    editor.value = "a = 1\nb = a +";
    await fireEvent.input(editor);

    await waitFor(() => {
      expect(getProgramSymbolsMock.mock.calls.length).toBeGreaterThanOrEqual(2);
      expect(editor.value).toBe("a = 1\nb = a +");
      expect(container.querySelector(".start-value-card.is-stale")).not.toBeNull();
      expect(container.querySelector(".start-caption-status")?.textContent || "").toContain("Stale while editing");
      expect(container.querySelector(".start-stale-banner")?.textContent || "").toContain("Stale");
      expect(container.querySelector('.vx-editor__line--error[data-line="2"]')).not.toBeNull();
      expect(container.querySelectorAll(".start-value-tag").length).toBe(2);
      expect(container.textContent).toContain("2");
    });

    const aTag = [...container.querySelectorAll(".start-value-tag")].find((button) =>
      String(button.textContent || "").includes("a"),
    );
    expect(aTag).not.toBeNull();
    await fireEvent.click(aTag);

    await waitFor(() => {
      expect(container.querySelector(".start-caption-main")?.textContent || "").toContain("a");
    });

    expect(container.querySelector(".inline-error")).toBeNull();
  });

  it("keeps the selected overlay viewer stable when edit-time symbols temporarily drop it", async () => {
    const initialProgram = "img = 1\nmid = 2\nthr = img\nstats = 3";
    const partialProgram = "img = 1\nmid = 2 +\nthr = img\nstats = 3";
    const restoredProgram = "img = 1\nmid = 2 + 1\nthr = img\nstats = 3";
    window.localStorage.setItem("voxlogica.start.program.v1", initialProgram);

    getProgramSymbolsMock.mockImplementation(async (program) => {
      const source = String(program || "").trim();
      if (source === partialProgram) {
        return {
          available: true,
          symbol_table: { img: "node-img", mid: "node-mid" },
          diagnostics: [],
        };
      }
      return {
        available: true,
        symbol_table: { img: "node-img", mid: "node-mid", thr: "node-thr", stats: "node-stats" },
        diagnostics: [],
      };
    });

    resolvePlaygroundValueMock.mockImplementation(async ({ variable }) => {
      if (variable === "thr") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-thr",
          path: "/",
          descriptor: {
            vox_type: "overlay",
            format_version: "voxpod/1",
            summary: { layer_count: 2, layer_labels: ["Base", "Mask"] },
            render: {
              kind: "image-overlay",
              layers: [
                { label: "Base", png_url: "/base-thr.png", opacity: 1.0, visible: true },
                { label: "Mask", png_url: "/mask-thr.png", opacity: 0.42, visible: true },
              ],
            },
            navigation: {
              path: "/",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "computed",
        compute_status: "completed",
        node_id: `node-${variable}`,
        path: "/",
        descriptor: {
          vox_type: "integer",
          format_version: "voxpod/1",
          summary: { value: variable === "img" ? 1 : variable === "mid" ? 2 : 3 },
          navigation: {
            path: "/",
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(container.querySelectorAll("button.start-value-tag").length).toBe(4);
    });

    const thrTag = Array.from(container.querySelectorAll("button.start-value-tag")).find((el) =>
      (el.textContent || "").includes("thr"),
    );
    expect(thrTag).not.toBeUndefined();
    await fireEvent.click(thrTag);

    let shellBefore = null;
    await waitFor(() => {
      const caption = container.querySelector(".start-caption-main");
      shellBefore = container.querySelector(".start-overlay-image-shell");
      expect(caption?.textContent || "").toContain("thr");
      expect(shellBefore).not.toBeNull();
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();

    editor.value = partialProgram;
    await fireEvent.input(editor);

    await waitFor(() => {
      const caption = container.querySelector(".start-caption-main");
      const shellDuring = container.querySelector(".start-overlay-image-shell");
      expect(caption?.textContent || "").toContain("thr");
      expect(shellDuring).toBe(shellBefore);
      expect(container.querySelector(".start-value-card.is-stale")).not.toBeNull();
    });

    editor.value = restoredProgram;
    await fireEvent.input(editor);
    await new Promise((resolve) => setTimeout(resolve, 180));

    await waitFor(() => {
      const caption = container.querySelector(".start-caption-main");
      const shellAfter = container.querySelector(".start-overlay-image-shell");
      expect(caption?.textContent || "").toContain("thr");
      expect(shellAfter).toBe(shellBefore);
      expect(container.querySelector(".start-value-card.is-stale")).toBeNull();
    });

    cleanup();
    await Promise.resolve();
  });

  it("shows static symbol types on hover before computation", async () => {
    window.localStorage.setItem("voxlogica.start.program.v1", "ov = overlay(1, 2)");
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { ov: "node-ov" },
      symbol_output_kinds: { ov: "overlay" },
      diagnostics: [],
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      const tag = container.querySelector(".start-value-tag");
      expect(tag?.getAttribute("title") || "").toContain("ov (overlay)");
    });

    const editorSymbol = container.querySelector('.vx-editor__symbol[title="ov (overlay)"]');
    expect(editorSymbol).not.toBeNull();
  });

  it("shows the live operations log in the right-side operations pane by default", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    pushComputeActivity({
      type: "resolve.primary",
      phase: "start",
      trackActive: true,
      operationKey: "resolve:x:/",
      summary: "Resolving x /",
      variable: "x",
      path: "/",
      status: "running",
      source: "start-tab",
    });

    await waitFor(() => {
      expect(container.querySelector(".start-prime-operations")).not.toBeNull();
      expect(container.textContent).toContain("Live now");
      expect(container.textContent).toContain("Resolving x /");
    });
  });

  it("shows inline explanations for operations labels", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const infoButton = container.querySelector('.start-operations-info[aria-label="Explain operations labels"]');
    expect(infoButton).not.toBeNull();
    await fireEvent.click(infoButton);

    await waitFor(() => {
      expect(container.textContent).toContain("What these labels mean");
      expect(container.textContent).toContain("Sent HTTP request");
      expect(container.textContent).toContain("Session only");
    });
  });

  it("toggles code, results, and operations panes independently", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const buttons = Array.from(container.querySelectorAll("button"));
    const codeToggle = buttons.find((button) => String(button.textContent || "").trim() === "Code");
    const resultsToggle = buttons.find((button) => String(button.textContent || "").trim() === "Results");
    const operationsToggle = buttons.find((button) => String(button.textContent || "").includes("Operations"));

    expect(codeToggle).not.toBeNull();
    expect(resultsToggle).not.toBeNull();
    expect(operationsToggle).not.toBeNull();
    expect(container.querySelector(".start-prime-editor")).not.toBeNull();
    expect(container.querySelector(".start-prime-results")).not.toBeNull();
    expect(container.querySelector(".start-prime-operations")).not.toBeNull();

    await fireEvent.click(codeToggle);
    await waitFor(() => {
      expect(container.querySelector(".start-prime-editor")).toBeNull();
    });

    await fireEvent.click(resultsToggle);
    await waitFor(() => {
      expect(container.querySelector(".start-prime-results")).toBeNull();
    });

    await fireEvent.click(operationsToggle);
    await waitFor(() => {
      expect(container.querySelector(".start-prime-operations")).toBeNull();
    });
  });

  it("marks a selected collection as loading while its nested page is still hydrating", async () => {
    const nestedPageDeferred = deferred();

    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-xs",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 3 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockImplementation(({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return Promise.resolve({
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 3,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 11 },
                  navigation: { path: "/0", pageable: true, can_descend: true, default_page_size: 64, max_page_size: 512 },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/1",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 11 },
                  navigation: { path: "/1", pageable: true, can_descend: true, default_page_size: 64, max_page_size: 512 },
                },
              },
              {
                index: 2,
                label: "[2]",
                path: "/2",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 11 },
                  navigation: { path: "/2", pageable: true, can_descend: true, default_page_size: 64, max_page_size: 512 },
                },
              },
            ],
          },
        });
      }
      if (path === "/2") {
        return nestedPageDeferred.promise;
      }
      return Promise.resolve({
        materialization: "computed",
        compute_status: "completed",
        path,
        page: {
          offset,
          limit,
          has_more: false,
          next_offset: null,
          total: 1,
          items: [
            {
              index: 0,
              label: "[0]",
              path: `${path}/0`,
              status: "materialized",
              descriptor: {
                vox_type: "integer",
                format_version: "voxpod/1",
                summary: { value: 1 },
                navigation: { path: `${path}/0`, pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
              },
            },
          ],
        },
      });
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    let targetButton;
    await waitFor(() => {
      const buttons = Array.from(container.querySelectorAll(".start-prime-results .start-collection-item"));
      targetButton = buttons.find((button) => (button.textContent || "").includes("[2]"));
      expect(targetButton).not.toBeUndefined();
    });
    await fireEvent.click(targetButton);

    await waitFor(() => {
      const selectedBadge = container.querySelector(".start-prime-results .start-collection-item.is-selected .start-collection-item-state");
      const stageBadge = container.querySelector(".start-prime-results .start-collection-stage-status");
      expect(selectedBadge?.textContent?.trim().toLowerCase()).toBe("loading");
      expect(stageBadge?.textContent?.trim().toLowerCase()).toBe("loading");
    });

    nestedPageDeferred.resolve({
      materialization: "computed",
      compute_status: "completed",
      path: "/2",
      page: {
        offset: 0,
        limit: 18,
        has_more: false,
        next_offset: null,
        total: 1,
        items: [
          {
            index: 0,
            label: "[0]",
            path: "/2/0",
            status: "materialized",
            descriptor: {
              vox_type: "integer",
              format_version: "voxpod/1",
              summary: { value: 42 },
              navigation: { path: "/2/0", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
            },
          },
        ],
      },
    });
  });

  it("keeps a selected collection in loading state when a cached nested page is still polling", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-xs",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 2 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 2,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 11 },
                  navigation: { path: "/0", pageable: true, can_descend: true, default_page_size: 64, max_page_size: 512 },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/1",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 11 },
                  navigation: { path: "/1", pageable: true, can_descend: true, default_page_size: 64, max_page_size: 512 },
                },
              },
            ],
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        path,
        page: {
          offset,
          limit,
          has_more: false,
          next_offset: null,
          total: 0,
          items: [],
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    let targetButton;
    await waitFor(() => {
      const buttons = Array.from(container.querySelectorAll(".start-prime-results .start-collection-item"));
      targetButton = buttons.find((button) => (button.textContent || "").includes("[1]"));
      expect(targetButton).not.toBeUndefined();
    });
    await fireEvent.click(targetButton);

    await waitFor(() => {
      const selectedBadge = container.querySelector(".start-prime-results .start-collection-item.is-selected .start-collection-item-state");
      const stageBadge = container.querySelector(".start-prime-results .start-collection-stage-status");
      expect(selectedBadge?.textContent?.trim().toLowerCase()).toBe("loading");
      expect(stageBadge?.textContent?.trim().toLowerCase()).toBe("loading");
    });
  });

  it("does not keep page activity live for cached pages whose visible rows are merely not_loaded", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "cached",
      compute_status: "cached",
      node_id: "node-xs",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 2 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "cached",
      compute_status: "cached",
      path: "/",
      page: {
        offset: 0,
        limit: 18,
        has_more: false,
        next_offset: null,
        total: 2,
        items: [
          {
            index: 0,
            label: "[0]",
            path: "/0",
            status: "pending",
            descriptor: {
              vox_type: "unavailable",
              format_version: "voxpod/1",
              summary: { reason: "not loaded yet" },
              navigation: { path: "/0", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
            },
          },
          {
            index: 1,
            label: "[1]",
            path: "/1",
            status: "pending",
            descriptor: {
              vox_type: "unavailable",
              format_version: "voxpod/1",
              summary: { reason: "not loaded yet" },
              navigation: { path: "/1", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
            },
          },
        ],
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalled();
      expect(get(ongoingComputeActivity)).toHaveLength(0);
    });
  });

  it("reloads selected collection child detail after the parent node changes on edit", async () => {
    vi.useFakeTimers();

    const initialProgram = "xs = old";
    const updatedProgram = "xs = new";
    window.localStorage.setItem("voxlogica.start.program.v1", initialProgram);

    getProgramSymbolsMock.mockImplementation(async (program) => ({
      available: true,
      symbol_table: { xs: String(program || "").trim() === updatedProgram ? "node-xs-new" : "node-xs-old" },
      diagnostics: [],
    }));

    resolvePlaygroundValueMock.mockImplementation(async ({ program, variable, path = "" }) => {
      const source = String(program || "").trim();
      const isUpdated = source === updatedProgram;
      if (variable !== "xs") throw new Error(`unexpected variable ${variable}`);
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: isUpdated ? "node-xs-new" : "node-xs-old",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 1 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/0") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: isUpdated ? "node-xs-new-item" : "node-xs-old-item",
          path: "/0",
          descriptor: {
            vox_type: "integer",
            format_version: "voxpod/1",
            summary: { value: isUpdated ? 99 : 1 },
            navigation: {
              path: "/0",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      throw new Error(`unexpected path ${path}`);
    });

    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 1,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                status: "materialized",
                descriptor: {
                  vox_type: "unavailable",
                  format_version: "voxpod/1",
                  summary: { reason: "detail required" },
                  navigation: {
                    path: "/0",
                    pageable: false,
                    can_descend: false,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
            ],
          },
        };
      }
      throw new Error(`unexpected page path ${path}`);
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      const initialDetailCalls = resolvePlaygroundValueMock.mock.calls.filter(
        ([request]) => request?.variable === "xs" && request?.path === "/0" && request?.program === initialProgram,
      );
      expect(initialDetailCalls).toHaveLength(1);
      expect(container.textContent).toContain("1");
    });

    const editor = container.querySelector(".vx-editor__textarea");
    expect(editor).not.toBeNull();
    editor.value = updatedProgram;
    await fireEvent.input(editor);

    vi.advanceTimersByTime(220);
    await Promise.resolve();
    vi.advanceTimersByTime(260);

    await waitFor(() => {
      const updatedDetailCalls = resolvePlaygroundValueMock.mock.calls.filter(
        ([request]) => request?.variable === "xs" && request?.path === "/0" && request?.program === updatedProgram,
      );
      expect(updatedDetailCalls).toHaveLength(1);
      expect(container.textContent).toContain("99");
    });

    vi.useRealTimers();
  });

  it("surfaces concrete execution error details instead of generic failure counts", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "failed",
      compute_status: "failed",
      node_id: "node-x",
      path: "/",
      error: "Execution failed with 1 errors",
      execution_errors: {
        "node-read": "ReadImage could not open '/tmp/missing_flair.nii.gz'",
      },
      execution_error_details: {
        "node-read": {
          operator: "ReadImage",
          args: ["/tmp/missing_flair.nii.gz"],
          kwargs: {},
          kind: "primitive",
          output_kind: "volume3d",
        },
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(container.textContent).toContain("ReadImage could not open");
      expect(container.textContent).toContain("execution_errors:");
    });
  });

  it("polls pending values until completion", async () => {
    vi.useFakeTimers();
    const originalWebSocket = globalThis.WebSocket;
    try {
      Object.defineProperty(globalThis, "WebSocket", {
        value: undefined,
        configurable: true,
      });
    } catch {
      // best effort for environments where WebSocket is not configurable
    }
    try {
      getProgramSymbolsMock.mockResolvedValue({
        available: true,
        symbol_table: { x: "node-x" },
        diagnostics: [],
      });
      resolvePlaygroundValueMock
        .mockResolvedValueOnce({
          materialization: "pending",
          compute_status: "running",
          node_id: "node-x",
          job_id: "job-1234567890",
          log_tail: '{"event":"playground.node","operator":"const","status":"cached","cache_source":"store","duration_s":0.001,"node_id":"node-c"}',
        })
        .mockResolvedValue({
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-x",
          path: "/",
          descriptor: {
            vox_type: "integer",
            format_version: "voxpod/1",
            summary: { value: 1 },
            navigation: {
              path: "/",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        });

      const { container } = render(StartTab, { active: true, capabilities: {} });
      await waitFor(() => {
        expect(getProgramSymbolsMock).toHaveBeenCalled();
      });

      const runButton = container.querySelector(".btn.btn-primary");
      expect(runButton).not.toBeNull();
      await fireEvent.click(runButton);

      await waitFor(() => {
        expect(resolvePlaygroundValueMock).toHaveBeenCalled();
      });

      vi.runOnlyPendingTimers();
      await waitFor(() => {
        expect(resolvePlaygroundValueMock.mock.calls.length).toBeGreaterThanOrEqual(2);
        const runState = container.querySelector(".start-run-state--completed");
        expect(runState).not.toBeNull();
      });
    } finally {
      try {
        Object.defineProperty(globalThis, "WebSocket", {
          value: originalWebSocket,
          configurable: true,
        });
      } catch {
        // best effort restore
      }
      vi.useRealTimers();
    }
  });

  it("keeps a poll fallback when websocket subscriptions stay silent", async () => {
    vi.useFakeTimers();
    const originalWebSocket = globalThis.WebSocket;

    class SilentWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 3;

      constructor() {
        this.readyState = SilentWebSocket.CONNECTING;
        setTimeout(() => {
          this.readyState = SilentWebSocket.OPEN;
          this.onopen?.();
        }, 0);
      }

      send() {}

      close() {
        this.readyState = SilentWebSocket.CLOSED;
        this.onclose?.();
      }
    }

    try {
      Object.defineProperty(globalThis, "WebSocket", {
        value: SilentWebSocket,
        configurable: true,
      });
    } catch {
      // best effort for environments where WebSocket is not configurable
    }

    try {
      getProgramSymbolsMock.mockResolvedValue({
        available: true,
        symbol_table: { x: "node-x" },
        diagnostics: [],
      });
      resolvePlaygroundValueMock
        .mockResolvedValueOnce({
          materialization: "pending",
          compute_status: "running",
          node_id: "node-x",
          job_id: "job-silent-ws",
          path: "/",
        })
        .mockResolvedValue({
          materialization: "cached",
          compute_status: "cached",
          node_id: "node-x",
          path: "/",
          descriptor: {
            vox_type: "integer",
            format_version: "voxpod/1",
            summary: { value: 7 },
            navigation: {
              path: "/",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        });

      const { container } = render(StartTab, { active: true, capabilities: {} });
      await waitFor(() => {
        expect(getProgramSymbolsMock).toHaveBeenCalled();
      });

      const runButton = container.querySelector(".btn.btn-primary");
      expect(runButton).not.toBeNull();
      await fireEvent.click(runButton);

      await waitFor(() => {
        expect(resolvePlaygroundValueMock).toHaveBeenCalledTimes(1);
        expect(container.querySelector(".start-run-state--running")).not.toBeNull();
      });

      vi.advanceTimersByTime(1200);

      await waitFor(() => {
        expect(resolvePlaygroundValueMock.mock.calls.length).toBeGreaterThanOrEqual(2);
        expect(container.querySelector(".start-run-state--completed")).not.toBeNull();
        expect(container.textContent).toContain("7");
      });
    } finally {
      try {
        Object.defineProperty(globalThis, "WebSocket", {
          value: originalWebSocket,
          configurable: true,
        });
      } catch {
        // best effort restore
      }
      vi.useRealTimers();
    }
  });

  it("renders value tags with visual states and resolves clicked tags", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { a: "node-a", b: "node-b" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ variable }) => ({
      materialization: "computed",
      compute_status: "completed",
      node_id: `node-${variable || "x"}`,
      path: "/",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: variable === "b" ? 2 : 1 },
        navigation: {
          path: "/",
          pageable: false,
          can_descend: false,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    }));

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(container.querySelectorAll("button.start-value-tag").length).toBeGreaterThanOrEqual(2);
    });
    const valueTags = Array.from(container.querySelectorAll("button.start-value-tag"));
    const aTag = valueTags.find((el) => (el.textContent || "").includes("a"));
    const bTag = valueTags.find((el) => (el.textContent || "").includes("b"));
    expect(aTag).not.toBeUndefined();
    expect(bTag).not.toBeUndefined();
    expect(aTag?.getAttribute("title") || "").toContain("a");

    await fireEvent.click(aTag);
    await waitFor(() => {
      const latest = resolvePlaygroundValueMock.mock.calls.at(-1)?.[0];
      expect(latest?.variable).toBe("a");
    });

    const latestBTag = Array.from(container.querySelectorAll("button.start-value-tag")).find((el) =>
      (el.textContent || "").includes("b"),
    );
    await fireEvent.click(latestBTag, { ctrlKey: true });
    await waitFor(() => {
      const latest = resolvePlaygroundValueMock.mock.calls.at(-1)?.[0];
      expect(latest?.variable).toBe("b");
    });
    await waitFor(() => {
      expect(container.querySelectorAll(".start-value-tag.is-selected").length).toBe(2);
    });
    expect(container.querySelector(".start-value-tag--failed")).toBeNull();
  });

  it("uses non-enqueue paging requests for collection previews", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-x",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 3 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "pending",
      compute_status: "running",
      page: { offset: 0, limit: 8, items: [], has_more: false, next_offset: null },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });
    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);
    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalled();
    });
    await waitFor(() => {
      const latest = resolvePlaygroundValuePageMock.mock.calls.at(-1)?.[0];
      expect(latest?.enqueue).toBe(false);
    });
  });

  it("keeps polling collection page while visible items are still pending", async () => {
    vi.useFakeTimers();
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-x",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 2 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock
      .mockResolvedValueOnce({
        materialization: "pending",
        compute_status: "running",
        page: {
          offset: 0,
          limit: 8,
          has_more: false,
          next_offset: null,
          items: [
            {
              index: 0,
              label: "[0]",
              path: "/0",
              state: "blocked",
              blocked_on: "/source/0",
              descriptor: { vox_type: "unavailable", summary: {} },
            },
            {
              index: 1,
              label: "[1]",
              path: "/1",
              state: "running",
              descriptor: { vox_type: "unavailable", summary: {} },
            },
          ],
        },
      })
      .mockResolvedValue({
        materialization: "computed",
        compute_status: "completed",
        page: {
          offset: 0,
          limit: 8,
          has_more: false,
          next_offset: null,
          items: [
            { index: 0, label: "[0]", path: "/0", status: "materialized", descriptor: { vox_type: "integer", summary: { value: 1 } } },
            { index: 1, label: "[1]", path: "/1", status: "materialized", descriptor: { vox_type: "integer", summary: { value: 2 } } },
          ],
        },
      });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });
    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);
    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalledTimes(1);
    });
    vi.advanceTimersByTime(1400);
    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    vi.useRealTimers();
  });

  it("renders exact collection item states from page payloads", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-xs",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 3 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "running",
      compute_status: "running",
      page: {
        offset: 0,
        limit: 8,
        has_more: false,
        next_offset: null,
        items: [
          {
            index: 0,
            label: "[0]",
            path: "/0",
            state: "queued",
            descriptor: { vox_type: "unavailable", summary: {} },
          },
          {
            index: 1,
            label: "[1]",
            path: "/1",
            state: "blocked",
            blocked_on: "/0",
            state_reason: "waiting-on-upstream",
            descriptor: { vox_type: "unavailable", summary: {} },
          },
          {
            index: 2,
            label: "[2]",
            path: "/2",
            state: "ready",
            descriptor: { vox_type: "integer", summary: { value: 7 } },
          },
        ],
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(container.textContent).toContain("queued");
      expect(container.textContent).toContain("blocked");
      expect(container.textContent).toContain("ready");
    });

    const blockedButton = Array.from(container.querySelectorAll(".start-collection-item")).find((button) =>
      (button.textContent || "").includes("[1]"),
    );
    expect(blockedButton?.getAttribute("title") || "").toContain("blocked on /0");
  });

  it("keeps the selected collection row in sync with the resolved stage value", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 8 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/5") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/5",
          descriptor: {
            vox_type: "number",
            format_version: "voxpod/1",
            summary: { value: 0.88 },
            navigation: {
              path: "/5",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "unavailable",
          format_version: "voxpod/1",
          summary: { reason: "status=pending" },
          navigation: {
            path,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "pending",
      compute_status: "running",
      path: "/",
      page: {
        offset: 0,
        limit: 18,
        has_more: false,
        next_offset: null,
        items: Array.from({ length: 8 }, (_, index) => ({
          index,
          label: `[${index}]`,
          path: `/${index}`,
          status: "pending",
          descriptor: {
            vox_type: "unavailable",
            format_version: "voxpod/1",
            summary: { reason: "status=pending" },
            navigation: {
              path: `/${index}`,
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        })),
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(container.querySelectorAll(".start-collection-item").length).toBeGreaterThanOrEqual(6);
    });
    const rowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
    expect(rowFive).not.toBeUndefined();
    await fireEvent.click(rowFive);

    await waitFor(() => {
      expect(resolvePlaygroundValueMock.mock.calls.some(([payload]) => payload?.path === "/5")).toBe(true);
      expect(container.textContent).toContain("0.88");
    });

    await waitFor(() => {
      const updatedRowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
      expect(updatedRowFive?.textContent || "").toContain("ready");
      expect(updatedRowFive?.textContent || "").toContain("0.88");
      expect(updatedRowFive?.getAttribute("title") || "").toContain("[5] (number) · ready");
    });
  });

  it("promotes a row to ready when a pending path resolve already carries a concrete value descriptor", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 8 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/5") {
        return {
          materialization: "pending",
          compute_status: "missing",
          node_id: "node-xs",
          path: "/5",
          descriptor: {
            vox_type: "number",
            format_version: "voxpod/1",
            summary: { value: 0.88 },
            navigation: {
              path: "/5",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "unavailable",
          format_version: "voxpod/1",
          summary: { reason: "status=pending" },
          navigation: {
            path,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "pending",
      compute_status: "running",
      path: "/",
      page: {
        offset: 0,
        limit: 18,
        has_more: false,
        next_offset: null,
        items: Array.from({ length: 8 }, (_, index) => ({
          index,
          label: `[${index}]`,
          path: `/${index}`,
          status: "pending",
          descriptor: {
            vox_type: "unavailable",
            format_version: "voxpod/1",
            summary: { reason: "status=pending" },
            navigation: {
              path: `/${index}`,
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        })),
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(container.querySelectorAll(".start-collection-item").length).toBeGreaterThanOrEqual(6);
    });
    const rowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
    expect(rowFive).not.toBeUndefined();
    await fireEvent.click(rowFive);

    await waitFor(() => {
      expect(resolvePlaygroundValueMock.mock.calls.some(([payload]) => payload?.path === "/5")).toBe(true);
      expect(container.textContent).toContain("0.88");
    });

    await waitFor(() => {
      const updatedRowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
      expect(updatedRowFive?.textContent || "").toContain("ready");
      expect(updatedRowFive?.textContent || "").toContain("0.88");
      expect(updatedRowFive?.getAttribute("title") || "").toContain("[5] (number) · ready");
    });
  });

  it("switches the stage when clicking a different ready child after the first ready child auto-hydrates", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 8 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/0") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/0",
          descriptor: {
            vox_type: "number",
            format_version: "voxpod/1",
            summary: { value: 0.11 },
            navigation: {
              path: "/0",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/5") {
        return {
          materialization: "pending",
          compute_status: "missing",
          node_id: "node-xs",
          path: "/5",
          descriptor: {
            vox_type: "number",
            format_version: "voxpod/1",
            summary: { value: 0.88 },
            navigation: {
              path: "/5",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "unavailable",
          format_version: "voxpod/1",
          summary: { reason: "status=pending" },
          navigation: {
            path,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "pending",
      compute_status: "running",
      path: "/",
      page: {
        offset: 0,
        limit: 18,
        has_more: false,
        next_offset: null,
        items: Array.from({ length: 8 }, (_, index) => ({
          index,
          label: `[${index}]`,
          path: `/${index}`,
          status: "pending",
          descriptor: {
            vox_type: "unavailable",
            format_version: "voxpod/1",
            summary: { reason: "status=pending" },
            navigation: {
              path: `/${index}`,
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        })),
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      const stageScalar = container.querySelector(".start-collection-stage .start-pure-scalar");
      expect(stageScalar?.textContent || "").toContain("0.11");
      const stageLabel = container.querySelector(".start-collection-stage-label");
      expect(stageLabel?.textContent || "").toContain("[0]");
    });

    const rowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
    expect(rowFive).not.toBeUndefined();
    await fireEvent.click(rowFive);

    await waitFor(() => {
      const updatedRowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
      expect(updatedRowFive?.textContent || "").toContain("ready");
      expect(updatedRowFive?.textContent || "").toContain("0.88");
      const stageScalar = container.querySelector(".start-collection-stage .start-pure-scalar");
      expect(stageScalar?.textContent || "").toContain("0.88");
      const stageLabel = container.querySelector(".start-collection-stage-label");
      expect(stageLabel?.textContent || "").toContain("[5]");
    });
  });

  it("does not let a later pending page snapshot overwrite a concrete child that is already cached", async () => {
    vi.useFakeTimers();
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 8 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/5") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/5",
          descriptor: {
            vox_type: "number",
            format_version: "voxpod/1",
            summary: { value: 0.88 },
            navigation: {
              path: "/5",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "unavailable",
          format_version: "voxpod/1",
          summary: { reason: "status=pending" },
          navigation: {
            path,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });

    const pendingRootPage = {
      materialization: "pending",
      compute_status: "running",
      path: "/",
      page: {
        offset: 0,
        limit: 18,
        has_more: false,
        next_offset: null,
        items: Array.from({ length: 8 }, (_, index) => ({
          index,
          label: `[${index}]`,
          path: `/${index}`,
          status: "pending",
          descriptor: {
            vox_type: "unavailable",
            format_version: "voxpod/1",
            summary: { reason: "status=pending" },
            navigation: {
              path: `/${index}`,
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        })),
      },
    };
    resolvePlaygroundValuePageMock.mockImplementation(async () => pendingRootPage);

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalledTimes(1);
      expect(container.querySelectorAll(".start-collection-item").length).toBeGreaterThanOrEqual(6);
    });

    const rowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
    expect(rowFive).not.toBeUndefined();
    await fireEvent.click(rowFive);

    await waitFor(() => {
      const updatedRowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
      expect(updatedRowFive?.textContent || "").toContain("ready");
      expect(updatedRowFive?.textContent || "").toContain("0.88");
    });

    vi.advanceTimersByTime(1200);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock.mock.calls.length).toBeGreaterThanOrEqual(2);
      const updatedRowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
      expect(updatedRowFive?.textContent || "").toContain("ready");
      expect(updatedRowFive?.textContent || "").toContain("0.88");
      expect(updatedRowFive?.getAttribute("title") || "").toContain("[5] (number) · ready");
    });

    vi.useRealTimers();
  });

  it("clicking a pending child resolves it once and then opens its nested page without extra path churn", async () => {
    const pathCounts = new Map();
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 8 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      const nextCount = Number(pathCounts.get(path) || 0) + 1;
      pathCounts.set(path, nextCount);
      if (path === "/5") {
        return {
          materialization: nextCount >= 2 ? "computed" : "pending",
          compute_status: nextCount >= 2 ? "completed" : "queued",
          node_id: "node-xs",
          path: "/5",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 2 },
            navigation: {
              path: "/5",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "unavailable",
          format_version: "voxpod/1",
          summary: { reason: "status=pending" },
          navigation: {
            path,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });
    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "pending",
          compute_status: "running",
          path: "/",
          page: {
            offset: 0,
            limit: 18,
            has_more: false,
            next_offset: null,
            items: Array.from({ length: 8 }, (_, index) => ({
              index,
              label: `[${index}]`,
              path: `/${index}`,
              status: "pending",
              descriptor: {
                vox_type: "unavailable",
                format_version: "voxpod/1",
                summary: { reason: "status=pending" },
                navigation: {
                  path: `/${index}`,
                  pageable: false,
                  can_descend: false,
                  default_page_size: 64,
                  max_page_size: 512,
                },
              },
            })),
          },
        };
      }
      if (path === "/5") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/5",
          page: {
            offset: 0,
            limit: 18,
            has_more: false,
            next_offset: null,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/5/0",
                status: "ready",
                state: "ready",
                descriptor: {
                  vox_type: "number",
                  format_version: "voxpod/1",
                  summary: { value: 1 },
                  navigation: {
                    path: "/5/0",
                    pageable: false,
                    can_descend: false,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
            ],
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        path,
        page: {
          offset: 0,
          limit: 18,
          has_more: false,
          next_offset: null,
          items: [],
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    expect(pathCounts.get("/5") || 0).toBe(0);

    const rowFive = () => Array.from(container.querySelectorAll(".start-collection-item"))[5];
    await waitFor(() => {
      expect(rowFive()).not.toBeUndefined();
    });
    await fireEvent.click(rowFive());

    await waitFor(() => {
      expect(pathCounts.get("/5") || 0).toBeGreaterThanOrEqual(1);
    });
    await waitFor(() => {
      expect(container.textContent).toContain("[0]");
      expect(container.textContent).toContain("1");
    });
  });

  it("clears the stage immediately when switching from a resolved item to an unresolved item", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 8 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/3") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/3",
          descriptor: {
            vox_type: "number",
            format_version: "voxpod/1",
            summary: { value: 3.14159 },
            navigation: {
              path: "/3",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      if (path === "/5") {
        return {
          materialization: "pending",
          compute_status: "running",
          node_id: "node-xs",
          path: "/5",
          descriptor: {
            vox_type: "unavailable",
            format_version: "voxpod/1",
            summary: { reason: "status=pending" },
            navigation: {
              path: "/5",
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "unavailable",
          format_version: "voxpod/1",
          summary: { reason: "status=pending" },
          navigation: {
            path,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "pending",
      compute_status: "running",
      path: "/",
      page: {
        offset: 0,
        limit: 18,
        has_more: false,
        next_offset: null,
        items: Array.from({ length: 8 }, (_, index) => ({
          index,
          label: `[${index}]`,
          path: `/${index}`,
          status: "pending",
          descriptor: {
            vox_type: "unavailable",
            format_version: "voxpod/1",
            summary: { reason: "status=pending" },
            navigation: {
              path: `/${index}`,
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        })),
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(container.querySelectorAll(".start-collection-item").length).toBeGreaterThanOrEqual(6);
    });

    const rowThree = Array.from(container.querySelectorAll(".start-collection-item"))[3];
    const rowFive = Array.from(container.querySelectorAll(".start-collection-item"))[5];
    expect(rowThree).not.toBeUndefined();
    expect(rowFive).not.toBeUndefined();

    await fireEvent.click(rowThree);
    await waitFor(() => {
      const stageScalar = container.querySelector(".start-collection-stage .start-pure-scalar");
      expect(stageScalar?.textContent || "").toContain("3.14159");
    });

    await fireEvent.click(rowFive);
    await waitFor(() => {
      const stageScalar = container.querySelector(".start-collection-stage .start-pure-scalar");
      expect(stageScalar?.textContent || "").not.toContain("3.14159");
      expect(container.querySelector(".start-collection-stage .start-pure-array")?.textContent || "").toContain("status=pending");
    });
  });

  it("renders nested sequence pages for ready sequence-of-sequences items", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path) {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 3 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      const tokens = String(path).split("/").filter(Boolean);
      if (tokens.length >= 2) {
        const outerIndex = Number(tokens[0] || 0);
        const innerIndex = Number(tokens[1] || 0);
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path,
          descriptor: {
            vox_type: "integer",
            format_version: "voxpod/1",
            summary: { value: 1200 + outerIndex * 10 + innerIndex + 1 },
            navigation: {
              path,
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "computed",
        compute_status: "completed",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "sequence",
          format_version: "voxpod/1",
          summary: { length: null },
          navigation: {
            path,
            pageable: true,
            can_descend: true,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });
    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 3,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                status: "materialized",
                descriptor: {
                  vox_type: "unavailable",
                  format_version: "voxpod/1",
                  summary: { reason: "queued" },
                  navigation: { path: "/0", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/1",
                status: "materialized",
                descriptor: {
                  vox_type: "unavailable",
                  format_version: "voxpod/1",
                  summary: { reason: "queued" },
                  navigation: { path: "/1", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
                },
              },
              {
                index: 2,
                label: "[2]",
                path: "/2",
                status: "materialized",
                descriptor: {
                  vox_type: "unavailable",
                  format_version: "voxpod/1",
                  summary: { reason: "queued" },
                  navigation: { path: "/2", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
                },
              },
            ],
          },
        };
      }

      const pathIndex = Number(String(path).split("/").filter(Boolean)[0] || 0);
      const base = 700 + pathIndex * 100;
      return {
        materialization: "computed",
        compute_status: "completed",
        path,
        page: {
          offset,
          limit,
          has_more: false,
          next_offset: null,
          total: 2,
          items: [
            {
              index: 0,
              label: "[0]",
              path: `${path}/0`,
              status: "materialized",
              descriptor: {
                vox_type: "integer",
                format_version: "voxpod/1",
                summary: { value: base + 1 },
                navigation: { path: `${path}/0`, pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
              },
            },
            {
              index: 1,
              label: "[1]",
              path: `${path}/1`,
              status: "materialized",
              descriptor: {
                vox_type: "integer",
                format_version: "voxpod/1",
                summary: { value: base + 2 },
                navigation: { path: `${path}/1`, pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
              },
            },
          ],
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalled();
      expect(container.textContent).toContain("701");
    });

    const collectionButtons = Array.from(container.querySelectorAll(".start-collection-item"));
    const outerThird = collectionButtons.find((button) => (button.textContent || "").includes("[2]"));
    expect(outerThird).not.toBeUndefined();
    await fireEvent.click(outerThird);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock.mock.calls.some(([payload]) => payload?.path === "/2")).toBe(true);
      expect(container.textContent).toContain("901");
      expect(container.textContent).not.toContain("No values yet");
    });
  });

  it("reuses inline nested runtime preview pages from value resolves", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path) {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 3 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      const tokens = String(path).split("/").filter(Boolean);
      if (tokens.length >= 2) {
        const outerIndex = Number(tokens[0] || 0);
        const innerIndex = Number(tokens[1] || 0);
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path,
          descriptor: {
            vox_type: "integer",
            format_version: "voxpod/1",
            summary: { value: 1200 + outerIndex * 10 + innerIndex + 1 },
            navigation: {
              path,
              pageable: false,
              can_descend: false,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "computed",
        compute_status: "completed",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "sequence",
          format_version: "voxpod/1",
          summary: { length: null },
          navigation: {
            path,
            pageable: true,
            can_descend: true,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
        runtime_preview_page: {
          offset: 0,
          limit: 18,
          has_more: false,
          next_offset: null,
          total: 2,
          items: [
            {
              index: 0,
              label: "[0]",
              path: `${path}/0`,
              status: "materialized",
              descriptor: {
                vox_type: "integer",
                format_version: "voxpod/1",
                summary: { value: 1201 },
                navigation: { path: `${path}/0`, pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
              },
            },
            {
              index: 1,
              label: "[1]",
              path: `${path}/1`,
              status: "materialized",
              descriptor: {
                vox_type: "integer",
                format_version: "voxpod/1",
                summary: { value: 1202 },
                navigation: { path: `${path}/1`, pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
              },
            },
          ],
        },
      };
    });
    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 3,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                status: "materialized",
                descriptor: {
                  vox_type: "unavailable",
                  format_version: "voxpod/1",
                  summary: { reason: "queued" },
                  navigation: { path: "/0", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/1",
                status: "materialized",
                descriptor: {
                  vox_type: "unavailable",
                  format_version: "voxpod/1",
                  summary: { reason: "queued" },
                  navigation: { path: "/1", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
                },
              },
              {
                index: 2,
                label: "[2]",
                path: "/2",
                status: "materialized",
                descriptor: {
                  vox_type: "unavailable",
                  format_version: "voxpod/1",
                  summary: { reason: "queued" },
                  navigation: { path: "/2", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
                },
              },
            ],
          },
        };
      }
      return {
        materialization: "computed",
        compute_status: "completed",
        path,
        page: {
          offset,
          limit,
          has_more: false,
          next_offset: null,
          total: 2,
          items: [
            {
              index: 0,
              label: "[0]",
              path: `${path}/0`,
              status: "materialized",
              descriptor: {
                vox_type: "integer",
                format_version: "voxpod/1",
                summary: { value: 1301 },
                navigation: { path: `${path}/0`, pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
              },
            },
            {
              index: 1,
              label: "[1]",
              path: `${path}/1`,
              status: "materialized",
              descriptor: {
                vox_type: "integer",
                format_version: "voxpod/1",
                summary: { value: 1302 },
                navigation: { path: `${path}/1`, pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
              },
            },
          ],
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    let outerThird;
    await waitFor(() => {
      const collectionButtons = Array.from(container.querySelectorAll(".start-prime-results .start-collection-item"));
      outerThird = collectionButtons.find((button) => (button.textContent || "").includes("[2]"));
      expect(outerThird).not.toBeUndefined();
    });
    await fireEvent.click(outerThird);

    await waitFor(() => {
      expect(container.textContent).toContain("1201");
      expect(resolvePlaygroundValuePageMock.mock.calls.some(([payload]) => payload?.path === "/2")).toBe(false);
    });
  });

  it("opens nested collection pages directly from page snapshots without child path resolve churn", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockImplementation(async ({ path = "" }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          node_id: "node-xs",
          path: "/",
          descriptor: {
            vox_type: "sequence",
            format_version: "voxpod/1",
            summary: { length: 2 },
            navigation: {
              path: "/",
              pageable: true,
              can_descend: true,
              default_page_size: 64,
              max_page_size: 512,
            },
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "queued",
        node_id: "node-xs",
        path,
        descriptor: {
          vox_type: "unavailable",
          format_version: "voxpod/1",
          summary: { state: "queued" },
          navigation: {
            path,
            pageable: false,
            can_descend: false,
            default_page_size: 64,
            max_page_size: 512,
          },
        },
      };
    });
    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 2,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                node_id: "child-0",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 2 },
                  navigation: {
                    path: "/0",
                    pageable: true,
                    can_descend: true,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/1",
                node_id: "child-1",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 2 },
                  navigation: {
                    path: "/1",
                    pageable: true,
                    can_descend: true,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
            ],
          },
        };
      }
      if (path === "/0") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/0",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 2,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0/0",
                status: "materialized",
                descriptor: {
                  vox_type: "integer",
                  format_version: "voxpod/1",
                  summary: { value: 901 },
                  navigation: {
                    path: "/0/0",
                    pageable: false,
                    can_descend: false,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/0/1",
                status: "materialized",
                descriptor: {
                  vox_type: "integer",
                  format_version: "voxpod/1",
                  summary: { value: 902 },
                  navigation: {
                    path: "/0/1",
                    pageable: false,
                    can_descend: false,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
            ],
          },
        };
      }
      return {
        materialization: "computed",
        compute_status: "completed",
        path,
        page: {
          offset,
          limit,
          has_more: false,
          next_offset: null,
          total: 0,
          items: [],
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(container.textContent).toContain("901");
      expect(container.textContent).not.toContain("No values yet");
    });

    const nestedValueCalls = resolvePlaygroundValueMock.mock.calls.filter(([payload]) => payload?.path === "/0");
    expect(nestedValueCalls).toHaveLength(0);
    const nestedPageCalls = resolvePlaygroundValuePageMock.mock.calls.filter(([payload]) => payload?.path === "/0");
    expect(nestedPageCalls.length).toBeGreaterThan(0);
  });

  it("keeps nested pending collection pages in a loading state instead of showing an empty terminal message", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-xs",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 2 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 2,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                node_id: "child-0",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 9 },
                  navigation: {
                    path: "/0",
                    pageable: true,
                    can_descend: true,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/1",
                node_id: "child-1",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 9 },
                  navigation: {
                    path: "/1",
                    pageable: true,
                    can_descend: true,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
            ],
          },
        };
      }
      if (path === "/0") {
        return {
          materialization: "pending",
          compute_status: "running",
          path: "/0",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 9,
            items: [],
          },
        };
      }
      return {
        materialization: "pending",
        compute_status: "running",
        path,
        page: {
          offset,
          limit,
          has_more: false,
          next_offset: null,
          total: 0,
          items: [],
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock.mock.calls.some(([payload]) => payload?.path === "/0")).toBe(true);
    });

    await waitFor(() => {
      expect(container.querySelector(".start-collection-stage-loading")).not.toBeNull();
      expect(container.textContent).not.toContain("No values yet");
    });
  });

  it("renders cached nested overlays directly from page snapshots without waiting on a child resolve", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-xs",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 2 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockImplementation(async ({ path = "", offset = 0, limit = 64 }) => {
      if (!path || path === "/") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 2,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0",
                node_id: "child-0",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 2 },
                  navigation: {
                    path: "/0",
                    pageable: true,
                    can_descend: true,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/1",
                node_id: "child-1",
                status: "materialized",
                descriptor: {
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: 2 },
                  navigation: {
                    path: "/1",
                    pageable: true,
                    can_descend: true,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
            ],
          },
        };
      }
      if (path === "/0") {
        return {
          materialization: "computed",
          compute_status: "completed",
          path: "/0",
          page: {
            offset,
            limit,
            has_more: false,
            next_offset: null,
            total: 2,
            items: [
              {
                index: 0,
                label: "[0]",
                path: "/0/0",
                status: "materialized",
                descriptor: {
                  vox_type: "overlay",
                  format_version: "voxpod/1",
                  summary: { layer_count: 2, layer_labels: ["Base", "Mask"] },
                  render: {
                    kind: "image-overlay",
                    layers: [
                      { label: "Base", png_url: "/base.png", opacity: 1.0, visible: true },
                      { label: "Mask", png_url: "/mask.png", opacity: 0.42, visible: true },
                    ],
                  },
                  navigation: {
                    path: "/0/0",
                    pageable: false,
                    can_descend: false,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
              {
                index: 1,
                label: "[1]",
                path: "/0/1",
                status: "materialized",
                descriptor: {
                  vox_type: "overlay",
                  format_version: "voxpod/1",
                  summary: { layer_count: 2, layer_labels: ["Base", "Mask"] },
                  render: {
                    kind: "image-overlay",
                    layers: [
                      { label: "Base", png_url: "/base-2.png", opacity: 1.0, visible: true },
                      { label: "Mask", png_url: "/mask-2.png", opacity: 0.42, visible: true },
                    ],
                  },
                  navigation: {
                    path: "/0/1",
                    pageable: false,
                    can_descend: false,
                    default_page_size: 64,
                    max_page_size: 512,
                  },
                },
              },
            ],
          },
        };
      }
      return {
        materialization: "computed",
        compute_status: "completed",
        path,
        page: {
          offset,
          limit,
          has_more: false,
          next_offset: null,
          total: 0,
          items: [],
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(container.querySelectorAll(".start-overlay-image-layer").length).toBe(2);
      expect(container.textContent).not.toContain("No values yet");
    });

    const nestedLeafCalls = resolvePlaygroundValueMock.mock.calls.filter(([payload]) => payload?.path === "/0/0");
    expect(nestedLeafCalls).toHaveLength(0);
  });

  it("keeps collection navigation visible when page resolution fails and shows the concrete error", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { xs: "node-xs" },
      diagnostics: [],
    });
    resolvePlaygroundValueMock.mockResolvedValue({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-xs",
      path: "/",
      descriptor: {
        vox_type: "sequence",
        format_version: "voxpod/1",
        summary: { length: 3 },
        navigation: {
          path: "/",
          pageable: true,
          can_descend: true,
          default_page_size: 64,
          max_page_size: 512,
        },
      },
    });
    resolvePlaygroundValuePageMock.mockResolvedValue({
      materialization: "failed",
      compute_status: "failed",
      node_id: "node-xs",
      path: "/",
      error: "Unknown primitive: pflair",
      diagnostics: {
        code: "E_RUNTIME_INSPECTION",
        message: "Unknown primitive: pflair",
        node_id: "node-xs",
        path: "/",
      },
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    await waitFor(() => {
      expect(resolvePlaygroundValuePageMock).toHaveBeenCalled();
      const collectionButtons = Array.from(container.querySelectorAll(".start-collection-item"));
      expect(collectionButtons.length).toBeGreaterThanOrEqual(3);
      expect(container.textContent).toContain("Unknown primitive: pflair");
    });
  });

  it("ignores stale resolve responses after switching variable tags", async () => {
    vi.useFakeTimers();
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { a: "node-a", b: "node-b" },
      diagnostics: [],
    });
    const aDeferred = deferred();
    const bDeferred = deferred();
    resolvePlaygroundValueMock.mockImplementation(async ({ variable }) => {
      if (variable === "a") return aDeferred.promise;
      if (variable === "b") return bDeferred.promise;
      return {
        materialization: "computed",
        compute_status: "completed",
        node_id: "node-x",
        path: "/",
        descriptor: {
          vox_type: "integer",
          format_version: "voxpod/1",
          summary: { value: 0 },
          navigation: { path: "/", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
        },
      };
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(container.querySelectorAll("button.start-value-tag").length).toBe(2);
    });
    const valueTags = Array.from(container.querySelectorAll("button.start-value-tag"));
    const aTag = valueTags.find((el) => (el.textContent || "").includes("a"));
    const bTag = valueTags.find((el) => (el.textContent || "").includes("b"));
    expect(aTag).not.toBeUndefined();
    expect(bTag).not.toBeUndefined();

    await fireEvent.click(aTag);
    await fireEvent.click(bTag);

    bDeferred.resolve({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-b",
      path: "/",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: 2 },
        navigation: { path: "/", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
      },
    });
    await waitFor(() => {
      const caption = container.querySelector(".start-caption-main");
      expect(caption?.textContent || "").toContain("b");
      expect(container.textContent).toContain("2");
    });

    aDeferred.resolve({
      materialization: "computed",
      compute_status: "completed",
      node_id: "node-a",
      path: "/",
      descriptor: {
        vox_type: "integer",
        format_version: "voxpod/1",
        summary: { value: 1 },
        navigation: { path: "/", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
      },
    });
    await Promise.resolve();
    await Promise.resolve();

    const caption = container.querySelector(".start-caption-main");
    expect(caption?.textContent || "").toContain("b");
    expect(container.textContent).toContain("2");
    vi.useRealTimers();
  });
});
