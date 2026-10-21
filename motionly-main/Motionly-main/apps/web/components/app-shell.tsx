import Image from "next/image";
import { cookies } from "next/headers";
import { type PixelIconName } from "@/components/pixel-icons";
import { ThemeMenu } from "@/components/theme-menu";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";
import { SidebarLink } from "@/components/sidebar-link";
import { ProgressRing } from "@/components/progress-ring";
import {
  CommandPaletteTrigger,
  RegisterCommands,
  type PaletteCommand,
} from "@/components/command-palette";
import { env } from "@/lib/env";
import { usageMeter, type UsageMeter } from "@/lib/usage-meter.mjs";
import { WORKSPACE_COOKIE_NAME } from "@/lib/api-auth";
import {
  canManageBilling,
  canManageWorkspace,
  normalizeWorkspaceRole,
} from "@/lib/workspace-roles.mjs";
import { createClient } from "@/lib/supabase/server";
import type { BillingStatusSummary } from "@motionly/shared";
import { PageTitle } from "@/components/page-title";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { href: "/projects/new", label: "New Video", icon: "new" },
  { href: "/projects", label: "Projects", icon: "project" },
] satisfies Array<{ href: string; label: string; icon: PixelIconName }>;

const settingsItems = [
  { href: "/billing", label: "Billing", icon: "billing" },
  { href: "/account/team", label: "Team", icon: "settings" },
  { href: "/account/api-keys", label: "API Keys", icon: "settings" },
  { href: "/account", label: "Settings", icon: "settings" },
] satisfies Array<{ href: string; label: string; icon: PixelIconName }>;

// Members can't manage billing or API keys, so those nav entries are hidden for
// them (Task 19/24). Billing is owner-only; API keys are owner/admin.
function visibleSettingsItems(showBilling: boolean, showApiKeys: boolean) {
  return settingsItems.filter((item) => {
    if (item.href === "/billing") return showBilling;
    if (item.href === "/account/api-keys") return showApiKeys;
    return true;
  });
}

export async function AppShell({
  children,
  title,
}: {
  children: React.ReactNode;
  title: string;
}) {
  const shell = await loadShellState();
  const visibleSettings = visibleSettingsItems(
    shell.showBilling,
    shell.showApiKeys,
  );
  const allHrefs = [
    ...navItems.map((item) => item.href),
    ...visibleSettings.map((item) => item.href),
  ];
  return (
    <div className="min-h-screen w-full overflow-x-hidden bg-background text-foreground lg:flex lg:h-screen lg:overflow-hidden">
      <PageTitle title={title} />
      <RegisterCommands source="app-shell" commands={shellCommands(shell)} />
      {/* Sidebar */}
      <aside className="flex bg-background lg:m-4 lg:h-[calc(100vh-2rem)] lg:w-64 lg:shrink-0 lg:flex-col lg:rounded-neu-xl lg:bg-surface lg:p-4 lg:shadow-neu-raised-lg">
        {/* Logo */}
        <div className="flex h-16 shrink-0 items-center justify-between px-4 lg:h-auto lg:px-1 lg:pb-4">
          <div className="flex items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
              <Image
                src="/icon.svg"
                alt="Motionly"
                width={28}
                height={28}
                className="size-7"
                priority
              />
            </div>
            <span className="text-lg font-bold tracking-tight text-foreground">
              Motionly
            </span>
          </div>
          <ThemeMenu
            items={[
              { href: "/dashboard", label: "Dashboard" },
              { href: "/projects/new", label: "New Video" },
              { href: "/projects", label: "Projects" },
              ...(shell.showBilling
                ? [{ href: "/billing", label: "Billing" }]
                : []),
              { href: "/account/team", label: "Team" },
              ...(shell.showApiKeys
                ? [{ href: "/account/api-keys", label: "API Keys" }]
                : []),
              { href: "/account", label: "Settings" },
            ]}
          />
        </div>

        {/* Nav */}
        <div className="hidden min-w-0 flex-1 lg:flex lg:flex-col lg:overflow-y-auto">
          <WorkspaceSwitcher
            workspaces={shell.workspaces}
            currentWorkspaceId={shell.workspaceId}
          />
          <p className="mb-2 px-3 text-xs font-semibold text-primary">
            Workspace
          </p>
          <nav className="space-y-1">
            {navItems.map((item) => (
              <SidebarLink key={item.href} {...item} allHrefs={allHrefs} />
            ))}
          </nav>

          <p className="mb-2 mt-6 px-3 text-xs font-semibold text-primary">
            Settings
          </p>
          <nav className="space-y-1">
            {visibleSettings.map((item) => (
              <SidebarLink key={item.href} {...item} allHrefs={allHrefs} />
            ))}
          </nav>

          {/* Usage meter */}
          <div className="mt-auto flex items-center gap-3 rounded-neu-lg bg-surface p-3 shadow-neu-inset">
            {shell.usage.metered && shell.usage.ariaLabel ? (
              <ProgressRing
                value={shell.usage.percent ?? 0}
                label={shell.usage.ariaLabel}
                size={52}
              />
            ) : null}
            <div className="min-w-0">
              <p className="text-xs font-semibold text-foreground">
                {shell.usage.label}
              </p>
              {shell.usage.percentLabel ? (
                <p className="text-xs font-semibold tabular-nums text-muted-foreground">
                  {shell.usage.percentLabel}
                </p>
              ) : null}
            </div>
          </div>
        </div>

        {/* User block */}
        <div className="hidden lg:mt-3 lg:block">
          <div className="flex items-center gap-3 rounded-neu-lg bg-surface p-3 shadow-neu-raised-sm">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground shadow-neu-raised-sm">
              {shell.creatorName.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold leading-tight text-foreground">
                {shell.creatorName}
              </p>
              <p className="truncate text-xs font-medium text-muted-foreground">
                {shell.planName}
              </p>
            </div>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col lg:h-screen lg:overflow-hidden lg:py-4 lg:pr-4">
        {/* Header */}
        <header className="flex min-h-16 items-center justify-between gap-3 bg-surface px-4 sm:px-6 lg:mb-4 lg:rounded-neu-xl lg:px-6 lg:shadow-neu-raised">
          <h1 className="min-w-0 truncate text-xl font-bold tracking-tight text-foreground">
            {title}
          </h1>
          <div className="flex items-center gap-3">
            <span className="hidden items-center rounded-full bg-surface px-3 py-1 text-xs font-semibold tabular-nums text-foreground shadow-neu-raised-sm sm:inline-flex">
              {shell.usage.label}
            </span>
            <CommandPaletteTrigger compact className="lg:hidden" />
            <CommandPaletteTrigger className="hidden lg:inline-flex" />
          </div>
        </header>

        <div
          className="flex-1 overflow-y-auto bg-background px-4 py-6 sm:px-6 lg:rounded-neu-xl lg:p-8"
          tabIndex={0}
          aria-label={`${title} content`}
          role="region"
        >
          <div className="container-dashboard">{children}</div>
        </div>
      </main>
    </div>
  );
}

async function loadShellState() {
  const fallback: {
    creatorName: string;
    planName: string;
    usage: UsageMeter;
    showBilling: boolean;
    showApiKeys: boolean;
    workspaces: Array<{ id: string; name: string }>;
    workspaceId: string | null;
  } = {
    creatorName: "Creator",
    planName: "Free Plan",
    usage: usageMeter({
      renders_used: 0,
      renders_limit: 10,
      remaining_renders: 10,
      percent_used: 0,
    }),
    // Default to showing billing (owner) until we resolve the member's role;
    // skipAuthMiddleware local dev acts as the owner.
    showBilling: true,
    showApiKeys: true,
    // A single (local/dev) workspace: the switcher hides itself below two.
    workspaces: [],
    workspaceId: null,
  };
  if (env.skipAuthMiddleware) return fallback;
  const supabase = await createClient();
  const [
    {
      data: { user },
    },
    {
      data: { session },
    },
  ] = await Promise.all([supabase.auth.getUser(), supabase.auth.getSession()]);
  if (!user || !session?.access_token) return fallback;
  const [{ data: profile }, { data: memberships }] = await Promise.all([
    supabase
      .from("profiles")
      .select("full_name")
      .eq("id", user.id)
      .maybeSingle(),
    supabase
      .from("workspace_members")
      .select("workspace_id, role, workspaces(name)")
      .eq("user_id", user.id)
      .eq("status", "active"),
  ]);
  const selectedWorkspaceId = (await cookies()).get(
    WORKSPACE_COOKIE_NAME,
  )?.value;
  const workspaceId =
    selectedWorkspaceId ??
    (memberships?.length === 1 ? String(memberships[0].workspace_id) : null);
  // Every workspace the viewer belongs to, for the app-shell switcher.
  const workspaces = (memberships ?? []).map((item) => {
    const workspace = Array.isArray(item.workspaces)
      ? item.workspaces[0]
      : item.workspaces;
    return {
      id: String(item.workspace_id),
      name: workspace?.name ?? "Workspace",
    };
  });
  const membership = memberships?.find(
    (item) => String(item.workspace_id) === workspaceId,
  );
  if (!membership?.workspace_id) {
    return {
      ...fallback,
      creatorName: profile?.full_name || fallback.creatorName,
      workspaces,
      workspaceId,
    };
  }
  const role = normalizeWorkspaceRole((membership as { role?: unknown }).role);
  const showBilling = canManageBilling(role);
  const showApiKeys = canManageWorkspace(role);
  const response = await fetch(`${env.apiBaseUrl}/billing/status`, {
    headers: {
      authorization: `Bearer ${session.access_token}`,
      "x-motionly-workspace-id": String(membership.workspace_id),
    },
    cache: "no-store",
  });
  if (!response.ok)
    return {
      ...fallback,
      creatorName: profile?.full_name || fallback.creatorName,
      showBilling,
      showApiKeys,
      workspaces,
      workspaceId,
    };
  const status = (await response.json()) as BillingStatusSummary;
  return {
    creatorName: profile?.full_name || fallback.creatorName,
    planName: `${status.current_plan.name} Plan`,
    usage: usageMeter(status.usage),
    showBilling,
    showApiKeys,
    workspaces,
    workspaceId,
  };
}

function shellCommands(shell: {
  showBilling: boolean;
  showApiKeys: boolean;
}): PaletteCommand[] {
  const commands: PaletteCommand[] = [
    {
      id: "nav-dashboard",
      label: "Dashboard",
      group: "Navigate",
      href: "/dashboard",
      icon: "dashboard",
      keywords: ["home", "overview"],
    },
    {
      id: "nav-projects",
      label: "Projects",
      group: "Navigate",
      href: "/projects",
      icon: "project",
      keywords: ["reels", "videos", "library"],
    },
  ];
  if (shell.showBilling) {
    commands.push({
      id: "nav-billing",
      label: "Billing",
      group: "Navigate",
      href: "/billing",
      icon: "billing",
      keywords: ["plan", "subscription", "invoice", "usage"],
    });
  }
  commands.push({
    id: "nav-team",
    label: "Team",
    group: "Navigate",
    href: "/account/team",
    icon: "settings",
    keywords: ["members", "workspace", "invite"],
  });
  if (shell.showApiKeys) {
    commands.push({
      id: "nav-api-keys",
      label: "API Keys",
      group: "Navigate",
      href: "/account/api-keys",
      icon: "settings",
      keywords: ["tokens", "developer", "integration"],
    });
  }
  commands.push(
    {
      id: "nav-api-reference",
      label: "API reference",
      group: "Navigate",
      href: "/account/api-reference",
      icon: "info",
      keywords: ["docs", "developer", "endpoints"],
    },
    {
      id: "nav-settings",
      label: "Settings",
      group: "Navigate",
      href: "/account",
      icon: "settings",
      keywords: ["account", "profile", "preferences"],
    },
    {
      id: "create-new-video",
      label: "New video",
      group: "Create",
      href: "/projects/new",
      icon: "new",
      keywords: ["reel", "project", "upload", "start"],
    },
    {
      id: "account-logout",
      label: "Log out",
      group: "Account",
      action: "logout",
      icon: "x",
      keywords: ["sign out", "exit"],
    },
  );
  return commands;
}
