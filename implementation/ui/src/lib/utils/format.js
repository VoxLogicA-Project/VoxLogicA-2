export const fmtSeconds = (value) => {
  if (!Number.isFinite(value)) return "-";
  if (value < 0.001) return `${(value * 1000).toFixed(2)} ms`;
  return `${value.toFixed(3)} s`;
};

export const fmtPercent = (value) => {
  if (!Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
};

export const fmtBytes = (value) => {
  if (!Number.isFinite(value) || value < 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(idx === 0 ? 0 : 2)} ${units[idx]}`;
};

export const median = (values) => {
  const nums = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!nums.length) return NaN;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 === 1 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
};

export const statusChipClass = (status) => {
  const normalized = String(status || "idle").toLowerCase();
  if (["running", "completed", "failed", "killed"].includes(normalized)) {
    return normalized;
  }
  return "neutral";
};
