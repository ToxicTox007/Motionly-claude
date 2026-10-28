import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";
import { LoadErrorAlert } from "@/components/load-error-alert";
import { getBillingStatus } from "@/lib/billing";
import { requireWorkspaceApiContext } from "@/lib/require-context";
import { serverApiFetch } from "@/lib/server-api";
import { canManageWorkspace } from "@/lib/workspace-roles.mjs";
import { ApiKeysClient } from "./ApiKeysClient";
import type { ApiKeyView } from "./actions";

async function loadKeys(
  headers: Record<string, string>,
): Promise<ApiKeyView[]> {
  const result = await serverApiFetch<{ keys?: ApiKeyView[] }>("/api-keys", {
    headers,
  });
  return result.ok && Array.isArray(result.data?.keys) ? result.data.keys : [];
}

export default async function ApiKeysPage() {
  const context = await requireWorkspaceApiContext("/account/api-keys");
  // Owners and admins only; members may not manage API keys (Task 19/24).
  const canManage = canManageWorkspace(context.role);
  // The plan and the keys are independent: load them together.
  const [status, keys] = await Promise.all([
    getBillingStatus(),
    canManage ? loadKeys(context.headers) : Promise.resolve([]),
  ]);
  const planKnown = status !== null;
  const hasApiAccess = status?.entitlements.api_access ?? false;

  return (
    <AppShell title="API Keys">
      <div className="space-y-5">
        {!canManage ? (
          <Alert tone="info" title="Owner or admin only">
            Only workspace owners and admins can manage API keys.
          </Alert>
        ) : !planKnown ? (
          // Unknown plan: neither claim the feature is locked nor unlock it.
          <LoadErrorAlert
            title="API key access is unavailable"
            body="Motionly could not load this workspace's plan, so it cannot tell whether API keys are included. Existing keys keep working; try again in a moment."
          />
        ) : !hasApiAccess ? (
          <Alert tone="warning" title="API access is an Agency-plan feature">
            Upgrade to the Agency plan to create API keys and automate Motionly.{" "}
            <Link href="/billing" className="font-bold underline">
              View plans
            </Link>
            .
          </Alert>
        ) : (
          <>
            <Alert tone="info" title="Programmatic access">
              API keys authenticate automation to the Motionly API. See the{" "}
              <Link
                href="/account/api-reference"
                className="font-bold underline"
              >
                API reference
              </Link>{" "}
              for the endpoints a key can call.
            </Alert>
            <ApiKeysClient initialKeys={keys} />
          </>
        )}
      </div>
    </AppShell>
  );
}
