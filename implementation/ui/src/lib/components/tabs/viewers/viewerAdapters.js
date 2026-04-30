const clearHost = (host) => {
  if (host) host.replaceChildren();
};

const setText = (node, value) => {
  if (!node) return;
  node.textContent = String(value ?? "");
};

const setImageSource = (imageEl, url = "", alt = "") => {
  if (!imageEl) return;
  imageEl.setAttribute("src", String(url || ""));
  imageEl.setAttribute("alt", String(alt || ""));
};

const createCenteredShell = (host, innerClassName = "") => {
  const outer = document.createElement("div");
  outer.className = "start-value-centered";
  const inner = document.createElement("div");
  inner.className = innerClassName;
  outer.append(inner);
  host.replaceChildren(outer);
  return { outer, inner };
};

const createMessageAdapter = (host) => {
  const messageEl = document.createElement("div");
  messageEl.className = "start-viewer-message";
  host.replaceChildren(messageEl);
  return {
    update(contract) {
      setText(messageEl, contract?.text || "No value");
    },
    destroy() {
      clearHost(host);
    },
  };
};

const createScalarAdapter = (host) => {
  const { inner } = createCenteredShell(host, "start-pure-scalar");
  return {
    update(contract) {
      setText(inner, contract?.text || "");
    },
    destroy() {
      clearHost(host);
    },
  };
};

const createTextAdapter = (host) => {
  const outer = document.createElement("div");
  outer.className = "start-value-centered start-value-centered--text";
  const pre = document.createElement("pre");
  pre.className = "start-pure-text";
  outer.append(pre);
  host.replaceChildren(outer);
  return {
    update(contract) {
      setText(pre, contract?.text || "");
    },
    destroy() {
      clearHost(host);
    },
  };
};

const createArrayAdapter = (host) => {
  const { inner } = createCenteredShell(host, "start-pure-array");
  return {
    update(contract) {
      setText(inner, contract?.text || "");
    },
    destroy() {
      clearHost(host);
    },
  };
};

const createImageAdapter = (host) => {
  const outer = document.createElement("div");
  outer.className = "start-value-centered";
  const imageEl = document.createElement("img");
  imageEl.className = "start-pure-image";
  outer.append(imageEl);
  host.replaceChildren(outer);
  return {
    update(contract) {
      setImageSource(imageEl, contract?.imageUrl || "", contract?.alt || `${contract?.label || "value"} preview`);
    },
    destroy() {
      clearHost(host);
    },
  };
};

const createImageOverlayAdapter = (host) => {
  const outer = document.createElement("div");
  outer.className = "start-value-centered";
  const shell = document.createElement("div");
  shell.className = "start-overlay-image-shell";
  outer.append(shell);
  host.replaceChildren(outer);

  const syncLayerCount = (count) => {
    while (shell.childElementCount > count) {
      shell.lastElementChild?.remove();
    }
    while (shell.childElementCount < count) {
      const imageEl = document.createElement("img");
      shell.append(imageEl);
    }
  };

  return {
    update(contract) {
      const layers = (Array.isArray(contract?.layers) ? contract.layers : []).filter((layer) => layer?.visible !== false);
      shell.setAttribute("aria-label", String(contract?.ariaLabel || `${contract?.label || "value"} overlay`));
      syncLayerCount(layers.length);
      layers.forEach((layer, index) => {
        const imageEl = shell.children[index];
        if (!(imageEl instanceof HTMLImageElement)) return;
        imageEl.className = `start-overlay-image-layer ${index === 0 ? "is-base" : "is-overlay"}`.trim();
        setImageSource(imageEl, layer?.url || "", layer?.label || `Layer ${index + 1}`);
        imageEl.style.setProperty("--layer-opacity", String(Number.isFinite(Number(layer?.opacity)) ? Number(layer.opacity) : index === 0 ? 1 : 0.4));
      });
    },
    destroy() {
      clearHost(host);
    },
  };
};

const createRecordViewerFallback = (host) => {
  const pre = document.createElement("pre");
  pre.className = "start-viewer-message start-viewer-message--record";
  host.replaceChildren(pre);
  return {
    setLoading(message) {
      setText(pre, message || "Loading...");
    },
    setError(message) {
      setText(pre, message || "Viewer error");
    },
    renderRecord(record) {
      setText(pre, JSON.stringify(record || {}, null, 2));
    },
    refreshPage() {
      // No paging surface in the fallback viewer.
    },
    destroy() {
      clearHost(host);
    },
  };
};

const createRecordViewerAdapter = (host) => {
  let viewer = null;
  const callbacks = {
    onNavigate: null,
    fetchPage: null,
    onStatusClick: null,
  };

  const ensureViewer = () => {
    if (viewer) return viewer;

    const ctor = window?.VoxResultViewer?.ResultViewer;
    if (typeof ctor === "function") {
      viewer = new ctor(host, {
        onNavigate: (path) => callbacks.onNavigate?.(path),
        fetchPage: (request) => callbacks.fetchPage?.(request),
        onStatusClick: (record) => callbacks.onStatusClick?.(record),
      });
      return viewer;
    }

    viewer = createRecordViewerFallback(host);
    return viewer;
  };

  return {
    update(contract) {
      callbacks.onNavigate = typeof contract?.onNavigate === "function" ? contract.onNavigate : null;
      callbacks.fetchPage = typeof contract?.fetchPage === "function" ? contract.fetchPage : null;
      callbacks.onStatusClick = typeof contract?.onStatusClick === "function" ? contract.onStatusClick : null;

      const activeViewer = ensureViewer();
      const refresh = contract?.pageRefresh;
      if (refresh?.nodeId && typeof activeViewer?.refreshPage === "function") {
        activeViewer.refreshPage(refresh.nodeId, refresh.path || "");
        if (refresh.preserveRecord) return;
      }

      switch (String(contract?.state || "empty")) {
        case "loading":
          activeViewer?.setLoading?.(String(contract?.message || `Loading ${contract?.label || "value"}...`));
          return;
        case "error":
          activeViewer?.setError?.(String(contract?.message || "Viewer error"));
          return;
        case "record":
          activeViewer?.renderRecord?.(contract?.record || null);
          return;
        case "empty":
        default:
          activeViewer?.renderRecord?.(null);
      }
    },
    destroy() {
      if (viewer && typeof viewer.destroy === "function") {
        viewer.destroy();
      }
      viewer = null;
      clearHost(host);
    },
  };
};

const setLoadingState = (loadingEl, errorEl, { loading = false, error = "" } = {}) => {
  if (loadingEl) loadingEl.style.display = loading ? "block" : "none";
  if (errorEl) {
    errorEl.style.display = error ? "block" : "none";
    errorEl.textContent = error || "";
  }
};

const withCacheBuster = (url) => {
  const text = String(url || "").trim();
  if (!text) return "";
  const separator = text.includes("?") ? "&" : "?";
  return `${text}${separator}_=${Date.now()}`;
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

const medicalLoadLooksTransient = (error) => {
  const message = String(error?.message || error || "").toLowerCase();
  if (!message) return false;
  return (
    message.includes("404") ||
    message.includes("not found") ||
    message.includes("failed to fetch") ||
    message.includes("networkerror") ||
    message.includes("loadvolume")
  );
};

const snapshotMedicalViewState = (niivueInstance) => ({
  crosshairPos: Array.isArray(niivueInstance?.scene?.crosshairPos) ? [...niivueInstance.scene.crosshairPos] : null,
});

const restoreMedicalViewState = (niivueInstance, snapshot) => {
  if (!niivueInstance || !snapshot) return;
  if (snapshot.crosshairPos && niivueInstance.scene) {
    niivueInstance.scene.crosshairPos = [...snapshot.crosshairPos];
  }
  if (typeof niivueInstance.drawScene === "function") {
    niivueInstance.drawScene();
  }
};

const createMedicalAdapter = (host) => {
  const shell = document.createElement("div");
  shell.className = "start-medical-volume";
  const canvasEl = document.createElement("canvas");
  canvasEl.className = "start-medical-volume-canvas";
  const loadingEl = document.createElement("div");
  loadingEl.className = "start-medical-volume-loading";
  loadingEl.setAttribute("aria-hidden", "true");
  const errorEl = document.createElement("div");
  errorEl.className = "start-medical-volume-error";
  shell.append(canvasEl, loadingEl, errorEl);
  host.replaceChildren(shell);

  let niivueInstance = null;
  let destroyed = false;
  let updateSeq = 0;
  let attached = false;
  let loadedFingerprint = "";
  let resizeObserver = null;

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

  const destroyNiivue = () => {
    const current = niivueInstance;
    niivueInstance = null;
    attached = false;
    loadedFingerprint = "";
    if (!current || typeof current.destroy !== "function") return;
    try {
      current.destroy();
    } catch {
      // Best-effort cleanup only.
    }
  };

  const disconnectResizeObserver = () => {
    if (!resizeObserver || typeof resizeObserver.disconnect !== "function") return;
    try {
      resizeObserver.disconnect();
    } catch {
      // Best-effort cleanup only.
    }
    resizeObserver = null;
  };

  const syncCanvasSize = () => {
    const rect = shell.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width || canvasEl.clientWidth || 720));
    const height = Math.max(1, Math.round(rect.height || canvasEl.clientHeight || 420));
    const dpr = Number.isFinite(window.devicePixelRatio) && window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
    canvasEl.style.width = `${width}px`;
    canvasEl.style.height = `${height}px`;
    canvasEl.width = Math.max(1, Math.round(width * dpr));
    canvasEl.height = Math.max(1, Math.round(height * dpr));
    if (attached && typeof niivueInstance?.drawScene === "function") {
      try {
        niivueInstance.drawScene();
      } catch {
        // Best-effort redraw only.
      }
    }
  };

  const ensureResizeObserver = () => {
    if (resizeObserver || typeof ResizeObserver !== "function") return;
    resizeObserver = new ResizeObserver(() => {
      if (destroyed) return;
      syncCanvasSize();
    });
    resizeObserver.observe(host);
    resizeObserver.observe(shell);
  };

  const waitForConnectedCanvas = async (token) => {
    for (let attempt = 0; attempt < 24; attempt += 1) {
      if (destroyed || token !== updateSeq) return false;
      if (canvasEl.isConnected) {
        syncCanvasSize();
        return true;
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
      // Older APIs use the inverse argument order.
    }
    try {
      nv.setOpacity(value, index);
    } catch {
      // No-op.
    }
  };

  const setVolumeColormap = (nv, index, colormap) => {
    if (!colormap || typeof nv?.setColormap !== "function") return;
    try {
      nv.setColormap(colormap, index);
      return;
    } catch {
      // Older APIs use the inverse argument order.
    }
    try {
      nv.setColormap(index, colormap);
    } catch {
      // No-op.
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
    if (ns?.SLICE_TYPE && typeof nv.setSliceType === "function") {
      nv.setSliceType(ns.SLICE_TYPE.MULTIPLANAR);
    }
    if (typeof nv.updateGLVolume === "function") {
      nv.updateGLVolume();
    }
    if (typeof nv.drawScene === "function") {
      nv.drawScene();
    }
  };

  const loadSources = async (sources, token) => {
    const ns = window?.niivue;
    if (!ns || typeof ns.Niivue !== "function") {
      setLoadingState(loadingEl, errorEl, { loading: false, error: "Medical viewer unavailable." });
      return;
    }

    if (!niivueInstance) {
      niivueInstance = new ns.Niivue({
        dragAndDropEnabled: false,
        show3Dcrosshair: true,
        isResizeCanvas: true,
        backColor: [0.02, 0.03, 0.05, 1.0],
      });
    }

    if (!attached) {
      let attachError = null;
      if (typeof niivueInstance.attachToCanvas === "function") {
        try {
          await withTimeout(Promise.resolve(niivueInstance.attachToCanvas(canvasEl, false)), 7000, "Niivue attach");
          attached = true;
        } catch (error) {
          attachError = error;
        }
      }
      if (!attached && typeof niivueInstance.attachTo === "function") {
        try {
          if (!canvasEl.id) {
            canvasEl.id = `start-niivue-${Math.random().toString(36).slice(2, 10)}`;
          }
          await withTimeout(Promise.resolve(niivueInstance.attachTo(canvasEl.id)), 6000, "Niivue attach fallback");
          attached = true;
        } catch (error) {
          attachError = error;
        }
      }
      if (!attached) {
        throw attachError || new Error("Unable to initialize medical viewer.");
      }
    }

    const nextFingerprint = JSON.stringify(sources.map((source) => ({
      url: source.url,
      visible: source.visible !== false,
      opacity: Number(source.opacity),
      colormap: String(source.colormap || ""),
    })));

    if (loadedFingerprint === nextFingerprint) {
      applyVolumeStyles(niivueInstance, ns, sources);
      return;
    }

    const snapshot = snapshotMedicalViewState(niivueInstance);
    let loaded = false;
    let loadError = null;
    const attempts = buildLoadAttempts(sources);
    const transientRetryDelaysMs = [180, 360, 720];
    for (let retryIndex = 0; retryIndex <= transientRetryDelaysMs.length && !loaded; retryIndex += 1) {
      for (const candidateSources of attempts) {
        try {
          if (destroyed || token !== updateSeq) return;
          await withTimeout(
            Promise.resolve(
              niivueInstance.loadVolumes(
                candidateSources.map((source, index) => ({
                  url: source.url,
                  name: source.url.includes(".gz") ? `${source.label || `Layer ${index + 1}`}.nii.gz` : `${source.label || `Layer ${index + 1}`}.nii`,
                })),
              ),
            ),
            18000,
            "Niivue load",
          );
          if (!Array.isArray(niivueInstance.volumes) || !niivueInstance.volumes.length) {
            throw new Error("Empty volume data.");
          }
          for (const volume of niivueInstance.volumes) {
            if (!volume || !Number.isFinite(volume.global_min) || !Number.isFinite(volume.global_max)) continue;
            if (!Number.isFinite(volume.cal_min) || !Number.isFinite(volume.cal_max) || volume.cal_max <= volume.cal_min) {
              volume.cal_min = Number(volume.global_min);
              volume.cal_max = Math.max(Number(volume.global_max), Number(volume.global_min) + 1);
            }
          }
          applyVolumeStyles(niivueInstance, ns, candidateSources);
          restoreMedicalViewState(niivueInstance, snapshot);
          loadedFingerprint = nextFingerprint;
          loaded = true;
          break;
        } catch (error) {
          loadError = error;
        }
      }
      if (loaded) break;
      if (!medicalLoadLooksTransient(loadError) || retryIndex >= transientRetryDelaysMs.length) {
        break;
      }
      await sleep(transientRetryDelaysMs[retryIndex]);
    }

    if (!loaded) {
      throw loadError || new Error("Unable to load volume.");
    }
  };

  return {
    async update(contract) {
      const token = updateSeq + 1;
      updateSeq = token;
      const label = String(contract?.ariaLabel || `${contract?.label || "value"} medical viewer`);
      canvasEl.setAttribute("aria-label", label);

      const sources = Array.isArray(contract?.sources) ? contract.sources.filter(Boolean) : [];
      if (!sources.length) {
        setLoadingState(loadingEl, errorEl, { loading: false, error: "Medical viewer unavailable." });
        return;
      }

      const connected = await waitForConnectedCanvas(token);
      if (!connected || destroyed || token !== updateSeq || typeof window === "undefined") return;
      ensureResizeObserver();

      setLoadingState(loadingEl, errorEl, { loading: true, error: "" });
      try {
        await loadSources(sources, token);
        if (destroyed || token !== updateSeq) return;
        setLoadingState(loadingEl, errorEl, { loading: false, error: "" });
      } catch (error) {
        if (destroyed || token !== updateSeq) return;
        setLoadingState(loadingEl, errorEl, {
          loading: false,
          error: String(error?.message || error || "Unable to render volume."),
        });
      }
    },
    destroy() {
      destroyed = true;
      updateSeq += 1;
      disconnectResizeObserver();
      destroyNiivue();
      clearHost(host);
    },
  };
};

// Every leaf viewer implements the same imperative contract:
// - `update(contract)` morphs the existing viewer in place.
// - `destroy()` tears down any external state.
// This lets StartTab keep viewer slots stable while values change.
export const createViewerAdapter = (host, contract) => {
  const adapterKey = String(contract?.adapterKey || "message");
  switch (adapterKey) {
    case "record-viewer":
      return createRecordViewerAdapter(host);
    case "scalar":
      return createScalarAdapter(host);
    case "text":
      return createTextAdapter(host);
    case "image":
      return createImageAdapter(host);
    case "image-overlay":
      return createImageOverlayAdapter(host);
    case "medical":
      return createMedicalAdapter(host);
    case "array":
      return createArrayAdapter(host);
    case "message":
    default:
      return createMessageAdapter(host);
  }
};
