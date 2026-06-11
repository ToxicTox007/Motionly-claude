import {
  CreditCard,
  FolderKanban,
  LayoutDashboard,
  PlusCircle,
  Settings,
  Sparkles,
  UserRound,
} from "lucide-react";
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
    <div className="min-h-screen w-full overflow-x-hidden bg-background text-foreground lg:flex lg:h-screen lg:overflow-hidden">
      <aside className="pixel-grid-fine flex border-b border-border bg-surface/95 backdrop-blur lg:h-screen lg:w-64 lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r">
        <div className="flex h-16 shrink-0 items-center gap-3 border-border px-4 lg:border-b lg:px-6">
          <div className="pixel-button flex size-8 items-center justify-center bg-primary text-primary-foreground">
            <Sparkles className="size-5" />
          </div>
          <span className="text-lg font-black tracking-tight text-foreground">Motionly</span>
        </div>

        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto px-3 py-3 lg:block lg:overflow-y-auto lg:py-6">
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
              <p className="text-sm font-bold truncate">Creator</p>
              <p className="text-xs text-muted-foreground truncate">Free Plan</p>
            </div>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col lg:h-screen lg:overflow-hidden">
        <header className="pixel-grid-fine flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-border bg-surface/95 px-4 py-3 backdrop-blur sm:px-6 lg:px-8">
          <h1 className="text-lg font-bold text-foreground">{title}</h1>
          <div className="flex items-center gap-3">
             <div className="pixel-progress h-2 w-28 sm:w-32">
                <div className="pixel-progress-fill h-full w-1/3" />
             </div>
             <span className="text-xs font-bold text-muted-foreground">3/10 Exports</span>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:p-8">
          <div className="container-dashboard">{children}</div>
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
      className="flex shrink-0 items-center gap-2 border border-transparent px-3 py-2 text-sm font-bold text-muted-foreground transition-all duration-200 hover:border-primary/30 hover:bg-muted hover:text-foreground lg:gap-3 lg:px-4 lg:py-3"
    >
      <Icon className="size-5 text-muted-foreground/70" />
      {label}
    </Link>
  );
}
