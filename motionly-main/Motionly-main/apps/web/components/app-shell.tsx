import {
  CreditCard,
  FolderKanban,
  LayoutDashboard,
  PlusCircle,
  Settings,
  Sparkles,
  UserRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import Link from "next/link";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects/new", label: "New Video", icon: PlusCircle },
  { href: "/projects", label: "Projects", icon: FolderKanban },
];

const settingsItems = [
  { href: "/billing", label: "Billing", icon: CreditCard },
  { href: "/account", label: "Settings", icon: Settings },
];

export function AppShell({ children, title }: { children: React.ReactNode; title: string }) {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Global Left Sidebar */}
      <aside className="flex w-64 flex-col border-r border-border bg-surface">
        <div className="flex h-16 items-center gap-3 px-6 border-b border-border">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Sparkles className="size-5" />
          </div>
          <span className="font-bold text-lg tracking-tight text-foreground">Motionly</span>
        </div>

        <div className="flex-1 overflow-y-auto py-6">
          <nav className="px-3 space-y-1">
            {navItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>

          <div className="mt-8 px-6">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
              Settings
            </p>
          </div>
          <nav className="mt-2 px-3 space-y-1">
            {settingsItems.map((item) => (
              <SidebarItem key={item.href} {...item} />
            ))}
          </nav>
        </div>

        <div className="p-4 border-t border-border">
          <div className="flex items-center gap-3 px-2 py-2">
            <div className="size-8 rounded-full bg-muted flex items-center justify-center">
              <UserRound className="size-4 text-muted-foreground" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold truncate">Creator</p>
              <p className="text-xs text-muted-foreground truncate">Free Plan</p>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        <header className="flex h-16 items-center justify-between border-b border-border bg-surface px-8">
          <h1 className="text-lg font-bold text-foreground">{title}</h1>
          <div className="flex items-center gap-4">
             <div className="h-2 w-32 rounded-full bg-muted overflow-hidden">
                <div className="h-full w-1/3 bg-primary" />
             </div>
             <span className="text-xs font-bold text-muted-foreground">3/10 Exports</span>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto p-8">
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </div>
      </main>
    </div>
  );
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
      className="flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-bold text-muted-foreground transition-all duration-200 hover:bg-muted hover:text-foreground"
    >
      <Icon className="size-5 text-muted-foreground/70" />
      {label}
    </Link>
  );
}
