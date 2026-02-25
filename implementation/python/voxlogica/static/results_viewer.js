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

  class ResultViewer {
    constructor(root, options = {}) {
      this.root = root;
      this.onNavigate = options.onNavigate || (() => {});
      this.nv = null;
      this.carouselState = {};
    }

    destroyNiivue() {
      if (this.nv && typeof this.nv.destroy === "function") {
        this.nv.destroy();
      }
      this.nv = null;
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
      const badge = create("span", `chip ${record.status || "neutral"}`, record.status || "unknown");
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
        const canvas = create("canvas", "viewer-niivue");
        wrap.append(toolbar, canvas);
        card.append(wrap);
        this._mountNiivue(canvas, this._withCacheBuster(render.nifti_url));
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

    async _mountNiivue(canvas, url) {
      const ns = window.niivue;
      if (!ns || typeof ns.Niivue !== "function") {
        const missing = create("div", "viewer-error");
        missing.textContent = "Niivue library unavailable in browser.";
        canvas.replaceWith(missing);
        return;
      }
      try {
        this.destroyNiivue();
        const nv = new ns.Niivue({
          dragAndDropEnabled: false,
          isRuler: false,
          show3Dcrosshair: true,
          backColor: [0.02, 0.03, 0.05, 1.0],
        });
        this.nv = nv;
        await nv.attachToCanvas(canvas);
        await nv.loadVolumes([{ url, name: "stored-volume" }]);
        if (ns.SLICE_TYPE && typeof nv.setSliceType === "function") {
          nv.setSliceType(ns.SLICE_TYPE.MULTIPLANAR);
        }
      } catch (error) {
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
