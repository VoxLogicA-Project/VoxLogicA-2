import { fireEvent, render, waitFor } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

import StartTab from "./StartTab.svelte";

const getProgramSymbolsMock = vi.fn();
const resolvePlaygroundValueMock = vi.fn();
const resolvePlaygroundValuePageMock = vi.fn();

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
      const captionType = container.querySelector(".start-caption-type");
      expect(captionName?.textContent || "").toContain("b");
      expect(captionType?.textContent || "").toContain("integer");
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
      expect(container.textContent).toContain("Static diagnostics detected.");
    });

    const runButton = container.querySelector(".btn.btn-primary");
    expect(runButton).not.toBeNull();
    await fireEvent.click(runButton);

    expect(resolvePlaygroundValueMock).not.toHaveBeenCalled();
  });

  it("shows pending per-job logs while value is resolving", async () => {
    vi.useFakeTimers();
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
      expect(container.textContent).toContain("Resolution log");
      expect(container.textContent).toContain("job job-12345678");
      expect(container.textContent).toContain("const · cached");
      expect(container.textContent).toContain("playground.node");
    });

    vi.runOnlyPendingTimers();
    await waitFor(() => {
      expect(container.textContent).not.toContain("Resolution log");
    });
    vi.useRealTimers();
  });

  it("uses non-enqueue paging requests from the viewer", async () => {
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

    let fetchPage = null;
    const previousViewer = window.VoxResultViewer;
    window.VoxResultViewer = {
      ResultViewer: class {
        constructor(_element, options) {
          fetchPage = options.fetchPage;
        }

        setLoading() {}
        setError() {}
        renderRecord() {}
        destroy() {}
      },
    };

    try {
      render(StartTab, { active: true, capabilities: {} });
      await waitFor(() => {
        expect(typeof fetchPage).toBe("function");
      });

      await fetchPage({ nodeId: "node-x", path: "/", offset: 0, limit: 8 });

      const latest = resolvePlaygroundValuePageMock.mock.calls.at(-1)?.[0];
      expect(latest?.enqueue).toBe(false);
    } finally {
      window.VoxResultViewer = previousViewer;
    }
  });
});
