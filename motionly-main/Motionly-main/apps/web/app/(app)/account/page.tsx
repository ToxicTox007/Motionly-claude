import { Briefcase, KeyRound, Mail, UserRound } from "lucide-react";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { EditorialLearningSettings } from "@/components/editorial-learning-settings";
import { Button } from "@/components/button";
import { Alert } from "@/components/brutal-ui";
import { getActiveMemberships, getSessionSnapshot } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { messageFor, searchParam } from "@/lib/error-messages";
import { requireWorkspaceApiContext } from "@/lib/require-context";
import { LogoutForm } from "@/components/logout-form";
import { updatePassword, updateProfile } from "./actions";

export default async function AccountPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await searchParams) ?? {};
  const notice =
    typeof params.updated === "string"
      ? "Profile updated."
      : typeof params.password === "string"
        ? "Password updated. Every other session was signed out."
        : null;
  const errorCode = searchParam(params, "error");
  // Mapped copy only: a crafted ?error= never puts its text on the page.
  const error = errorCode ? messageFor("account", errorCode) : null;
  let email = "local@motionly.dev";
  let fullName = "Local creator";
  let company = "";
  let role = "";
  let workspaceName = "Personal workspace";
  let workspaceRole = "owner";
  let workspaceId = "00000000-0000-4000-8000-000000000002";

  if (!env.skipAuthMiddleware) {
    const { supabase, user } = await getSessionSnapshot();
    if (!user) redirect("/login?next=%2Faccount");
    email = user.email ?? email;
    // The workspace shown and configured here is the one selected for this
    // session (cookie-aware), not whichever membership a query returns first.
    const [{ data: profile }, context, memberships] = await Promise.all([
      supabase
        .from("profiles")
        .select("full_name,company,role")
        .eq("id", user.id)
        .maybeSingle(),
      requireWorkspaceApiContext("/account"),
      getActiveMemberships(user.id),
    ]);
    fullName = String(profile?.full_name ?? user.user_metadata.full_name ?? "");
    company = String(profile?.company ?? "");
    role = String(profile?.role ?? "");
    workspaceId = context.workspaceId;
    workspaceRole = context.role;
    workspaceName =
      memberships.find((item) => item.workspaceId === context.workspaceId)
        ?.name ?? workspaceName;
  }

  return (
    <AppShell title="Account">
      <div className="space-y-6">
        {/* Profile header */}
        <section className="flex flex-col items-start gap-6 rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg sm:flex-row sm:p-8">
          <div className="flex size-14 shrink-0 items-center justify-center rounded-full bg-primary text-xl font-bold text-primary-foreground shadow-neu-glow">
            {(fullName || "C").charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <span className="text-xs font-semibold text-primary">Profile</span>
            <h2 className="mt-1 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              Account settings
            </h2>
            <p className="mt-1 text-sm font-medium text-muted-foreground">
              Manage your creator identity, workspace access, and password.
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <div className="min-w-0 text-right">
              <p className="truncate text-sm font-semibold text-foreground">
                {fullName || "Creator"}
              </p>
              <p className="truncate text-xs font-medium text-muted-foreground">
                {email}
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3 py-1 text-xs font-semibold capitalize text-accent-foreground shadow-neu-raised-sm">
              {workspaceRole}
            </span>
          </div>
        </section>

        {notice ? <Alert tone="success">{notice}</Alert> : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          {/* Public profile form */}
          <form
            action={updateProfile}
            className="space-y-5 rounded-neu-lg bg-surface p-6 shadow-neu-raised"
          >
            <div className="flex items-center gap-2">
              <UserRound className="size-5 text-primary" aria-hidden="true" />
              <h3 className="text-lg font-bold text-foreground">
                Public profile
              </h3>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label>
                <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
                  Full name
                </span>
                <input
                  name="fullName"
                  defaultValue={fullName}
                  maxLength={120}
                  className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
                />
              </label>
              <label>
                <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
                  Role
                </span>
                <input
                  name="role"
                  defaultValue={role}
                  maxLength={80}
                  placeholder="Founder, editor, creator"
                  className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
                />
              </label>
            </div>
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
                Company or channel
              </span>
              <input
                name="company"
                defaultValue={company}
                maxLength={120}
                className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
              />
            </label>
            <Button type="submit">Save profile</Button>
          </form>

          <aside className="space-y-5">
            {/* Workspace sidebar card */}
            <div className="space-y-4 rounded-neu-lg bg-surface p-5 shadow-neu-raised">
              <div className="flex items-center gap-2">
                <Briefcase className="size-5 text-primary" aria-hidden="true" />
                <h3 className="text-lg font-bold text-foreground">Workspace</h3>
              </div>
              <Fact label="Name" value={workspaceName} />
              <Fact label="Role" value={workspaceRole} />
              <Fact label="Email" value={email} icon={Mail} />
            </div>

            {/* Signs out here and tells other tabs (components/logout-form). */}
            <LogoutForm className="rounded-neu-lg bg-surface p-5 shadow-neu-raised">
              <h3 className="text-lg font-bold text-foreground">Session</h3>
              <p className="mt-2 text-sm font-medium text-muted-foreground">
                Log out on this browser before switching accounts.
              </p>
              <Button type="submit" variant="secondary" className="mt-4 w-full">
                Log out
              </Button>
            </LogoutForm>
          </aside>
        </div>

        {/* Password form */}
        <form
          action={updatePassword}
          className="max-w-2xl space-y-5 rounded-neu-lg bg-surface p-6 shadow-neu-raised"
        >
          <div className="flex items-center gap-2">
            <KeyRound className="size-5 text-primary" aria-hidden="true" />
            <h3 className="text-lg font-bold text-foreground">Password</h3>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <label>
              <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
                Current password
              </span>
              <input
                name="currentPassword"
                type="password"
                required
                autoComplete="current-password"
                className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
              />
            </label>
            <label>
              <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
                New password
              </span>
              <input
                name="password"
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                aria-describedby="password-policy"
                className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
              />
            </label>
            <label>
              <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
                Confirm password
              </span>
              <input
                name="confirmPassword"
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
              />
            </label>
          </div>
          <p
            id="password-policy"
            className="text-xs font-medium text-muted-foreground"
          >
            At least 8 characters with some variety, not a common password, and
            not containing your email name. Changing it signs out every other
            session.
          </p>
          <Button type="submit" variant="secondary">
            Update password
          </Button>
        </form>

        <EditorialLearningSettings workspaceId={workspaceId} />
      </div>
    </AppShell>
  );
}

function Fact({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon?: typeof Mail;
}) {
  return (
    <div className="rounded-neu-md bg-surface p-3 shadow-neu-inset-sm">
      <p className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
        {Icon ? <Icon className="size-3.5" aria-hidden="true" /> : null}
        {label}
      </p>
      <p className="mt-1 truncate text-sm font-semibold text-foreground">
        {value || "Not set"}
      </p>
    </div>
  );
}
