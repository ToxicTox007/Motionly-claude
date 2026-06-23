import {
  CreditCard,
  FolderKanban,
  LayoutDashboard,
  PlusCircle,
  Settings,
  UserRound,
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
      <aside className="pixel-grid-fine glass-surface flex border-b border-border bg-surface/90 lg:h-screen lg:w-72 lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r">
        <div className="flex h-16 shrink-0 items-center justify-between gap-3 border-border px-4 lg:border-b lg:px-6">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center overflow-hidden border border-border bg-foreground">
              <Image src="/icon.svg" alt="Motionly" width={36} height={36} className="size-9" priority />
            </div>
            <span className="text-lg font-black tracking-tight text-foreground">Motionly</span>
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

        <div className="hidden min-w-0 flex-1 px-3 py-3 lg:block lg:overflow-y-auto lg:py-6">
          <nav className="flex gap-2 lg:block lg:space-y-1">
            {navItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>

          <div className="hidden lg:mt-8 lg:block lg:px-6">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
              Settings
            </p>
          </div>
          <nav className="flex gap-2 lg:mt-2 lg:block lg:space-y-1">
            {settingsItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>
        </div>

        <div className="hidden border-t border-border p-4 lg:block">
          <div className="flex items-center gap-3 px-2 py-2">
            <div className="flex size-8 items-center justify-center border border-border bg-muted">
              <UserRound className="size-4 text-muted-foreground" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold truncate">{shell.creatorName}</p>
              <p className="text-xs text-muted-foreground truncate">{shell.planName}</p>
            </div>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col lg:h-screen lg:overflow-hidden">
        <header className="glass-surface pixel-grid-fine flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-6 lg:px-8">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.24em] text-primary">Command center</p>
            <h1 className="text-lg font-bold text-foreground">{title}</h1>
          </div>
          <div className="flex items-center gap-3">
             <div className="pixel-progress h-2.5 w-28 sm:w-32">
                <div className="pixel-progress-fill h-full" style={{ width: `${shell.percentUsed}%` }} />
             </div>
             <span className="text-xs font-bold text-muted-foreground">{shell.rendersUsed}/{shell.rendersLimit} Renders</span>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:p-8">
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
      className="pixel-button flex shrink-0 items-center gap-2 bg-transparent px-3 py-2 text-sm font-semibold text-muted-foreground transition-all duration-200 hover:bg-muted hover:text-foreground lg:gap-3 lg:px-4 lg:py-3"
    >
      <Icon className="size-5 text-muted-foreground/70" />
      {label}
    </Link>
  );
}
