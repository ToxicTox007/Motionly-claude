import { AlertTriangle, Briefcase, KeyRound, Mail, UserRound } from "lucide-react";
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
        <section className="pixel-panel pixel-corners flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-black uppercase tracking-widest text-primary">
              Profile
            </p>
            <h2 className="mt-2 text-3xl font-black tracking-tight">
              Account settings
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Manage your creator identity, workspace access, and password.
            </p>
          </div>
          <div className="flex items-center gap-3 border border-border bg-muted p-3">
            <div className="flex size-10 items-center justify-center border border-border bg-surface">
              <UserRound className="size-5 text-primary" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-black">{fullName || "Creator"}</p>
              <p className="truncate text-xs text-muted-foreground">{email}</p>
            </div>
          </div>
        </section>

        {notice ? (
          <div className="border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-700">
            {notice}
          </div>
        ) : null}
        {error ? (
          <div className="flex gap-2 border border-red-200 bg-red-50 p-3 text-sm font-bold text-red-700">
            <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
            {error}
          </div>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <form action={updateProfile} className="pixel-panel space-y-5 p-5">
            <div className="flex items-center gap-2">
              <UserRound className="size-5 text-primary" aria-hidden="true" />
              <h3 className="text-lg font-black">Public profile</h3>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label>
                <span className="mb-2 block text-xs font-black uppercase tracking-widest text-muted-foreground">
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
                <span className="mb-2 block text-xs font-black uppercase tracking-widest text-muted-foreground">
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
              <span className="mb-2 block text-xs font-black uppercase tracking-widest text-muted-foreground">
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
            <div className="pixel-panel space-y-4 p-5">
              <div className="flex items-center gap-2">
                <Briefcase className="size-5 text-primary" aria-hidden="true" />
                <h3 className="text-lg font-black">Workspace</h3>
              </div>
              <Fact label="Name" value={workspaceName} />
              <Fact label="Role" value={workspaceRole} />
              <Fact label="Email" value={email} icon={Mail} />
            </div>

            <form action={logout} className="pixel-panel p-5">
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

        <form action={updatePassword} className="pixel-panel max-w-2xl space-y-5 p-5">
          <div className="flex items-center gap-2">
            <KeyRound className="size-5 text-primary" aria-hidden="true" />
            <h3 className="text-lg font-black">Password</h3>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label>
              <span className="mb-2 block text-xs font-black uppercase tracking-widest text-muted-foreground">
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
              <span className="mb-2 block text-xs font-black uppercase tracking-widest text-muted-foreground">
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
    <div className="border border-border bg-surface p-3">
      <p className="flex items-center gap-2 text-xs font-black uppercase tracking-widest text-muted-foreground">
        {Icon ? <Icon className="size-3.5" aria-hidden="true" /> : null}
        {label}
      </p>
      <p className="mt-1 truncate text-sm font-bold">{value || "Not set"}</p>
    </div>
  );
}
