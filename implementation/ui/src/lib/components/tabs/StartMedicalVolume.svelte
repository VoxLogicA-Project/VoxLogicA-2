<script>
  import { onDestroy } from "svelte";

  export let niftiUrl = "";
  export let label = "value";

  let canvasEl = null;
  let loading = false;
  let errorMessage = "";
  let mountSeq = 0;
  let niivueInstance = null;

  const sleep = (ms) =>
    new Promise((resolve) => {
      window.setTimeout(resolve, ms);
    });

  const withTimeout = (promise, ms, labelText) =>
    Promise.race([
      promise,
      new Promise((_, reject) => {
        window.setTimeout(() => reject(new Error(`${labelText} timed out after ${ms} ms`)), ms);
      }),
    ]);

  const withCacheBuster = (url) => {
    if (!url) return "";
    const separator = String(url).includes("?") ? "&" : "?";
    return `${url}${separator}_=${Date.now()}`;
  };

  const candidateUrlsFor = (url) => {
    const base = withCacheBuster(url);
    const variants = [base];
    if (base.includes("/render/nii?")) {
      variants.push(base.replace("/render/nii?", "/render/nii.gz?"));
    } else if (base.endsWith("/render/nii")) {
      variants.push(`${base}.gz`);
    }
    return variants;
  };

  const destroyNiivue = () => {
    const current = niivueInstance;
    niivueInstance = null;
    if (!current) return;
    if (typeof current.destroy === "function") {
      try {
        current.destroy();
      } catch {
        // No-op: best effort cleanup.
      }
    }
  };

  const syncCanvasSize = () => {
    if (!canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width || canvasEl.clientWidth || 720));
    const height = Math.max(1, Math.round(rect.height || canvasEl.clientHeight || 420));
    const dpr = Number.isFinite(window.devicePixelRatio) && window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
    canvasEl.style.width = `${width}px`;
    canvasEl.style.height = `${height}px`;
    canvasEl.width = Math.max(1, Math.round(width * dpr));
    canvasEl.height = Math.max(1, Math.round(height * dpr));
  };

  const waitForConnectedCanvas = async (token) => {
    for (let attempt = 0; attempt < 48; attempt += 1) {
      if (token !== mountSeq) return false;
      if (canvasEl && canvasEl.isConnected) {
        const rect = canvasEl.getBoundingClientRect();
        if (rect.width > 8 && rect.height > 8) {
          syncCanvasSize();
          return true;
        }
      }
      await sleep(24);
    }
    return false;
  };

  const mountVolume = async (url) => {
    if (!canvasEl || !url) return;
    const token = mountSeq + 1;
    mountSeq = token;
    loading = true;
    errorMessage = "";

    if (typeof window === "undefined") {
      loading = false;
      return;
    }
    const ns = window.niivue;
    if (!ns || typeof ns.Niivue !== "function") {
      loading = false;
      errorMessage = "Medical viewer unavailable.";
      return;
    }

    const connected = await waitForConnectedCanvas(token);
    if (!connected || token !== mountSeq) {
      if (token === mountSeq) {
        loading = false;
        errorMessage = "Viewer mount failed.";
      }
      return;
    }

    try {
      destroyNiivue();
      const nv = new ns.Niivue({
        dragAndDropEnabled: false,
        show3Dcrosshair: true,
        isResizeCanvas: true,
        backColor: [0.02, 0.03, 0.05, 1.0],
      });
      niivueInstance = nv;

      let attached = false;
      let attachError = null;
      if (typeof nv.attachToCanvas === "function") {
        try {
          await withTimeout(Promise.resolve(nv.attachToCanvas(canvasEl, false)), 7000, "Niivue attach");
          attached = true;
        } catch (error) {
          attachError = error;
        }
      }
      if (!attached && typeof nv.attachTo === "function") {
        try {
          if (!canvasEl.id) {
            canvasEl.id = `start-niivue-${Math.random().toString(36).slice(2, 10)}`;
          }
          await withTimeout(Promise.resolve(nv.attachTo(canvasEl.id)), 6000, "Niivue attach fallback");
          attached = true;
        } catch (error) {
          attachError = error;
        }
      }
      if (!attached) {
        throw attachError || new Error("Unable to initialize medical viewer.");
      }

      let loaded = false;
      let loadError = null;
      for (const candidateUrl of candidateUrlsFor(url)) {
        try {
          await withTimeout(
            Promise.resolve(
              nv.loadVolumes([
                {
                  url: candidateUrl,
                  name: candidateUrl.includes(".gz") ? "value.nii.gz" : "value.nii",
                },
              ]),
            ),
            18000,
            "Niivue load",
          );
          loaded = true;
          break;
        } catch (error) {
          loadError = error;
        }
      }
      if (!loaded) {
        throw loadError || new Error("Unable to load volume.");
      }
      if (token !== mountSeq) return;
      if (!Array.isArray(nv.volumes) || !nv.volumes.length) {
        throw new Error("Empty volume data.");
      }

      const volume = nv.volumes[0];
      if (volume && Number.isFinite(volume.global_min) && Number.isFinite(volume.global_max)) {
        if (!Number.isFinite(volume.cal_min) || !Number.isFinite(volume.cal_max) || volume.cal_max <= volume.cal_min) {
          volume.cal_min = Number(volume.global_min);
          volume.cal_max = Math.max(Number(volume.global_max), Number(volume.global_min) + 1.0);
        }
      }
      if (typeof nv.setOpacity === "function") {
        try {
          nv.setOpacity(0, 1.0);
        } catch {
          // Older APIs differ.
        }
      }
      if (typeof nv.setColormap === "function") {
        try {
          nv.setColormap("gray", 0);
        } catch {
          try {
            nv.setColormap(0, "gray");
          } catch {
            // Older APIs differ.
          }
        }
      }
      if (ns.SLICE_TYPE && typeof nv.setSliceType === "function") {
        nv.setSliceType(ns.SLICE_TYPE.MULTIPLANAR);
      }
      if (nv.scene && Array.isArray(nv.scene.crosshairPos)) {
        nv.scene.crosshairPos = [0.5, 0.5, 0.5];
      }
      if (typeof nv.updateGLVolume === "function") {
        nv.updateGLVolume();
      }
      if (typeof nv.drawScene === "function") {
        nv.drawScene();
      }
      if (token !== mountSeq) return;
      loading = false;
      errorMessage = "";
    } catch (error) {
      if (token !== mountSeq) return;
      destroyNiivue();
      loading = false;
      errorMessage = String(error?.message || error || "Unable to render volume.");
    }
  };

  $: normalizedUrl = String(niftiUrl || "").trim();
  $: if (canvasEl && normalizedUrl) {
    void mountVolume(normalizedUrl);
  }
  $: if (canvasEl && !normalizedUrl) {
    mountSeq += 1;
    destroyNiivue();
    loading = false;
    errorMessage = "";
  }

  onDestroy(() => {
    mountSeq += 1;
    destroyNiivue();
  });
</script>

<div class="start-medical-volume">
  <canvas bind:this={canvasEl} class="start-medical-volume-canvas" aria-label={`${label} medical viewer`}></canvas>
  {#if loading}
    <div class="start-medical-volume-loading" aria-hidden="true"></div>
  {:else if errorMessage}
    <div class="start-medical-volume-error">{errorMessage}</div>
  {/if}
</div>
