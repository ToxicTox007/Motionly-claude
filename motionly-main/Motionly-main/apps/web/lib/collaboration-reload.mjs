// Task 26 — map a live-collaboration broadcast to the panel the browser should
// reload, and to the revision it should advance its own tracker to.
//
// Realtime delivers only ids and revisions (never content, by design). This
// pure module decides, from one message, WHICH editor panel is now stale so the
// client reloads just that panel (lib/editor-loading.mjs loads each panel
// independently) instead of reloading the whole editor. It also reports the new
// tracked revision so the next save sends a fresh ``expected_revision`` and does
// not conflict needlessly against a change this client already absorbed.
//
// Framework-agnostic and side-effect free so it unit-tests without a browser.

/** The broadcast events the API emits on a project's private channel. */
export const MANIFEST_UPDATED = "manifest_updated";
export const PLAN_REVISION_UPDATED = "plan_revision_updated";

/** The editor panels a broadcast can mark stale. */
export const PANEL_COLLABORATION = "collaboration";
export const PANEL_PLAN = "plan";

/**
 * @typedef {Object} CollaborationBroadcast
 * @property {string} event   one of MANIFEST_UPDATED / PLAN_REVISION_UPDATED
 * @property {Object} [payload]
 * @property {string} [payload.project_id]
 * @property {string} [payload.workspace_id]
 * @property {string} [payload.plan_id]
 * @property {number} [payload.revision]
 */

/**
 * @typedef {Object} PanelReload
 * @property {string} panel     the panel to reload (PANEL_COLLABORATION / PANEL_PLAN)
 * @property {number|null} revision  the revision the client should now track
 * @property {string|null} planId   the affected plan id (plan events only)
 */

/**
 * Resolve a broadcast to the panel to reload and the revision to track, or
 * ``null`` for an unknown/malformed event so the caller ignores it safely.
 *
 * @param {CollaborationBroadcast} message
 * @returns {PanelReload|null}
 */
export function panelReloadForBroadcast(message) {
  if (!message || typeof message.event !== "string") return null;
  const payload = message.payload ?? {};
  const revision = normalizeRevision(payload.revision);
  switch (message.event) {
    case MANIFEST_UPDATED:
      return {
        panel: PANEL_COLLABORATION,
        revision,
        planId: null,
      };
    case PLAN_REVISION_UPDATED:
      return {
        panel: PANEL_PLAN,
        revision,
        planId: typeof payload.plan_id === "string" ? payload.plan_id : null,
      };
    default:
      return null;
  }
}

/**
 * Decide whether to act on a broadcast given the revision this client already
 * tracks. A message whose revision is not newer than ``trackedRevision`` is a
 * stale echo (often this client's own save) and must be ignored — acting on it
 * would reload needlessly and could fight an in-flight edit.
 *
 * @param {number|null} incomingRevision
 * @param {number|null} trackedRevision
 * @returns {boolean}
 */
export function shouldApplyRevision(incomingRevision, trackedRevision) {
  if (incomingRevision === null || incomingRevision === undefined) {
    // No revision to compare: a bare notification — reload to be safe.
    return true;
  }
  if (trackedRevision === null || trackedRevision === undefined) return true;
  return incomingRevision > trackedRevision;
}

function normalizeRevision(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}
