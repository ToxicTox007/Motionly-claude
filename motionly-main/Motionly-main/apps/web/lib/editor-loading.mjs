// Task 13 / B27: independent editor panel loading.
//
// The editor opens even when one panel's request fails. Each panel resolves on
// its own: a failure becomes a per-panel error state (with a Retry affordance
// and an accessible message) instead of rejecting the whole page load.
//
// This module is framework-agnostic and pure so it can be unit tested without a
// browser or a running API.

/**
 * @template T
 * @typedef {{ status: "ok", data: T } | { status: "error", error: string }} PanelResult
 */

/**
 * Human-readable, accessible error message for a failed panel.
 * @param {string} panelLabel
 * @param {unknown} error
 * @returns {string}
 */
export function panelErrorMessage(panelLabel, error) {
  const detail =
    error instanceof Error && error.message
      ? error.message
      : String(error ?? "");
  const suffix = detail ? ` ${detail}` : "";
  return `${panelLabel} could not be loaded.${suffix} Use Retry to try again.`.trim();
}

/**
 * Load a single panel. Never throws: a rejected loader becomes an error result.
 * @template T
 * @param {string} panelLabel
 * @param {() => Promise<T>} loader
 * @returns {Promise<PanelResult<T>>}
 */
export async function loadPanel(panelLabel, loader) {
  try {
    const data = await loader();
    return { status: "ok", data };
  } catch (error) {
    return { status: "error", error: panelErrorMessage(panelLabel, error) };
  }
}

/**
 * Settle every panel independently. One panel failing does not reject the
 * others, so the editor still renders with the panels that did load.
 * @template {Record<string, () => Promise<unknown>>} L
 * @param {L} loaders
 * @returns {Promise<{ [K in keyof L]: PanelResult<Awaited<ReturnType<L[K]>>> }>}
 */
export async function settlePanels(loaders) {
  const entries = Object.entries(loaders);
  const results = await Promise.all(
    entries.map(([label, loader]) => loadPanel(label, loader)),
  );
  /** @type {Record<string, unknown>} */
  const out = {};
  entries.forEach(([label], index) => {
    out[label] = results[index];
  });
  return /** @type {any} */ (out);
}

/**
 * True when at least one panel loaded. The editor shell should still open as
 * long as something resolved (e.g. the project itself).
 * @param {Record<string, PanelResult<unknown>>} panels
 * @returns {boolean}
 */
export function hasAnyPanelLoaded(panels) {
  return Object.values(panels).some((panel) => panel.status === "ok");
}

/**
 * Labels of panels that failed to load, for surfacing retry affordances.
 * @param {Record<string, PanelResult<unknown>>} panels
 * @returns {string[]}
 */
export function failedPanels(panels) {
  return Object.entries(panels)
    .filter(([, panel]) => panel.status === "error")
    .map(([label]) => label);
}
