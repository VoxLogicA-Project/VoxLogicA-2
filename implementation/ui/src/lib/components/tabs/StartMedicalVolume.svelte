<script>
  import { onDestroy } from "svelte";

  export let niftiUrl = "";
  export let layers = [];
  export let label = "value";

  let canvasEl = null;
  let loading = false;
  let errorMessage = "";
  let mountSeq = 0;
  let niivueInstance = null;
  let destroyed = false;

  const setTimeoutSafe = (callback, ms) => {
    const scheduler = typeof globalThis.setTimeout === "function" ? globalThis.setTimeout.bind(globalThis) : null;
    if (!scheduler) return null;
    return scheduler(callback, ms);
  };

  const sleep = (ms) =>
    new Promise((resolve) => {
      const timer = setTimeoutSafe(resolve, ms);
      if (timer === null) resolve();
    });

  const withTimeout = (promise, ms, labelText) =>
    Promise.race([
      promise,
      new Promise((_, reject) => {
        const timer = setTimeoutSafe(() => reject(new Error(`${labelText} timed out after ${ms} ms`)), ms);
        if (timer === null) reject(new Error(`${labelText} timed out after ${ms} ms`));
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

  const normalizeLayerSource = (layer, index) => {
    const source = layer && typeof layer === "object" ? layer : { nifti_url: layer };
    const nifti = String(source?.nifti_url || source?.url || "").trim();
    if (!nifti) return null;
    const opacityRaw = source?.opacity;
    const opacity =
      opacityRaw === null || opacityRaw === undefined || opacityRaw === ""
        ? index === 0
          ? 1
          : 0.42
        : Number(opacityRaw);
    return {
      url: nifti,
      label: String(source?.label || source?.name || `Layer ${index + 1}`),
      opacity: Number.isFinite(opacity) ? Math.max(0, Math.min(1, opacity)) : index === 0 ? 1 : 0.42,
      colormap: String(source?.colormap || "").trim(),
      visible: source?.visible !== false,
    };
  };

  const buildLoadAttempts = (sources) => {
    const variants = sources.map((source) => candidateUrlsFor(source.url));
    const maxVariants = variants.reduce((max, list) => Math.max(max, list.length), 1);
    const attempts = [];
    for (let variantIndex = 0; variantIndex < maxVariants; variantIndex += 1) {
      attempts.push(
        sources.map((source, sourceIndex) => ({
          ...source,
          url: variants[sourceIndex][Math.min(variantIndex, variants[sourceIndex].length - 1)],
        })),
      );
    }
    return attempts;
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
    if (!canvasEl || typeof window === "undefined") return;
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
      if (destroyed || token !== mountSeq) return false;
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

  const setVolumeOpacity = (nv, index, value) => {
    if (typeof nv?.setOpacity !== "function") return;
    try {
      nv.setOpacity(index, value);
      return;
    } catch {
      // Try alternate signature below.
    }
    try {
      nv.setOpacity(value, index);
    } catch {
      // Older APIs differ.
    }
  };

  const setVolumeColormap = (nv, index, colormap) => {
    if (!colormap || typeof nv?.setColormap !== "function") return;
    try {
      nv.setColormap(colormap, index);
      return;
    } catch {
      // Try alternate signature below.
    }
    try {
      nv.setColormap(index, colormap);
    } catch {
      // Older APIs differ.
    }
  };

  const applyVolumeStyles = (nv, ns, sources) => {
    const fallbackMaps = ["gray", "red", "green", "blue", "winter", "warm", "plasma"];
    sources.forEach((source, index) => {
      const opacity = source.visible === false ? 0 : source.opacity;
      setVolumeOpacity(nv, index, opacity);
      const desiredMap = source.colormap || fallbackMaps[Math.min(index, fallbackMaps.length - 1)];
      setVolumeColormap(nv, index, index === 0 ? "gray" : desiredMap);
    });
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
  };

  const mountVolume = async (sources) => {
    if (destroyed || !canvasEl || !Array.isArray(sources) || !sources.length) return;
    const token = mountSeq + 1;
    mountSeq = token;
    loading = true;
    errorMessage = "";

    if (typeof navigator !== "undefined" && /jsdom/i.test(String(navigator.userAgent || ""))) {
      loading = false;
      errorMessage = "Medical viewer unavailable.";
      return;
    }

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
    if (!connected || destroyed || token !== mountSeq || typeof document === "undefined") {
      if (token === mountSeq) {
        loading = false;
        errorMessage = "Viewer mount failed.";
      }
      return;
    }

    try {
      if (destroyed || token !== mountSeq || typeof document === "undefined") return;
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
          if (destroyed || token !== mountSeq || typeof document === "undefined") return;
          await withTimeout(Promise.resolve(nv.attachToCanvas(canvasEl, false)), 7000, "Niivue attach");
          attached = true;
        } catch (error) {
          attachError = error;
        }
      }
      if (!attached && typeof nv.attachTo === "function") {
        try {
          if (destroyed || token !== mountSeq || typeof document === "undefined") return;
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
      for (const candidateSources of buildLoadAttempts(sources)) {
        try {
          if (destroyed || token !== mountSeq || typeof document === "undefined") return;
          await withTimeout(
            Promise.resolve(
              nv.loadVolumes([
                ...candidateSources.map((source, index) => ({
                  url: source.url,
                  name: source.url.includes(".gz") ? `${source.label || `${label}-${index + 1}`}.nii.gz` : `${source.label || `${label}-${index + 1}`}.nii`,
                })),
              ]),
            ),
            18000,
            "Niivue load",
          );
          applyVolumeStyles(nv, ns, candidateSources);
          loaded = true;
          break;
        } catch (error) {
          loadError = error;
        }
      }
      if (!loaded) {
        throw loadError || new Error("Unable to load volume.");
      }
      if (destroyed || token !== mountSeq) return;
      if (!Array.isArray(nv.volumes) || !nv.volumes.length) {
        throw new Error("Empty volume data.");
      }

      for (const volume of nv.volumes) {
        if (volume && Number.isFinite(volume.global_min) && Number.isFinite(volume.global_max)) {
          if (!Number.isFinite(volume.cal_min) || !Number.isFinite(volume.cal_max) || volume.cal_max <= volume.cal_min) {
            volume.cal_min = Number(volume.global_min);
            volume.cal_max = Math.max(Number(volume.global_max), Number(volume.global_min) + 1.0);
          }
        }
      }
      if (destroyed || token !== mountSeq) return;
      loading = false;
      errorMessage = "";
    } catch (error) {
      if (destroyed || token !== mountSeq) return;
      destroyNiivue();
      loading = false;
      errorMessage = String(error?.message || error || "Unable to render volume.");
    }
  };

  $: normalizedUrl = String(niftiUrl || "").trim();
  $: normalizedLayers = (
    Array.isArray(layers)
      ? layers.map((layer, index) => normalizeLayerSource(layer, index)).filter(Boolean)
      : []
  );
  $: activeSources = normalizedLayers.length
    ? normalizedLayers
    : normalizedUrl
      ? [{ url: normalizedUrl, label, opacity: 1, colormap: "gray", visible: true }]
      : [];
  $: if (canvasEl && activeSources.length) {
    void mountVolume(activeSources);
  }
  $: if (canvasEl && !activeSources.length) {
    mountSeq += 1;
    destroyNiivue();
    loading = false;
    errorMessage = "";
  }

  onDestroy(() => {
    destroyed = true;
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
