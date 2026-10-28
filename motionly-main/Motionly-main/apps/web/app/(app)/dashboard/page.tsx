import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";
import {
  getActiveMemberships,
  getSessionSnapshot,
  getWorkspaceApiContext,
  WORKSPACE_COOKIE_NAME,
} from "@/lib/api-auth";
import { loadDashboardData, type DashboardData } from "@/lib/dashboard-data";
import { env } from "@/lib/env";
import { knownErrorFor, noticeFor, searchParam } from "@/lib/error-messages";
import { DashboardClient } from "@/components/DashboardClient";
import { NoWorkspaceState } from "@/components/no-workspace-state";
import { WorkspacePicker } from "@/components/workspace-picker";
import { storageGauge } from "@/lib/storage-gauge.mjs";
import { resolveWorkspaceState } from "@/lib/workspace-state";

const WORKSPACE_ERRORS = new Set([
  "workspace_missing",
  "workspace_invalid",
  "workspace_selection_required",
  "workspace_unavailable",
]);
const ACCESS_ERRORS = new Set(["admin_access_denied", "admin_required"]);

export default async function DashboardPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const resolvedSearchParams = (await searchParams) ?? {};
  const errorCode = searchParam(resolvedSearchParams, "error");
  const workspaceError =
    errorCode && WORKSPACE_ERRORS.has(errorCode) ? errorCode : null;
  const accessError =
    errorCode && ACCESS_ERRORS.has(errorCode)
      ? knownErrorFor("dashboard", errorCode)
      : null;
  const notice = noticeFor(
    "dashboard",
    searchParam(resolvedSearchParams, "notice"),
  );

  let workspaceName = "Personal workspace";
  let data: DashboardData = {
    projects: [],
    loadError: false,
    storage: storageGauge(null),
  };

  if (!env.skipAuthMiddleware) {
    const { user } = await getSessionSnapshot();
    if (!user) redirect("/login?next=/dashboard");
    const memberships = await getActiveMemberships(user.id);
    const selectedWorkspaceId = (await cookies()).get(
      WORKSPACE_COOKIE_NAME,
    )?.value;
    const state = resolveWorkspaceState(memberships, selectedWorkspaceId);

    // No workspace at all: never call anything that needs one (FE-01).
    if (state.kind === "none") {
      return (
        <AppShell title="Dashboard">
          <NoWorkspaceState />
        </AppShell>
      );
    }

    if (state.kind === "pick") {
      return (
        <AppShell title="Choose workspace">
          <WorkspacePicker
            workspaces={memberships.map((membership) => ({
              id: membership.workspaceId,
              name: membership.name,
            }))}
            error={workspaceError}
          />
        </AppShell>
      );
    }

    workspaceName =
      memberships.find((item) => item.workspaceId === state.workspaceId)
        ?.name ?? workspaceName;
    const context = await getWorkspaceApiContext();
    if (!context) redirect("/login?next=/dashboard");
    data = await loadDashboardData(context.headers);
  }

  return (
    <AppShell title="Dashboard">
      {accessError ? (
        <Alert tone="danger" className="mb-6">
          {accessError}
        </Alert>
      ) : null}
      {notice ? (
        <Alert tone="success" className="mb-6">
          {notice}
        </Alert>
      ) : null}
      <DashboardClient
        initialProjects={data.projects}
        loadError={data.loadError}
        workspaceName={workspaceName}
        storage={data.storage}
      />
    </AppShell>
  );
}
