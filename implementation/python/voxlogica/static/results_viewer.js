(() => {
  const create = (tag, className = "", text = "") => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  };

  const fmt = (value) => {
    if (value === null || value === undefined) return "null";
    if (typeof value === "number") return Number.isFinite(value) ? value.toString() : `${value}`;
    if (typeof value === "boolean") return value ? "true" : "false";
    return `${value}`;
  };

  const keyValueGrid = (pairs) => {
    const grid = create("div", "viewer-kv");
    for (const [label, value] of pairs) {
      const row = create("div", "viewer-kv-row");
      const k = create("span", "viewer-k");
      const v = create("span", "viewer-v");
      k.textContent = label;
      v.textContent = fmt(value);
      row.append(k, v);
      grid.append(row);
    }
    return grid;
  };

  const sparkline = (values) => {
    if (!Array.isArray(values) || values.length < 2) return null;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1.0;
    const width = 620;
    const height = 160;
    const pad = 8;
    const points = values
      .map((v, idx) => {
        const x = pad + (idx / (values.length - 1)) * (width - 2 * pad);
        const y = height - pad - ((v - min) / span) * (height - 2 * pad);
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
    const svg = create("svg", "viewer-sparkline");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = `
      <defs>
        <linearGradient id="voxSpark" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#2fd7b9"></stop>
          <stop offset="100%" stop-color="#ff9f54"></stop>
        </linearGradient>
      </defs>
      <polyline fill="none" stroke="url(#voxSpark)" stroke-width="2.2" points="${points}"></polyline>
    `;
    return svg;
  };

  const debugLog = (...args) => {
    try {
      console.warn(...args);
    } catch (_logErr) {
      // Ignore console serialization issues.
    }
  };

  class ResultViewer {
    constructor(root, options = {}) {
      this.root = root;
      this.onNavigate = options.onNavigate || (() => {});
      this.onStatusClick = options.onStatusClick || (() => {});
      this.nv = null;
      this.carouselState = {};
      this.mountToken = 0;
    }

    destroyNiivue() {
      this.mountToken += 1;
      const prior = this.nv;
      this.nv = null;
      if (prior && typeof prior.destroy === "function") {
        // Avoid blocking the UI thread on teardown; some browser+GPU combos stall on synchronous destroy.
        window.setTimeout(() => {
          try {
            prior.destroy();
          } catch (error) {
            try {
              console.warn("[vox-viewer] niivue destroy failed", error);
            } catch (_logErr) {
              // Ignore console serialization issues.
            }
          }
        }, 0);
      }
    }

    setLoading(message) {
      this.destroyNiivue();
      this.root.innerHTML = "";
      this.root.append(create("div", "muted", message || "Loading result..."));
    }

    setError(message) {
      this.destroyNiivue();
      this.root.innerHTML = "";
      const block = create("div", "viewer-error");
      block.textContent = message || "Unable to render result.";
      this.root.append(block);
    }

    renderRecord(record) {
      this.destroyNiivue();
      this.root.innerHTML = "";
      if (!record) {
        this.root.append(create("div", "muted", "Select a result from the list."));
        return;
      }
      this.lastRecord = record;
      const header = create("div", "viewer-header");
      const title = create("h3", "", record.node_id || "result");
      const status = String(record.status || "unknown");
      const badge = create("span", `chip ${status || "neutral"}`, status || "unknown");
      if (status === "failed" || status === "killed") {
        badge.classList.add("chip-clickable");
        badge.setAttribute("role", "button");
        badge.setAttribute("tabindex", "0");
        badge.setAttribute("title", "Open failure diagnostics");
        badge.addEventListener("click", () => this.onStatusClick(record));
        badge.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            this.onStatusClick(record);
          }
        });
      }
      header.append(title, badge);
      this.root.append(header);
      this.root.append(
        keyValueGrid([
          ["runtime", record.runtime_version || "-"],
          ["path", record.path || "/"],
          ["updated", record.updated_at || "-"],
        ]),
      );
      const descriptor = record.descriptor || { kind: "unknown" };
      const body = this._renderDescriptor(descriptor, record.path || "");
      this.root.append(body);
    }

    _renderDescriptor(descriptor, path) {
      const card = create("section", "viewer-card");
      const kind = create("div", "viewer-kind", descriptor.kind || "unknown");
      card.append(kind);

      if (descriptor.kind === "string") {
        const pre = create("pre", "viewer-code");
        pre.textContent = descriptor.preview && descriptor.preview.text ? descriptor.preview.text : "";
        card.append(pre);
        return card;
      }

      if (["integer", "number", "boolean", "null"].includes(descriptor.kind)) {
        card.append(create("div", "viewer-scalar", fmt(descriptor.value)));
        return card;
      }

      if (descriptor.kind === "bytes") {
        card.append(create("div", "muted", `byte length: ${fmt(descriptor.length)}`));
        return card;
      }

      if (descriptor.kind === "ndarray") {
        card.append(
          keyValueGrid([
            ["dtype", descriptor.dtype || "-"],
            ["shape", (descriptor.shape || []).join(" x ") || "-"],
            ["size", descriptor.size || 0],
          ]),
        );
        if (descriptor.stats && Object.keys(descriptor.stats).length) {
          card.append(
            keyValueGrid([
              ["min", descriptor.stats.min],
              ["max", descriptor.stats.max],
              ["mean", descriptor.stats.mean],
            ]),
          );
        }
        if (Array.isArray(descriptor.values)) {
          const plot = sparkline(descriptor.values.map((v) => Number(v)));
          if (plot) card.append(plot);
        }
        this._appendRenderMedia(card, descriptor.render, path);
        return card;
      }

      if (descriptor.kind === "simpleitk-image") {
        card.append(
          keyValueGrid([
            ["dimension", descriptor.dimension || "-"],
            ["size", (descriptor.size || []).join(" x ") || "-"],
            ["pixel", descriptor.pixel_id || "-"],
            ["spacing", (descriptor.spacing || []).join(", ") || "-"],
          ]),
        );
        this._appendRenderMedia(card, descriptor.render, path);
        return card;
      }

      if (descriptor.kind === "sequence") {
        card.append(
          keyValueGrid([
            ["type", descriptor.sequence_type || "sequence"],
            ["length", descriptor.length || 0],
          ]),
        );
        if (Array.isArray(descriptor.numeric_values) && descriptor.numeric_values.length > 1) {
          const plot = sparkline(descriptor.numeric_values);
          if (plot) card.append(plot);
        }
        card.append(this._buildCarousel(descriptor.items || [], "sequence", descriptor.truncated));
        return card;
      }

      if (descriptor.kind === "mapping") {
        card.append(
          keyValueGrid([
            ["type", descriptor.mapping_type || "mapping"],
            ["entries", descriptor.length || 0],
          ]),
        );
        card.append(this._buildCarousel(descriptor.entries || [], "mapping", descriptor.truncated));
        return card;
      }

      if (descriptor.kind === "object") {
        const pre = create("pre", "viewer-code");
        pre.textContent = descriptor.repr || descriptor.type || "object";
        card.append(pre);
        return card;
      }

      if (descriptor.kind === "unavailable") {
        card.append(create("div", "muted", descriptor.reason || "Value unavailable."));
        return card;
      }

      card.append(create("pre", "viewer-code", JSON.stringify(descriptor, null, 2)));
      return card;
    }

    _appendRenderMedia(card, render, path) {
      if (!render || !render.kind) return;
      if (render.kind === "image2d" && render.png_url) {
        const figure = create("figure", "viewer-figure");
        const img = create("img", "viewer-image");
        img.src = this._withCacheBuster(render.png_url);
        img.alt = `Rendered image at ${path || "/"}`;
        figure.append(img);
        card.append(figure);
      }
      if (render.kind === "medical-volume" && render.nifti_url) {
        const wrap = create("div", "viewer-niivue-wrap");
        const toolbar = create("div", "viewer-toolbar");
        const openAtPath = create("button", "btn btn-ghost btn-small", "Inspect This Value");
        openAtPath.addEventListener("click", () => this.onNavigate(path));
        toolbar.append(openAtPath);
        const status = create("div", "viewer-load-status muted", "Initializing medical viewer...");
        const canvas = create("canvas", "viewer-niivue");
        wrap.append(toolbar, status, canvas);
        card.append(wrap);
        const mountWhenConnected = (attempt = 0) => {
          if (canvas.isConnected) {
            this._mountNiivue(canvas, this._withCacheBuster(render.nifti_url), status);
            return;
          }
          if (attempt >= 40) {
            status.textContent = "Viewer mount failed: canvas not connected.";
            return;
          }
          window.requestAnimationFrame(() => mountWhenConnected(attempt + 1));
        };
        mountWhenConnected();
      }
    }

    _buildCarousel(items, family, truncated) {
      const block = create("div", "viewer-carousel");
      if (!Array.isArray(items) || items.length === 0) {
        block.append(create("div", "muted", "No preview items available."));
        return block;
      }
      const stateKey = `${family}:${items.map((item) => item.path).join("|")}`;
      if (!Number.isInteger(this.carouselState[stateKey])) this.carouselState[stateKey] = 0;
      const index = Math.max(0, Math.min(this.carouselState[stateKey], items.length - 1));
      this.carouselState[stateKey] = index;

      const top = create("div", "viewer-carousel-head");
      const title = create("strong", "", `${family} item ${index + 1}/${items.length}`);
      const nav = create("div", "row gap-s");
      const prev = create("button", "btn btn-ghost btn-small", "Prev");
      const next = create("button", "btn btn-ghost btn-small", "Next");
      prev.disabled = index === 0;
      next.disabled = index === items.length - 1;
      prev.addEventListener("click", () => {
        this.carouselState[stateKey] = Math.max(0, this.carouselState[stateKey] - 1);
        this.renderRecord(this.lastRecord);
      });
      next.addEventListener("click", () => {
        this.carouselState[stateKey] = Math.min(items.length - 1, this.carouselState[stateKey] + 1);
        this.renderRecord(this.lastRecord);
      });
      nav.append(prev, next);
      top.append(title, nav);
      block.append(top);

      const selected = items[index];
      const label = create("div", "muted", `${selected.label || "item"} (${selected.path || "/"})`);
      block.append(label);
      const summary = create("pre", "viewer-code");
      summary.textContent = JSON.stringify(selected.summary || {}, null, 2);
      block.append(summary);
      const inspect = create("button", "btn btn-primary btn-small", "Inspect Selected");
      inspect.addEventListener("click", () => this.onNavigate(selected.path || ""));
      block.append(inspect);
      if (truncated) {
        block.append(create("div", "muted", "Preview truncated. Use path navigation to inspect deeper values."));
      }
      return block;
    }

    async _mountNiivue(canvas, url, statusEl = null) {
      const setStatus = (message) => {
        if (!statusEl) return;
        statusEl.textContent = message || "";
      };
      const clearStatus = () => {
        if (!statusEl) return;
        statusEl.textContent = "";
      };
      const ns = window.niivue;
      if (!ns || typeof ns.Niivue !== "function") {
        setStatus("Niivue library unavailable in browser.");
        const missing = create("div", "viewer-error");
        missing.textContent = "Niivue library unavailable in browser.";
        canvas.replaceWith(missing);
        return;
      }
      const withTimeout = (promise, ms, label) =>
        Promise.race([
          promise,
          new Promise((_, reject) => {
            window.setTimeout(() => reject(new Error(`${label} timed out after ${ms} ms`)), ms);
          }),
        ]);
      const sleep = (ms) =>
        new Promise((resolve) => {
          window.setTimeout(resolve, ms);
        });
      const candidateUrls = [url];
      if (typeof url === "string" && url.includes("/render/nii?")) {
        candidateUrls.push(url.replace("/render/nii?", "/render/nii.gz?"));
      } else if (typeof url === "string" && url.endsWith("/render/nii")) {
        candidateUrls.push(`${url}.gz`);
      }
      try {
        setStatus("Starting viewer...");
        debugLog("[vox-viewer] niivue mount start", { url });
        debugLog("[vox-viewer] step 1: destroy previous instance");
        this.destroyNiivue();
        debugLog("[vox-viewer] step 2: create mount token");
        const token = ++this.mountToken;
        if (!canvas || !canvas.isConnected) {
          debugLog("[vox-viewer] abort: canvas disconnected before mount");
          return;
        }
        debugLog("[vox-viewer] step 3: size canvas");
        const rect = canvas.getBoundingClientRect();
        const cssWidth = Math.max(1, Math.round(rect.width || canvas.clientWidth || 640));
        const cssHeight = Math.max(1, Math.round(rect.height || canvas.clientHeight || 420));
        const dpr = Number.isFinite(window.devicePixelRatio) && window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
        canvas.style.width = `${cssWidth}px`;
        canvas.style.height = `${cssHeight}px`;
        canvas.width = Math.max(1, Math.round(cssWidth * dpr));
        canvas.height = Math.max(1, Math.round(cssHeight * dpr));
        debugLog("[vox-viewer] step 4: construct Niivue");
        const nv = new ns.Niivue({
          dragAndDropEnabled: false,
          isRuler: false,
          show3Dcrosshair: true,
          backColor: [0.02, 0.03, 0.05, 1.0],
          isResizeCanvas: true,
        });
        nv.onWarn = (message) => debugLog("[vox-viewer] niivue warn", message);
        nv.onInfo = (message) => debugLog("[vox-viewer] niivue info", message);
        nv.onError = (message) => {
          try {
            console.error("[vox-viewer] niivue error", message);
          } catch (_logErr) {
            // Ignore console serialization issues.
          }
        };
        this.nv = nv;
        debugLog("[vox-viewer] step 5: probe WebGL2");
        {
          const probe = document.createElement("canvas");
          const gl2 = probe.getContext("webgl2");
          if (!gl2) {
            throw new Error("WebGL2 is unavailable in this browser context.");
          }
        }
        setStatus("Attaching WebGL canvas...");
        let attached = false;
        let attachError = null;
        if (typeof nv.attachToCanvas === "function") {
          try {
            debugLog("[vox-viewer] step 6: attachToCanvas begin");
            setStatus("Attaching viewer...");
            await withTimeout(Promise.resolve(nv.attachToCanvas(canvas, false)), 7000, "Niivue attachToCanvas");
            debugLog("[vox-viewer] step 6: attachToCanvas completed");
            attached = true;
          } catch (err) {
            attachError = err;
            debugLog("[vox-viewer] attachToCanvas failed, trying attachTo fallback", {
              error: err && err.message ? err.message : String(err),
            });
          }
        }
        if (!attached && typeof nv.attachTo === "function") {
          try {
            debugLog("[vox-viewer] step 7: attachTo fallback begin");
            setStatus("Retrying canvas attach...");
            if (!canvas.id) {
              canvas.id = `vox-niivue-${Math.random().toString(36).slice(2, 10)}`;
            }
            await withTimeout(Promise.resolve(nv.attachTo(canvas.id)), 5000, "Niivue attachTo");
            debugLog("[vox-viewer] step 7: attachTo fallback completed");
            attached = true;
          } catch (err) {
            attachError = err;
            debugLog("[vox-viewer] attachTo fallback failed", {
              error: err && err.message ? err.message : String(err),
            });
          }
        }
        if (!attached) {
          throw attachError || new Error("Niivue failed to attach to the canvas.");
        }
        await sleep(60);
        if (token !== this.mountToken || !canvas.isConnected) {
          debugLog("[vox-viewer] abort: stale token or disconnected canvas after attach");
          this.destroyNiivue();
          return;
        }
        debugLog("[vox-viewer] step 8: load volumes");
        let loaded = false;
        let lastLoadError = null;
        for (const candidateUrl of candidateUrls) {
          try {
            setStatus(`Loading volume (${candidateUrl.includes(".gz") ? "gz" : "nii"})...`);
            debugLog("[vox-viewer] niivue load attempt", { candidateUrl });
            await withTimeout(
              Promise.resolve(
                nv.loadVolumes([
                  {
                    url: candidateUrl,
                    // Work around Niivue 0.64 edge-case with bare "nii.gz" URL basenames.
                    name: candidateUrl.includes(".gz") ? "stored-volume.nii.gz" : "stored-volume.nii",
                  },
                ]),
              ),
              18000,
              "Niivue loadVolumes",
            );
            loaded = true;
            break;
          } catch (loadErr) {
            lastLoadError = loadErr;
            debugLog("[vox-viewer] niivue load attempt failed", {
              candidateUrl,
              error: loadErr && loadErr.message ? loadErr.message : String(loadErr),
            });
          }
        }
        if (!loaded) {
          throw lastLoadError || new Error("Niivue failed to load all candidate volume URLs.");
        }
        if (token !== this.mountToken || !canvas.isConnected) {
          this.destroyNiivue();
          return;
        }
        if (!Array.isArray(nv.volumes) || nv.volumes.length === 0) {
          throw new Error("Niivue loaded zero volumes for this result.");
        }
        const volume = nv.volumes[0];
        debugLog("[vox-viewer] niivue volume loaded", {
          url,
          dims: volume && volume.dims ? volume.dims : null,
          calMin: volume ? volume.cal_min : null,
          calMax: volume ? volume.cal_max : null,
          globalMin: volume ? volume.global_min : null,
          globalMax: volume ? volume.global_max : null,
        });
        setStatus("Rendering volume...");
        if (volume && Number.isFinite(volume.global_min) && Number.isFinite(volume.global_max)) {
          if (!Number.isFinite(volume.cal_min) || !Number.isFinite(volume.cal_max) || volume.cal_max <= volume.cal_min) {
            volume.cal_min = Number(volume.global_min);
            volume.cal_max = Number(volume.global_max);
          }
          if (volume.cal_max <= volume.cal_min) {
            volume.cal_max = volume.cal_min + 1.0;
          }
        }
        if (volume && typeof nv.setOpacity === "function") {
          try {
            nv.setOpacity(0, 1.0);
          } catch (_err) {
            // Older Niivue signatures vary; continue with defaults.
          }
        }
        if (volume && typeof nv.setColormap === "function") {
          try {
            nv.setColormap("gray", 0);
          } catch (_err) {
            try {
              nv.setColormap(0, "gray");
            } catch (_err2) {
              // Colormap API varies between releases; continue with defaults.
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
        clearStatus();
      } catch (error) {
        try {
          console.error("[vox-viewer] niivue mount failed", error);
        } catch (_logErr) {
          // Ignore console serialization issues.
        }
        setStatus(`Viewer failed: ${error && error.message ? error.message : error}`);
        if (canvas && !canvas.isConnected) return;
        const failed = create("div", "viewer-error");
        failed.textContent = `Unable to open medical viewer: ${error && error.message ? error.message : error}`;
        canvas.replaceWith(failed);
      }
    }

    _withCacheBuster(url) {
      if (typeof url !== "string" || !url) return url;
      const sep = url.includes("?") ? "&" : "?";
      return `${url}${sep}_=${Date.now()}`;
    }
  }

  window.VoxResultViewer = {
    ResultViewer,
  };
})();
