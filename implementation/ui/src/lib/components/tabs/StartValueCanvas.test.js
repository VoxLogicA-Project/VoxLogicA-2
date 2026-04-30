import { render, waitFor } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StartValueCanvas from "./StartValueCanvas.svelte";

const baseNavigation = {
  path: "/",
  pageable: false,
  can_descend: false,
  default_page_size: 64,
  max_page_size: 512,
};

const overlayRecord = (baseUrl, maskUrl) => ({
  node_id: "overlay-node",
  path: "/",
  descriptor: {
    vox_type: "overlay",
    format_version: "voxpod/1",
    summary: { layer_count: 2 },
    render: {
      kind: "image-overlay",
      layers: [
        { label: "Base", png_url: baseUrl, opacity: 1, visible: true },
        { label: "Mask", png_url: maskUrl, opacity: 0.42, visible: true },
      ],
    },
    navigation: baseNavigation,
  },
});

const medicalVolumeRecord = (url) => ({
  node_id: "volume-node",
  path: "/",
  descriptor: {
    vox_type: "volume3d",
    format_version: "voxpod/1",
    summary: { size: [16, 16, 16] },
    render: {
      kind: "medical-volume",
      nifti_url: url,
    },
    navigation: baseNavigation,
  },
});

const medicalOverlayRecord = (baseUrl, overlayUrl) => ({
  node_id: "overlay-volume-node",
  path: "/",
  descriptor: {
    vox_type: "overlay",
    format_version: "voxpod/1",
    summary: { layer_count: 2 },
    render: {
      kind: "medical-overlay",
      layers: [
        { label: "Base", nifti_url: baseUrl, opacity: 1, visible: true },
        { label: "Overlay", nifti_url: overlayUrl, opacity: 0.42, visible: true },
      ],
    },
    navigation: baseNavigation,
  },
});

describe("StartValueCanvas", () => {
  let niivueInstances = [];
  let resizeObserverInstances = [];

  beforeEach(() => {
    niivueInstances = [];
    resizeObserverInstances = [];
    window.niivue = undefined;
    globalThis.ResizeObserver = class MockResizeObserver {
      constructor(callback) {
        this.callback = callback;
        this.observe = vi.fn();
        this.disconnect = vi.fn();
        resizeObserverInstances.push(this);
      }
    };
  });

  afterEach(() => {
    delete window.niivue;
    delete globalThis.ResizeObserver;
  });

  it("shows an explicit empty-range message for empty collections", async () => {
    const { getAllByText, getByText, queryByText } = render(StartValueCanvas, {
      record: {
        node_id: "empty-seq",
        path: "/",
        descriptor: {
          vox_type: "sequence",
          summary: { length: 0 },
          navigation: { ...baseNavigation, pageable: true },
        },
      },
      label: "Flair paths",
      sourceVariable: "flair_paths",
      collectionRecord: () => true,
      pageForRecord: () => ({ offset: 0, limit: 18, items: [], has_more: false, next_offset: null }),
      collectionItemsForPage: (page) => page.items || [],
      collectionSelectionFor: () => ({ selectedIndex: 0, selectedAbsoluteIndex: 0, selectedPath: "" }),
    });

    expect(getAllByText("This range has no values.")).toHaveLength(2);
    expect(getByText("Empty")).toBeTruthy();
    expect(queryByText("No selected value")).toBeNull();
    expect(queryByText("0-0")).toBeNull();
  });

  it("morphs image overlays in place without replacing the DOM host", async () => {
    const { container, rerender } = render(StartValueCanvas, {
      record: overlayRecord("/base-a.png", "/mask-a.png"),
      label: "Overlay",
    });

    const shellBefore = container.querySelector(".start-overlay-image-shell");
    expect(shellBefore).not.toBeNull();
    expect(shellBefore?.querySelectorAll("img")[0]?.getAttribute("src")).toBe("/base-a.png");

    await rerender({
      record: overlayRecord("/base-b.png", "/mask-b.png"),
    });

    await waitFor(() => {
      const shellAfter = container.querySelector(".start-overlay-image-shell");
      expect(shellAfter).toBe(shellBefore);
      expect(shellAfter?.querySelectorAll("img")[0]?.getAttribute("src")).toBe("/base-b.png");
      expect(shellAfter?.querySelectorAll("img")[1]?.getAttribute("src")).toBe("/mask-b.png");
    });
  });

  it("reuses the niivue instance when morphing between medical viewer contracts", async () => {
    class MockNiivue {
      constructor() {
        this.attachToCanvas = vi.fn(async () => {});
        this.loadVolumes = vi.fn(async (volumes) => {
          this.volumes = volumes.map(() => ({
            global_min: 0,
            global_max: 1,
            cal_min: 0,
            cal_max: 1,
          }));
        });
        this.setOpacity = vi.fn();
        this.setColormap = vi.fn();
        this.setSliceType = vi.fn();
        this.updateGLVolume = vi.fn();
        this.drawScene = vi.fn();
        this.destroy = vi.fn();
        this.scene = { crosshairPos: [0.5, 0.5, 0.5] };
        this.volumes = [];
        niivueInstances.push(this);
      }
    }

    window.niivue = {
      Niivue: MockNiivue,
      SLICE_TYPE: {
        MULTIPLANAR: "MULTIPLANAR",
      },
    };

    const { container, rerender, unmount } = render(StartValueCanvas, {
      record: medicalVolumeRecord("/base-a.nii.gz"),
      label: "Medical",
    });

    await waitFor(() => {
      expect(niivueInstances).toHaveLength(1);
      expect(niivueInstances[0].loadVolumes).toHaveBeenCalledTimes(1);
      expect(resizeObserverInstances).toHaveLength(1);
      expect(resizeObserverInstances[0].observe).toHaveBeenCalled();
    });

    const shellBefore = container.querySelector(".start-medical-volume");
    const instance = niivueInstances[0];

    await rerender({
      record: medicalOverlayRecord("/base-b.nii.gz", "/overlay-b.nii.gz"),
    });

    await waitFor(() => {
      const shellAfter = container.querySelector(".start-medical-volume");
      expect(shellAfter).toBe(shellBefore);
      expect(niivueInstances).toHaveLength(1);
      expect(instance.destroy).not.toHaveBeenCalled();
      expect(instance.loadVolumes).toHaveBeenCalledTimes(2);
      expect(instance.loadVolumes.mock.calls[1]?.[0]).toHaveLength(2);
    });

    unmount();
    expect(resizeObserverInstances[0].disconnect).toHaveBeenCalled();
  });

  it("keeps medical volumes in a loading state through a transient first-load 404 and then recovers", async () => {
    class MockNiivue {
      constructor() {
        this.attachToCanvas = vi.fn(async () => {});
        this.loadVolumes = vi
          .fn()
          .mockRejectedValueOnce(new Error("404 Not Found"))
          .mockImplementation(async (volumes) => {
            this.volumes = volumes.map(() => ({
              global_min: 0,
              global_max: 1,
              cal_min: 0,
              cal_max: 1,
            }));
          });
        this.setOpacity = vi.fn();
        this.setColormap = vi.fn();
        this.setSliceType = vi.fn();
        this.updateGLVolume = vi.fn();
        this.drawScene = vi.fn();
        this.destroy = vi.fn();
        this.scene = { crosshairPos: [0.5, 0.5, 0.5] };
        this.volumes = [];
        niivueInstances.push(this);
      }
    }

    window.niivue = {
      Niivue: MockNiivue,
      SLICE_TYPE: {
        MULTIPLANAR: "MULTIPLANAR",
      },
    };

    const { container } = render(StartValueCanvas, {
      record: medicalVolumeRecord("/base-racy.nii"),
      label: "Medical",
    });

    const loadingEl = container.querySelector(".start-medical-volume-loading");
    const errorEl = container.querySelector(".start-medical-volume-error");

    await waitFor(() => {
      expect(niivueInstances).toHaveLength(1);
      expect(niivueInstances[0].loadVolumes).toHaveBeenCalledTimes(2);
      expect(errorEl?.textContent || "").toBe("");
    });

    expect(loadingEl).not.toBeNull();
    expect(errorEl).not.toBeNull();
    expect(errorEl?.style.display).not.toBe("block");
  });
});
