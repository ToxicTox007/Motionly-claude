// Workspace role predicates (Task 19), mirrored from the API
// (apps/api/app/services/auth.py WORKSPACE_ROLES). Pure functions so they can
// be unit-tested and shared by the app shell, the billing page and api-auth.
//
// Roles, most-privileged first: "owner", "admin", "member".
//   - Only the owner manages billing.
//   - Owner or admin manage the workspace: team, API keys, brand kits, templates.
//   - Any active member can edit and render.

/** A missing / unexpected role still proves active membership; treat it as the
 * least-privileged "member" so the UI never over-exposes management actions. */
export function normalizeWorkspaceRole(value) {
  return value === "owner" || value === "admin" ? value : "member";
}

/** Only the workspace owner may manage billing. */
export function canManageBilling(role) {
  return normalizeWorkspaceRole(role) === "owner";
}

/** Owner or admin may manage workspace resources (brand kits, templates, team,
 * API keys). Members may not. */
export function canManageWorkspace(role) {
  const normalized = normalizeWorkspaceRole(role);
  return normalized === "owner" || normalized === "admin";
}
