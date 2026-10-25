import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { canManageWorkspace } from "@/lib/workspace-roles.mjs";
import { AiModelsClient } from "./AiModelsClient";

// Task 32 — the workspace "AI models" settings section (plan Section 2.11).
// Owners and admins set the workspace default, the member allow-list and the
// lock per capability; members see the current choices read-only. The settings
// are fetched and saved client-side through the browser-authenticated API
// client (as the editor panels do), so the server page only resolves the
// workspace id and the viewer's role.
export default async function AiModelsPage() {
  const context = await getWorkspaceApiContext();
  const role = context?.role ?? "owner";
  const canManage = canManageWorkspace(role);
  const workspaceId =
    context?.workspaceId ?? "00000000-0000-4000-8000-000000000002";

  return (
    <AppShell title="AI models">
      <div className="space-y-5">
        <Alert tone="info" title="Choose the models your workspace uses">
          Your operator supplies the models. Pick one per task for the whole
          workspace; any project can override the choice unless you lock it.
          {canManage ? null : (
            <> Only the workspace owner or an admin can change these.</>
          )}
        </Alert>
        <AiModelsClient workspaceId={workspaceId} canManage={canManage} />
      </div>
    </AppShell>
  );
}
