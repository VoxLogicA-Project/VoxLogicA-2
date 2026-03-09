import { fireEvent, render, waitFor } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

import StartTab from "./StartTab.svelte";

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
  beforeEach(() => {
    window.localStorage.clear();
    getProgramSymbolsMock.mockReset();
    resolvePlaygroundValueMock.mockReset();
    resolvePlaygroundValuePageMock.mockReset();
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

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(getProgramSymbolsMock).toHaveBeenCalled();
    });
    expect(resolvePlaygroundValueMock).not.toHaveBeenCalled();

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    const latest = resolvePlaygroundValueMock.mock.calls.at(-1)?.[0];
    expect(latest?.variable).toBe("b");
    await waitFor(() => {
      const captionName = container.querySelector(".start-caption-main");
      expect(captionName?.textContent || "").toContain("b");
      expect(container.textContent).toContain("2");
    });
  });

  it("persists edited code in localStorage", async () => {
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

    const { container } = render(StartTab, { active: true, capabilities: {} });
    const editor = container.querySelector(".vx-editor__input");
    expect(editor).not.toBeNull();

    await fireEvent.input(editor, { target: { value: "x = 3" } });
    vi.advanceTimersByTime(220);
    await waitFor(() => {
      expect(window.localStorage.getItem("voxlogica.start.program.v1")).toBe("x = 3");
    });

    vi.useRealTimers();
  });

  it("surfaces static diagnostics and blocks value resolve", async () => {
    getProgramSymbolsMock.mockResolvedValue({
      available: true,
      symbol_table: { x: "node-x" },
      diagnostics: [{ code: "E_PARSE", message: "Unexpected token", location: "line 1, column 1" }],
    });

    const { container } = render(StartTab, { active: true, capabilities: {} });
    await waitFor(() => {
      expect(container.textContent).toContain("Line 1:1 - Unexpected token");
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    expect(resolvePlaygroundValueMock).not.toHaveBeenCalled();
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

  it("re-resolves a clicked pending child even when a cached pending descriptor already exists", async () => {
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

    await waitFor(() => {
      expect(pathCounts.get("/5") || 0).toBeGreaterThanOrEqual(1);
    });

    const rowFive = () => Array.from(container.querySelectorAll(".start-collection-item"))[5];
    await waitFor(() => {
      expect(rowFive()).not.toBeUndefined();
    });
    await fireEvent.click(rowFive());

    await waitFor(() => {
      expect(pathCounts.get("/5") || 0).toBeGreaterThanOrEqual(2);
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
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: null },
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
                  summary: { length: null },
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
                  summary: { length: null },
                  navigation: { path: "/2", pageable: true, can_descend: true, default_page_size: 64, max_page_size: 512 },
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
                  vox_type: "sequence",
                  format_version: "voxpod/1",
                  summary: { length: null },
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
                  summary: { length: null },
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
                  summary: { length: null },
                  navigation: { path: "/2", pageable: true, can_descend: true, default_page_size: 64, max_page_size: 512 },
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

    await waitFor(() => {
      expect(container.textContent).toContain("collection");
    });

    const collectionButtons = Array.from(container.querySelectorAll(".start-collection-item"));
    const outerThird = collectionButtons.find((button) => (button.textContent || "").includes("[2]"));
    expect(outerThird).not.toBeUndefined();
    await fireEvent.click(outerThird);

    await waitFor(() => {
      expect(container.textContent).toContain("1221");
      expect(resolvePlaygroundValuePageMock.mock.calls.some(([payload]) => payload?.path === "/2")).toBe(false);
    });
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
