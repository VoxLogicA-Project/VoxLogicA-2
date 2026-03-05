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

  it("polls pending values until completion", async () => {
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
      expect(container.textContent).toContain("Computing x");
    });

    vi.runOnlyPendingTimers();
    await waitFor(() => {
      expect(resolvePlaygroundValueMock.mock.calls.length).toBeGreaterThanOrEqual(2);
      const chip = container.querySelector(".chip");
      expect(chip?.textContent || "").toContain("completed");
    });
    vi.useRealTimers();
  });

  it("renders value tags with status/materialization and resolves clicked tags", async () => {
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
    expect(aTag?.textContent || "").toContain("idle");
    expect(aTag?.textContent || "").toContain("unresolved");

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
