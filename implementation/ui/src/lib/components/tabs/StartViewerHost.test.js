import { cleanup, render, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import StartViewerHost from "./StartViewerHost.svelte";
import { buildRecordViewerContract } from "./viewers/viewerContracts.js";

afterEach(() => {
  cleanup();
  delete window.VoxResultViewer;
});

describe("StartViewerHost", () => {
  it("reuses the external record viewer instance across contract updates", async () => {
    const viewerApi = {
      setLoading: vi.fn(),
      setError: vi.fn(),
      renderRecord: vi.fn(),
      refreshPage: vi.fn(),
      destroy: vi.fn(),
    };
    const ctor = vi.fn((host, callbacks) => {
      viewerApi.callbacks = callbacks;
      return viewerApi;
    });
    window.VoxResultViewer = { ResultViewer: ctor };

    const onNavigate = vi.fn();
    const fetchPage = vi.fn();
    const onStatusClick = vi.fn();
    const record = {
      node_id: "node-1",
      status: "materialized",
      path: "/",
      descriptor: {
        vox_type: "integer",
        summary: { value: 7 },
        navigation: { path: "/", pageable: false, can_descend: false, default_page_size: 64, max_page_size: 512 },
      },
    };

    const { rerender } = render(StartViewerHost, {
      contract: buildRecordViewerContract({
        label: "viewer",
        state: "loading",
        message: "Loading node-1",
        onNavigate,
        fetchPage,
        onStatusClick,
      }),
    });

    await waitFor(() => {
      expect(ctor).toHaveBeenCalledTimes(1);
      expect(viewerApi.setLoading).toHaveBeenCalledWith("Loading node-1");
    });

    await rerender({
      contract: buildRecordViewerContract({
        label: "viewer",
        state: "record",
        record,
        onNavigate,
        fetchPage,
        onStatusClick,
      }),
    });

    await waitFor(() => {
      expect(ctor).toHaveBeenCalledTimes(1);
      expect(viewerApi.renderRecord).toHaveBeenCalledWith(record);
    });

    viewerApi.callbacks.onNavigate("/0");
    expect(onNavigate).toHaveBeenCalledWith("/0");

    await rerender({
      contract: buildRecordViewerContract({
        label: "viewer",
        state: "record",
        record,
        pageRefresh: { nodeId: "node-1", path: "/0", preserveRecord: true },
        onNavigate,
        fetchPage,
        onStatusClick,
      }),
    });

    await waitFor(() => {
      expect(viewerApi.refreshPage).toHaveBeenCalledWith("node-1", "/0");
      expect(ctor).toHaveBeenCalledTimes(1);
    });
  });
});