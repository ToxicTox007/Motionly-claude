export type AdminIdentity = {
  userId: string;
  workspaceId: string;
  /** Workspace role; only owner/admin of an ADMIN_WORKSPACE_IDS workspace qualify. */
  role?: string;
};

export type AdminAllowlists = {
  adminUserIds?: string;
  adminWorkspaceIds?: string;
};

export function isAdminIdentityAllowed(
  identity: AdminIdentity,
  allowlists: AdminAllowlists,
): boolean;
