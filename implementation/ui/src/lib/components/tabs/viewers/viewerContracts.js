const clampOpacity = (value, fallback = 1) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(0, Math.min(1, numeric));
};

const safeText = (value) => {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
};

const normalizeImageLayers = (layers = []) =>
  (Array.isArray(layers) ? layers : [])
    .map((layer, index) => {
      if (!layer || typeof layer !== "object") return null;
      const pngUrl = String(layer?.png_url || layer?.url || "").trim();
      if (!pngUrl) return null;
      return {
        url: pngUrl,
        label: String(layer?.label || layer?.name || `Layer ${index + 1}`),
        opacity: clampOpacity(layer?.opacity, index === 0 ? 1 : 0.4),
        visible: layer?.visible !== false,
      };
    })
    .filter(Boolean);

export const normalizeMedicalViewerSources = ({ niftiUrl = "", layers = [], label = "value" } = {}) => {
  const normalizedLayers = (Array.isArray(layers) ? layers : [])
    .map((layer, index) => {
      const source = layer && typeof layer === "object" ? layer : { nifti_url: layer };
      const url = String(source?.nifti_url || source?.url || "").trim();
      if (!url) return null;
      return {
        url,
        label: String(source?.label || source?.name || `Layer ${index + 1}`),
        opacity: clampOpacity(source?.opacity, index === 0 ? 1 : 0.42),
        colormap: String(source?.colormap || "").trim(),
        visible: source?.visible !== false,
      };
    })
    .filter(Boolean);

  if (normalizedLayers.length) return normalizedLayers;

  const singleUrl = String(niftiUrl || "").trim();
  if (!singleUrl) return [];
  return [{ url: singleUrl, label, opacity: 1, colormap: "gray", visible: true }];
};

export const buildMedicalViewerContract = ({ niftiUrl = "", layers = [], label = "value" } = {}) => {
  const sources = normalizeMedicalViewerSources({ niftiUrl, layers, label });
  if (!sources.length) {
    return {
      adapterKey: "message",
      label,
      text: "Medical viewer unavailable.",
    };
  }
  return {
    adapterKey: "medical",
    label,
    ariaLabel: `${label} medical viewer`,
    sources,
  };
};

const normalizeRecordViewerState = ({ state = "empty", record = null, message = "", pageRefresh = null } = {}) => {
  const normalizedState = ["empty", "loading", "error", "record"].includes(String(state || "").toLowerCase())
    ? String(state || "empty").toLowerCase()
    : record && typeof record === "object"
      ? "record"
      : message
        ? "error"
        : "empty";

  return {
    state: normalizedState,
    record: record && typeof record === "object" ? record : null,
    message: String(message || ""),
    pageRefresh:
      pageRefresh && typeof pageRefresh === "object"
        ? {
            nodeId: String(pageRefresh?.nodeId || ""),
            path: String(pageRefresh?.path || ""),
            preserveRecord: pageRefresh?.preserveRecord === true,
          }
        : null,
  };
};

export const buildRecordViewerContract = ({
  label = "value",
  state = "empty",
  record = null,
  message = "",
  pageRefresh = null,
  onNavigate = null,
  fetchPage = null,
  onStatusClick = null,
} = {}) => ({
  adapterKey: "record-viewer",
  label: String(label || "value"),
  onNavigate: typeof onNavigate === "function" ? onNavigate : null,
  fetchPage: typeof fetchPage === "function" ? fetchPage : null,
  onStatusClick: typeof onStatusClick === "function" ? onStatusClick : null,
  ...normalizeRecordViewerState({ state, record, message, pageRefresh }),
});

// Leaf viewers all flow through one contract builder so the host can keep a
// stable adapter instance and update it in place as records change.
export const buildLeafViewerContract = ({ descriptor = {}, summary = {}, render = {}, label = "value", fallbackText = "value" } = {}) => {
  const voxType = String(descriptor?.vox_type || "unavailable").toLowerCase();
  const renderKind = String(render?.kind || "").toLowerCase();

  if (["integer", "number", "boolean", "null"].includes(voxType)) {
    return {
      adapterKey: "scalar",
      label,
      text: safeText(summary.value),
    };
  }

  if (voxType === "string") {
    return {
      adapterKey: "text",
      label,
      text: safeText(summary.value),
    };
  }

  if (voxType === "bytes") {
    return {
      adapterKey: "scalar",
      label,
      text: `${Number(summary.length || 0)} bytes`,
    };
  }

  if (renderKind === "image2d" && String(render?.png_url || "").trim()) {
    return {
      adapterKey: "image",
      label,
      imageUrl: String(render.png_url),
      alt: `${label} preview`,
    };
  }

  if (renderKind === "image-overlay") {
    return {
      adapterKey: "image-overlay",
      label,
      ariaLabel: `${label} overlay`,
      layers: normalizeImageLayers(render?.layers),
    };
  }

  if (renderKind === "medical-volume") {
    return buildMedicalViewerContract({
      niftiUrl: String(render?.nifti_url || ""),
      label,
    });
  }

  if (renderKind === "medical-overlay") {
    return buildMedicalViewerContract({
      layers: render?.layers,
      label,
    });
  }

  return {
    adapterKey: "array",
    label,
    text: String(fallbackText || "value"),
  };
};