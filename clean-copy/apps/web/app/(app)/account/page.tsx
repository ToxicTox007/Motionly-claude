import { AlertTriangle, Briefcase, CheckCircle2, KeyRound, Mail, UserRound } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/button";
import { createClient } from "@/lib/supabase/server";
import { logout } from "../../(auth)/actions";
import { updatePassword, updateProfile } from "./actions";

export default async function AccountPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await searchParams) ?? {};
  const notice = typeof params.updated === "string"
    ? "Profile updated."
    : typeof params.password === "string"
      ? "Password updated."
      : null;
  const error = typeof params.error === "string" ? params.error.replaceAll("_", " ") : null;
  let email = "local@motionly.dev";
  let fullName = "Local creator";
  let company = "";
  let role = "";
  let workspaceName = "Personal workspace";
  let workspaceRole = "owner";

  if (process.env.SKIP_AUTH_MIDDLEWARE !== "true") {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (user) {
      email = user.email ?? email;
      const { data: profile } = await supabase
        .from("profiles")
        .select("full_name,company,role")
        .eq("id", user.id)
        .maybeSingle();
      fullName = String(profile?.full_name ?? user.user_metadata.full_name ?? "");
      company = String(profile?.company ?? "");
      role = String(profile?.role ?? "");

      const { data: membership } = await supabase
        .from("workspace_members")
        .select("role,workspaces(name)")
        .eq("user_id", user.id)
        .eq("status", "active")
        .limit(1)
        .maybeSingle();

      const workspace = Array.isArray(membership?.workspaces)
        ? membership?.workspaces[0]
        : membership?.workspaces;
      workspaceName = workspace?.name ?? workspaceName;
      workspaceRole = membership?.role ?? workspaceRole;
    }
  }

  return (
    <AppShell title="Account">
      <div className="space-y-6">
        {/* Profile header */}
        <section className="card-elevated rounded-2xl p-6 flex flex-col sm:flex-row gap-6 items-start">
          <div className="flex size-14 rounded-full bg-gradient-to-br from-violet-500 to-cyan-400 items-center justify-center text-white text-xl font-black shrink-0">
            {(fullName || "C").charAt(0).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <span className="badge badge-primary mb-3 inline-flex">Profile</span>
            <h2 className="text-4xl font-black tracking-tight">Account settings</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Manage your creator identity, workspace access, and password.
            </p>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <div className="min-w-0 text-right">
              <p className="truncate text-sm font-black">{fullName || "Creator"}</p>
              <p className="truncate text-xs text-muted-foreground">{email}</p>
            </div>
            <span className="badge badge-accent capitalize">{workspaceRole}</span>
          </div>
        </section>

        {notice ? (
          <div className="rounded-lg border-l-4 border-emerald-500 bg-emerald-500/8 p-4 flex gap-3 mb-4 text-emerald-300">
            <CheckCircle2 className="size-5 shrink-0 mt-0.5" aria-hidden="true" />
            <p className="text-sm font-bold">{notice}</p>
          </div>
        ) : null}
        {error ? (
          <div className="rounded-lg border-l-4 border-danger bg-danger/8 p-4 flex gap-3 mb-4 text-red-300">
            <AlertTriangle className="size-5 shrink-0 mt-0.5" aria-hidden="true" />
            <p className="text-sm font-bold">{error}</p>
          </div>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          {/* Public profile form */}
          <form action={updateProfile} className="card rounded-xl p-6 space-y-5">
            <div className="flex items-center gap-2">
              <UserRound className="size-5 text-primary" aria-hidden="true" />
              <h3 className="text-lg font-black">Public profile</h3>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label>
                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">
                  Full name
                </span>
                <input
                  name="fullName"
                  defaultValue={fullName}
                  maxLength={120}
                  className="pixel-input h-11 w-full px-3"
                />
              </label>
              <label>
                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">
                  Role
                </span>
                <input
                  name="role"
                  defaultValue={role}
                  maxLength={80}
                  placeholder="Founder, editor, creator"
                  className="pixel-input h-11 w-full px-3"
                />
              </label>
            </div>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">
                Company or channel
              </span>
              <input
                name="company"
                defaultValue={company}
                maxLength={120}
                className="pixel-input h-11 w-full px-3"
              />
            </label>
            <Button type="submit" className="h-11">
              Save profile
            </Button>
          </form>

          <aside className="space-y-5">
            {/* Workspace sidebar card */}
            <div className="card rounded-xl p-5 space-y-4">
              <div className="flex items-center gap-2">
                <Briefcase className="size-5 text-primary" aria-hidden="true" />
                <h3 className="text-lg font-black">Workspace</h3>
              </div>
              <Fact label="Name" value={workspaceName} />
              <Fact label="Role" value={workspaceRole} />
              <Fact label="Email" value={email} icon={Mail} />
            </div>

            {/* Logout form */}
            <form action={logout} className="card rounded-xl p-5">
              <h3 className="text-lg font-black">Session</h3>
              <p className="mt-2 text-sm text-muted-foreground">
                Log out on this browser before switching accounts.
              </p>
              <Button type="submit" variant="secondary" className="mt-4 h-11 w-full">
                Log out
              </Button>
            </form>
          </aside>
        </div>

        {/* Password form */}
        <form action={updatePassword} className="card rounded-xl p-6 space-y-5 max-w-2xl">
          <div className="flex items-center gap-2">
            <KeyRound className="size-5 text-primary" aria-hidden="true" />
            <h3 className="text-lg font-black">Password</h3>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label>
              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">
                New password
              </span>
              <input
                name="password"
                type="password"
                minLength={8}
                className="pixel-input h-11 w-full px-3"
              />
            </label>
            <label>
              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">
                Confirm password
              </span>
              <input
                name="confirmPassword"
                type="password"
                minLength={8}
                className="pixel-input h-11 w-full px-3"
              />
            </label>
          </div>
          <Button type="submit" variant="secondary" className="h-11">
            Update password
          </Button>
        </form>
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
    <div className="rounded-lg border border-border bg-muted p-3">
      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        {Icon ? <Icon className="size-3.5" aria-hidden="true" /> : null}
        {label}
      </p>
      <p className="text-sm font-semibold mt-1 truncate">{value || "Not set"}</p>
    </div>
  );
}
