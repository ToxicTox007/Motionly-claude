import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { LoadErrorAlert } from "@/components/load-error-alert";
import { getAdminApiContext } from "@/lib/api-auth";
import { serverApiFetch } from "@/lib/server-api";
import type {
  RegistrySnapshot,
  RouteHealth,
  SecretMetadata,
} from "@/lib/admin-inference";
import { AdminAiClient } from "./AdminAiClient";

// Admin AI configuration (plan Task 23, Section 2.7). This server component
// enforces admin access and calls the Tier 3 admin inference API with the
// admin's token. No secret value is ever fetched: the secret list returns
// metadata only, so nothing sensitive is passed to the client component.

const PREFIX = "/admin/inference";

function adminHeaders(headers: Record<string, string>) {
  return {
    ...headers,
    ...(process.env.ADMIN_API_TOKEN
      ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
      : {}),
  };
}

async function fetchJson<T>(
  path: string,
  headers: Record<string, string>,
): Promise<T | null> {
  const result = await serverApiFetch<T>(path, {
    headers: adminHeaders(headers),
  });
  return result.ok ? result.data : null;
}

export default async function AdminAiPage() {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_access_denied");

  const [snapshot, health, secretList] = await Promise.all([
    fetchJson<RegistrySnapshot>(`${PREFIX}/registry`, context.headers),
    fetchJson<RouteHealth>(`${PREFIX}/routes/health`, context.headers),
    fetchJson<{ secrets: SecretMetadata[] }>(
      `${PREFIX}/secrets`,
      context.headers,
    ),
  ]);

  if (!snapshot) {
    // The API did not answer: that says nothing about the feature flag.
    return (
      <AppShell title="AI Configuration">
        <LoadErrorAlert
          title="The AI configuration is unavailable"
          body="Motionly could not load the inference registry from the API. Nothing was changed; try again in a moment."
        />
      </AppShell>
    );
  }

  if (!snapshot.database_enabled) {
    return (
      <AppShell title="AI Configuration">
        <div className="rounded-neu-lg bg-surface p-8 text-center shadow-neu-inset">
          <h2 className="text-lg font-bold text-foreground">
            The database inference registry is not enabled
          </h2>
          <p className="mt-2 text-sm font-medium text-muted-foreground">
            Set the registry-database feature flag to manage adapters, models,
            routes and secrets here. Until then the file registry is the source
            of truth and this surface stays read-only.
          </p>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell title="AI Configuration">
      <p className="mb-6 max-w-3xl text-sm font-medium text-muted-foreground leading-relaxed">
        Add and test AI adapters, models and routes at runtime. File-managed
        entries are read-only. Secrets are write-only and never shown again
        after they are saved. A validated save bumps the registry version and
        reloads the runtime, so a new route becomes available without a code
        change or image rebuild.
      </p>
      <AdminAiClient
        snapshot={snapshot}
        health={health}
        secrets={secretList?.secrets ?? []}
      />
    </AppShell>
  );
}
