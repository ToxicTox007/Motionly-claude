import Image from "next/image";
import { cookies } from "next/headers";
import Link from "next/link";
import { Progress } from "@/components/brutal-ui";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";
import { ThemeMenu } from "@/components/theme-menu";
import { env } from "@/lib/env";
import { WORKSPACE_COOKIE_NAME } from "@/lib/api-auth";
import { createClient } from "@/lib/supabase/server";
import type { BillingStatusSummary } from "@motionly/shared";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { href: "/projects/new", label: "New Video", icon: "new" },
  { href: "/projects", label: "Projects", icon: "project" },
] satisfies Array<{ href: string; label: string; icon: PixelIconName }>;

const settingsItems = [
  { href: "/billing", label: "Billing", icon: "billing" },
  { href: "/account", label: "Settings", icon: "settings" },
] satisfies Array<{ href: string; label: string; icon: PixelIconName }>;

export async function AppShell({ children, title }: { children: React.ReactNode; title: string }) {
  const shell = await loadShellState();
  return (
    <div className="min-h-screen w-full overflow-x-hidden bg-background text-foreground lg:flex lg:h-screen lg:overflow-hidden">
      {/* Sidebar */}
      <aside className="border-r-4 border-foreground bg-surface flex border-b lg:h-screen lg:w-64 lg:shrink-0 lg:flex-col lg:border-b-0">
        {/* Logo */}
        <div className="flex h-16 shrink-0 items-center justify-between border-b-4 border-foreground bg-background px-4">
          <div className="flex items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded border-2 border-foreground bg-primary shadow-brutal-sm">
              <Image src="/icon.svg" alt="Motionly" width={28} height={28} className="size-7" priority />
            </div>
            <span className="text-lg font-black uppercase text-foreground">Motionly</span>
          </div>
          <ThemeMenu
            items={[
              { href: "/dashboard", label: "Dashboard" },
              { href: "/projects/new", label: "New Video" },
              { href: "/projects", label: "Projects" },
              { href: "/billing", label: "Billing" },
              { href: "/account", label: "Settings" },
            ]}
          />
        </div>

        {/* Nav */}
        <div className="hidden min-w-0 flex-1 px-3 py-4 lg:flex lg:flex-col lg:overflow-y-auto">
          <p className="mb-2 px-3 font-mono text-[10px] font-black uppercase text-primary">
            Workspace
          </p>
          <nav className="flex gap-1 lg:block lg:space-y-1">
            {navItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>

          <p className="mb-2 mt-6 px-3 font-mono text-[10px] font-black uppercase text-primary">
            Settings
          </p>
          <nav className="flex gap-1 lg:block lg:space-y-1">
            {settingsItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>

          {/* Usage bar */}
          <div className="mx-0 mt-auto border-t-2 border-foreground pt-6">
            <div className="flex items-center justify-between mb-1">
              <p className="text-xs font-mono font-bold text-foreground">
                {shell.rendersUsed}/{shell.rendersLimit} renders
              </p>
              <p className="text-xs font-mono font-bold text-foreground">
                {shell.percentUsed}%
              </p>
            </div>
            <Progress value={shell.percentUsed} className="mt-2" />
          </div>
        </div>

        {/* User block */}
        <div className="hidden border-t-4 border-foreground bg-background p-4 lg:block">
          <div className="flex items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded border-2 border-foreground bg-primary text-xs font-black text-primary-foreground shadow-brutal-sm">
              {shell.creatorName.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold truncate leading-tight">{shell.creatorName}</p>
              <p className="text-xs font-mono font-medium text-muted-foreground truncate">{shell.planName}</p>
            </div>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col lg:h-screen lg:overflow-hidden">
        {/* Header */}
        <header className="flex min-h-16 items-center justify-between border-b-4 border-foreground bg-surface px-4 sm:px-6 lg:px-8">
          <h1 className="min-w-0 truncate text-xl font-black uppercase">{title}</h1>
          <div className="flex items-center gap-3">
            <span className="badge badge-primary shadow-brutal-sm">
              {shell.rendersUsed}/{shell.rendersLimit} renders
            </span>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:p-8 bg-background">
          <div className="container-dashboard">{children}</div>
        </div>
      </main>
    </div>
  );
}

async function loadShellState() {
  const fallback = {
    creatorName: "Creator",
    planName: "Free Plan",
    rendersUsed: 0,
    rendersLimit: 10,
    percentUsed: 0,
  };
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") return fallback;
  const supabase = await createClient();
  const [{ data: { user } }, { data: { session } }] = await Promise.all([
    supabase.auth.getUser(),
    supabase.auth.getSession(),
  ]);
  if (!user || !session?.access_token) return fallback;
  const [{ data: profile }, { data: memberships }] = await Promise.all([
    supabase.from("profiles").select("full_name").eq("id", user.id).maybeSingle(),
    supabase
      .from("workspace_members")
      .select("workspace_id, workspaces(name)")
      .eq("user_id", user.id)
      .eq("status", "active"),
  ]);
  const selectedWorkspaceId = (await cookies()).get(WORKSPACE_COOKIE_NAME)?.value;
  const workspaceId =
    selectedWorkspaceId ??
    (memberships?.length === 1 ? String(memberships[0].workspace_id) : null);
  const membership = memberships?.find(
    (item) => String(item.workspace_id) === workspaceId,
  );
  if (!membership?.workspace_id) {
    return { ...fallback, creatorName: profile?.full_name || fallback.creatorName };
  }
  const response = await fetch(`${env.apiBaseUrl}/billing/status`, {
    headers: {
      authorization: `Bearer ${session.access_token}`,
      "x-motionly-user-id": user.id,
      "x-motionly-workspace-id": String(membership.workspace_id),
    },
    cache: "no-store",
  });
  if (!response.ok) return { ...fallback, creatorName: profile?.full_name || fallback.creatorName };
  const status = await response.json() as BillingStatusSummary;
  return {
    creatorName: profile?.full_name || fallback.creatorName,
    planName: `${status.current_plan.name} Plan`,
    rendersUsed: status.usage.renders_used,
    rendersLimit: status.usage.renders_limit,
    percentUsed: status.usage.percent_used,
  };
}

function SidebarItem({
  href,
  icon,
  label,
}: {
  href: string;
  icon: PixelIconName;
  label: string;
}) {
  return (
    <Link
      href={href}
      className="flex min-h-11 items-center gap-3 rounded border-2 border-transparent px-3 py-2 text-sm font-black uppercase text-muted-foreground transition-all hover:border-foreground hover:bg-background hover:text-foreground hover:shadow-brutal-sm"
    >
      <PixelIcon name={icon} className="size-4 shrink-0" />
      {label}
    </Link>
  );
}
