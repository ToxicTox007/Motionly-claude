export type AdminIdentity = {
  userId: string;
  workspaceId: string;
};

export type AdminAllowlists = {
  adminUserIds?: string;
  adminWorkspaceIds?: string;
};

export function isAdminIdentityAllowed(
  identity: AdminIdentity,
  allowlists: AdminAllowlists,
): boolean;
