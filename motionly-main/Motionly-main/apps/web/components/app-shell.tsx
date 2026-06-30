import {
  CreditCard,
  FolderKanban,
  LayoutDashboard,
  PlusCircle,
  Settings,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { ThemeMenu } from "@/components/theme-menu";
import { env } from "@/lib/env";
import { createClient } from "@/lib/supabase/server";
import type { BillingStatusSummary } from "@motionly/shared";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects/new", label: "New Video", icon: PlusCircle },
  { href: "/projects", label: "Projects", icon: FolderKanban },
];

const settingsItems = [
  { href: "/billing", label: "Billing", icon: CreditCard },
  { href: "/account", label: "Settings", icon: Settings },
];

export async function AppShell({ children, title }: { children: React.ReactNode; title: string }) {
  const shell = await loadShellState();
  return (
    <div className="min-h-screen w-full overflow-x-hidden bg-background text-foreground lg:flex lg:h-screen lg:overflow-hidden">
      {/* Sidebar */}
      <aside className="bg-background border-r-4 border-foreground flex border-b lg:h-screen lg:w-60 lg:shrink-0 lg:flex-col lg:border-b-0">
        {/* Logo */}
        <div className="flex h-16 shrink-0 items-center justify-between border-b-2 border-foreground px-4">
          <div className="flex items-center gap-3">
            <div className="flex size-8 items-center justify-center overflow-hidden rounded border-2 border-foreground bg-primary shrink-0 shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
              <Image src="/icon.svg" alt="Motionly" width={24} height={24} className="size-6 invert dark:invert-0" priority />
            </div>
            <span className="text-lg font-black uppercase tracking-tight text-foreground">Motionly</span>
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
          <p className="text-[10px] font-black uppercase tracking-widest text-foreground px-3 mb-2 font-mono">
            Workspace
          </p>
          <nav className="flex gap-1 lg:block lg:space-y-1">
            {navItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>

          <p className="text-[10px] font-black uppercase tracking-widest text-foreground px-3 mt-6 mb-2 font-mono">
            Settings
          </p>
          <nav className="flex gap-1 lg:block lg:space-y-1">
            {settingsItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>

          {/* Usage bar */}
          <div className="mx-0 mt-auto pt-6 border-t-2 border-foreground/20">
            <div className="flex items-center justify-between mb-1">
              <p className="text-xs font-mono font-bold text-foreground">
                {shell.rendersUsed}/{shell.rendersLimit} renders
              </p>
              <p className="text-xs font-mono font-bold text-foreground">
                {shell.percentUsed}%
              </p>
            </div>
            <div className="progress-bar mt-1">
              <div
                className="progress-fill"
                style={{ width: `${shell.percentUsed}%` }}
              />
            </div>
          </div>
        </div>

        {/* User block */}
        <div className="hidden border-t-2 border-foreground p-4 lg:block">
          <div className="flex items-center gap-3">
            <div className="rounded border-2 border-foreground size-8 bg-primary flex items-center justify-center text-primary-foreground text-xs font-black shrink-0 shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
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
        <header className="bg-surface border-b-4 border-foreground min-h-16 flex items-center justify-between px-4 sm:px-6 lg:px-8">
          <h1 className="text-xl font-black uppercase tracking-tight">{title}</h1>
          <div className="flex items-center gap-3">
            <span className="badge badge-primary">
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
  const [{ data: profile }, { data: membership }] = await Promise.all([
    supabase.from("profiles").select("full_name").eq("id", user.id).maybeSingle(),
    supabase.from("workspace_members").select("workspace_id").eq("user_id", user.id).eq("status", "active").limit(1).maybeSingle(),
  ]);
  if (!membership?.workspace_id) return { ...fallback, creatorName: profile?.full_name || fallback.creatorName };
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
  icon: Icon,
  label,
}: {
  href: string;
  icon: any;
  label: string;
}) {
  return (
    <Link
      href={href}
      className="flex items-center gap-3 px-3 py-2 text-sm font-bold rounded-md border-2 border-transparent text-muted-foreground hover:bg-surface hover:text-foreground hover:border-foreground hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] transition-all"
    >
      <Icon className="size-4 shrink-0" strokeWidth={2} />
      {label}
    </Link>
  );
}
