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
    vi.useRealTimers();
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
            { index: 0, label: "[0]", path: "/0", status: "pending", descriptor: { vox_type: "unavailable", summary: {} } },
            { index: 1, label: "[1]", path: "/1", status: "pending", descriptor: { vox_type: "unavailable", summary: {} } },
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
