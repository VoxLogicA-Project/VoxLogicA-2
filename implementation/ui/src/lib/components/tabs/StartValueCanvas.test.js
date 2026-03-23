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

  beforeEach(() => {
    niivueInstances = [];
    window.niivue = undefined;
  });

  afterEach(() => {
    delete window.niivue;
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

    const { container, rerender } = render(StartValueCanvas, {
      record: medicalVolumeRecord("/base-a.nii.gz"),
      label: "Medical",
    });

    await waitFor(() => {
      expect(niivueInstances).toHaveLength(1);
      expect(niivueInstances[0].loadVolumes).toHaveBeenCalledTimes(1);
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
  });
});