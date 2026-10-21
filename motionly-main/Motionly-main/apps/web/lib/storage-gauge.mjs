// Task 13 / B23: the dashboard storage gauge shows the real workspace quota.
// Numbers come from the API StorageQuotaService (GET /storage/quota). When the
// quota is unknown (no max), the gauge is labelled "Not metered" rather than
// showing a fabricated percentage.

const UNITS = ["B", "KB", "MB", "GB", "TB"];

/**
 * @param {number} bytes
 * @returns {string}
 */
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded =
    value >= 10 || unit === 0 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${UNITS[unit]}`;
}

/**
 * @typedef {{ used_bytes?: number, max_bytes?: number, percentage?: number }} Quota
 * @typedef {{ metered: boolean, label: string, percent: number | null,
 *   percentLabel: string | null, ariaLabel: string }} StorageGauge
 */

/**
 * Build the gauge view model from a real quota, or null when the quota could
 * not be loaded.
 * @param {Quota | null | undefined} quota
 * @returns {StorageGauge}
 */
export function storageGauge(quota) {
  const max =
    quota && Number.isFinite(quota.max_bytes) ? Number(quota.max_bytes) : 0;
  const used =
    quota && Number.isFinite(quota.used_bytes) ? Number(quota.used_bytes) : 0;

  if (!quota || max <= 0) {
    return {
      metered: false,
      label: quota
        ? `${formatBytes(used)} used · not metered`
        : "Storage usage unavailable",
      percent: null,
      percentLabel: null,
      ariaLabel: "Storage usage is not metered",
    };
  }

  const rawPercent =
    typeof quota.percentage === "number"
      ? quota.percentage
      : (used / max) * 100;
  const percent = Math.max(0, Math.min(100, Math.round(rawPercent)));
  return {
    metered: true,
    label: `${formatBytes(used)} of ${formatBytes(max)}`,
    percent,
    percentLabel: `${percent}%`,
    ariaLabel: `Storage used: ${formatBytes(used)} of ${formatBytes(max)} (${percent}%)`,
  };
}
