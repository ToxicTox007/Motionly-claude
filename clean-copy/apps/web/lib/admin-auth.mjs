function parseAllowlist(value) {
  return new Set(
    String(value ?? "")
      .split(",")
      .map((entry) => entry.trim().toLowerCase())
      .filter(Boolean),
  );
}

export function isAdminIdentityAllowed(
  { userId, workspaceId },
  { adminUserIds, adminWorkspaceIds },
) {
  const normalizedUserId = String(userId ?? "").trim().toLowerCase();
  const normalizedWorkspaceId = String(workspaceId ?? "").trim().toLowerCase();
  return (
    (normalizedUserId && parseAllowlist(adminUserIds).has(normalizedUserId)) ||
    (normalizedWorkspaceId &&
      parseAllowlist(adminWorkspaceIds).has(normalizedWorkspaceId))
  );
}
