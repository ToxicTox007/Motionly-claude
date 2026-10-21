export type WorkspaceRole = "owner" | "admin" | "member";

export function normalizeWorkspaceRole(value: unknown): WorkspaceRole;

export function canManageBilling(role: unknown): boolean;

export function canManageWorkspace(role: unknown): boolean;
