function parseAllowlist(value) {
  return new Set(
    String(value ?? "")
      .split(",")
      .map((entry) => entry.trim().toLowerCase())
      .filter(Boolean),
  );
}

// ADMIN_WORKSPACE_IDS designates an operator workspace: only the people who
// run it (owner/admin) are platform admins, never an ordinary member invited
// to it. Mirrors ADMIN_WORKSPACE_ROLES in apps/api/app/api/admin.py.
const ADMIN_WORKSPACE_ROLES = new Set(["owner", "admin"]);

export function isAdminIdentityAllowed(
  { userId, workspaceId, role },
  { adminUserIds, adminWorkspaceIds },
) {
  const normalizedUserId = String(userId ?? "")
    .trim()
    .toLowerCase();
  const normalizedWorkspaceId = String(workspaceId ?? "")
    .trim()
    .toLowerCase();
  const normalizedRole = String(role ?? "")
    .trim()
    .toLowerCase();
  return Boolean(
    (normalizedUserId && parseAllowlist(adminUserIds).has(normalizedUserId)) ||
    (normalizedWorkspaceId &&
      ADMIN_WORKSPACE_ROLES.has(normalizedRole) &&
      parseAllowlist(adminWorkspaceIds).has(normalizedWorkspaceId)),
  );
}
