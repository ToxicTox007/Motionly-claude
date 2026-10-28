// Which workspace screen a signed-in viewer gets. Pure, so the decision that
// used to loop forever (FE-01: no membership → context redirects to
// /dashboard → no membership → …) is unit-tested.

export type WorkspaceState =
  | { kind: "none" }
  | { kind: "pick" }
  | { kind: "selected"; workspaceId: string };

/**
 * - `none`: the viewer belongs to no active workspace; show a way forward
 *   instead of calling anything that needs a workspace.
 * - `pick`: several workspaces (or a stale selection); show the picker.
 * - `selected`: the cookie's workspace, or the only one there is.
 */
export function resolveWorkspaceState(
  memberships: ReadonlyArray<{ workspaceId: string }>,
  selectedWorkspaceId: string | null | undefined,
): WorkspaceState {
  if (memberships.length === 0) return { kind: "none" };
  const selected = selectedWorkspaceId?.trim() || null;
  const effective =
    selected ?? (memberships.length === 1 ? memberships[0].workspaceId : null);
  if (effective && memberships.some((m) => m.workspaceId === effective)) {
    return { kind: "selected", workspaceId: effective };
  }
  return { kind: "pick" };
}
