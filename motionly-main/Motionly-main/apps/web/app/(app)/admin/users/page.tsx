import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { messageFor, noticeFor, searchParam } from "@/lib/error-messages";
import { serverApiFetch } from "@/lib/server-api";
import { RefundPanel } from "./RefundPanel";
import { ResetLinkPanel } from "./ResetLinkPanel";
import { dismissResetRequest, setUserBan } from "./actions";

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
): Promise<AdminUser[] | null> {
  if (!email) return [];
  const result = await serverApiFetch<{ users?: AdminUser[] }>(
    `/admin/users?email=${encodeURIComponent(email)}`,
    { headers },
  );
  return result.ok && Array.isArray(result.data?.users)
    ? result.data.users
    : null;
}

async function fetchResetRequests(
  headers: Record<string, string>,
): Promise<ResetRequest[] | null> {
  const result = await serverApiFetch<{ requests?: ResetRequest[] }>(
    "/admin/users/reset-requests",
    { headers },
  );
  return result.ok && Array.isArray(result.data?.requests)
    ? result.data.requests
    : null;
}

export default async function AdminUsersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_access_denied");

  const params = await searchParams;
  // ?q= stays a GET so a search is linkable (it carries the email searched
  // for). The recovery link itself never travels in a URL (ResetLinkPanel).
  const query = typeof params.q === "string" ? params.q : "";
  const errorCode = typeof params.error === "string" ? params.error : null;
  const done = noticeFor("admin", searchParam(params, "done"));

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
      {done ? (
        <Alert tone="success" className="mb-5">
          {done}
        </Alert>
      ) : null}

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
            {users === null ? (
              <p role="alert" className="text-sm font-medium text-danger">
                User search is unavailable right now. Try again in a moment.
              </p>
            ) : users.length === 0 ? (
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
                    <ResetLinkPanel email={user.email} />
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
        {resetRequests === null ? (
          <p role="alert" className="text-sm font-medium text-danger">
            Reset requests could not be loaded right now. Refresh to try again.
          </p>
        ) : resetRequests.length === 0 ? (
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
                  <ResetLinkPanel
                    email={request.email}
                    requestId={request.id}
                    primary
                  />
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
