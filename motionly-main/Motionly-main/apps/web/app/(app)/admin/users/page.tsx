import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { messageFor } from "@/lib/error-messages";
import { CopyLink } from "./CopyLink";
import { RefundPanel } from "./RefundPanel";
import { createResetLink, dismissResetRequest, setUserBan } from "./actions";

type AdminUser = {
  id: string;
  email: string;
  banned: boolean;
  banned_until: string | null;
  last_sign_in_at: string | null;
  created_at: string | null;
};

type ResetRequest = {
  id: string;
  email: string;
  status: string;
  created_at: string;
};

async function searchUsers(
  headers: Record<string, string>,
  email: string,
): Promise<AdminUser[]> {
  if (!email) return [];
  const response = await fetch(
    `${env.apiBaseUrl}/admin/users?email=${encodeURIComponent(email)}`,
    { cache: "no-store", headers },
  );
  if (!response.ok) return [];
  const payload = (await response.json()) as { users: AdminUser[] };
  return payload.users ?? [];
}

async function fetchResetRequests(
  headers: Record<string, string>,
): Promise<ResetRequest[]> {
  const response = await fetch(`${env.apiBaseUrl}/admin/users/reset-requests`, {
    cache: "no-store",
    headers,
  });
  if (!response.ok) return [];
  const payload = (await response.json()) as { requests: ResetRequest[] };
  return payload.requests ?? [];
}

export default async function AdminUsersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_access_denied");

  const params = await searchParams;
  const query = typeof params.q === "string" ? params.q : "";
  const link = typeof params.link === "string" ? params.link : null;
  const errorCode = typeof params.error === "string" ? params.error : null;

  const [users, resetRequests] = await Promise.all([
    searchUsers(context.headers, query),
    fetchResetRequests(context.headers),
  ]);

  return (
    <AppShell title="Users">
      {errorCode && (
        <Alert tone="danger" className="mb-5">
          {messageFor("admin", errorCode)}
        </Alert>
      )}
      {link && (
        <section className="mb-6 rounded-neu-lg bg-surface p-5 shadow-neu-raised">
          <h2 className="mb-2 text-sm font-semibold text-foreground">
            One-time reset link (valid 1 hour)
          </h2>
          <p className="mb-3 text-xs font-medium text-muted-foreground">
            Send this to the user over a trusted channel. It works once and
            expires in an hour; creating a new one invalidates it.
          </p>
          <CopyLink link={link} />
        </section>
      )}

      {/* Billing admin: issue a refund for a workspace. */}
      {/* The no-charge notice follows the billing mode, not billingEnabled. */}
      <RefundPanel demoMode={env.billingMode === "demo"} />

      {/* Search */}
      <section className="mb-8">
        <form method="GET" className="flex items-end gap-2">
          <div className="flex-1">
            <label
              htmlFor="q"
              className="block text-xs font-semibold text-muted-foreground"
            >
              Search users by email
            </label>
            <input
              id="q"
              name="q"
              type="search"
              defaultValue={query}
              placeholder="name@example.com"
              className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
            />
          </div>
          <button
            type="submit"
            className="min-h-11 rounded-neu bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
          >
            Search
          </button>
        </form>

        {query && (
          <div className="mt-4 space-y-3">
            {users.length === 0 ? (
              <p className="text-sm font-medium text-muted-foreground">
                No users match “{query}”.
              </p>
            ) : (
              users.map((user) => (
                <div
                  key={user.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-neu-md bg-surface p-4 shadow-neu-raised-sm"
                >
                  <div>
                    <p className="text-sm font-semibold text-foreground">
                      {user.email}
                    </p>
                    <p className="font-mono text-xs text-muted-foreground">
                      {user.id}
                      {user.banned ? " · BANNED" : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <form action={createResetLink}>
                      <input type="hidden" name="email" value={user.email} />
                      <button
                        type="submit"
                        className="min-h-10 rounded-neu bg-surface px-4 text-xs font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
                      >
                        Create reset link
                      </button>
                    </form>
                    <form action={setUserBan}>
                      <input type="hidden" name="userId" value={user.id} />
                      <input type="hidden" name="email" value={user.email} />
                      <input
                        type="hidden"
                        name="banned"
                        value={user.banned ? "false" : "true"}
                      />
                      <button
                        type="submit"
                        className="min-h-10 rounded-neu bg-surface px-4 text-xs font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
                      >
                        {user.banned ? "Unban" : "Ban"}
                      </button>
                    </form>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </section>

      {/* Pending reset requests */}
      <section>
        <h2 className="mb-3 text-base font-bold">Pending reset requests</h2>
        {resetRequests.length === 0 ? (
          <p className="text-sm font-medium text-muted-foreground">
            No pending requests.
          </p>
        ) : (
          <div className="space-y-3">
            {resetRequests.map((request) => (
              <div
                key={request.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-neu-md bg-surface p-4 shadow-neu-raised-sm"
              >
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    {request.email}
                  </p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {request.created_at}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <form action={createResetLink}>
                    <input type="hidden" name="email" value={request.email} />
                    <input type="hidden" name="requestId" value={request.id} />
                    <button
                      type="submit"
                      className="min-h-10 rounded-neu bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
                    >
                      Create reset link
                    </button>
                  </form>
                  <form action={dismissResetRequest}>
                    <input type="hidden" name="requestId" value={request.id} />
                    <button
                      type="submit"
                      className="min-h-10 rounded-neu bg-surface px-4 text-xs font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
                    >
                      Dismiss
                    </button>
                  </form>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}
