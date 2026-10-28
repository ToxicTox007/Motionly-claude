"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { useId, useState } from "react";
import { LogoutForm } from "@/components/logout-form";
import { SubmitButton } from "@/components/submit-button";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";
import { activeHref } from "@/lib/nav-active.mjs";
import { cn } from "@/lib/utils";
import { useFocusTrap } from "@/lib/useFocusTrap";

type NavItem = { href?: string; label: string; onClick?: () => void };

/** What a signed-in viewer can do from the menu on narrow screens. */
type AccountControls = {
  workspaces: Array<{ id: string; name: string }>;
  currentWorkspaceId: string | null;
  usageLabel?: string;
};

/**
 * The navigation drawer for screens below `lg`, where the sidebar is hidden.
 * Links are client-side (next/link) and mark the current page; the drawer
 * closes when the route changes. Given `account`, it also carries what the
 * sidebar offers on desktop: the workspace switcher, usage and Log out.
 */
export function MobileNav({
  items,
  account,
  className,
}: {
  items?: NavItem[];
  account?: AccountControls;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const focusTrapRef = useFocusTrap(open, () => setOpen(false));
  const panelId = useId();
  const pathname = usePathname() ?? "";
  const hrefs = (items ?? []).flatMap((item) => (item.href ? [item.href] : []));
  const current = activeHref(pathname, hrefs);

  // A navigation (link, Back, a redirect) closes the menu.
  const [shownFor, setShownFor] = useState(pathname);
  if (shownFor !== pathname) {
    setShownFor(pathname);
    if (open) setOpen(false);
  }

  return (
    <div className={cn("relative lg:hidden", className)}>
      <button
        type="button"
        aria-label="Menu"
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 min-w-11 items-center justify-center rounded-neu bg-surface text-foreground shadow-neu-raised transition-shadow hover:shadow-neu-raised-lg active:shadow-neu-pressed"
      >
        {open ? (
          <X className="size-[18px]" strokeWidth={1.5} aria-hidden="true" />
        ) : (
          <Menu className="size-[18px]" strokeWidth={1.5} aria-hidden="true" />
        )}
      </button>

      {open ? (
        <>
          {/* Pointer-only backdrop (hidden from assistive tech and Tab). */}
          <div
            aria-hidden="true"
            className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          />

          <div
            ref={focusTrapRef}
            id={panelId}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation menu"
            className="fixed inset-y-0 right-0 z-50 flex w-[min(21rem,88vw)] animate-slide-in-right flex-col overflow-y-auto bg-background shadow-neu-raised-lg"
          >
            <div className="flex shrink-0 items-center justify-between px-4 py-4">
              <p className="text-sm font-bold text-foreground">Menu</p>
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => setOpen(false)}
                className="flex size-11 items-center justify-center rounded-neu-sm bg-surface text-foreground shadow-neu-raised-sm transition-shadow hover:shadow-neu-raised active:shadow-neu-pressed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                <X
                  className="size-[18px]"
                  strokeWidth={1.5}
                  aria-hidden="true"
                />
              </button>
            </div>

            {account && account.workspaces.length > 1 ? (
              <div className="px-3">
                <WorkspaceSwitcher
                  workspaces={account.workspaces}
                  currentWorkspaceId={account.currentWorkspaceId}
                />
              </div>
            ) : null}

            <nav aria-label="Primary" className="flex-1 space-y-2 p-3">
              {items?.map((item) =>
                item.href ? (
                  <Link
                    key={item.label}
                    href={item.href}
                    aria-current={current === item.href ? "page" : undefined}
                    className={cn(
                      "flex min-h-11 items-center gap-3 rounded-neu px-3 py-2.5 text-sm font-semibold text-foreground transition-shadow",
                      current === item.href
                        ? "bg-surface shadow-neu-inset-sm"
                        : "hover:bg-surface hover:shadow-neu-raised-sm",
                    )}
                    onClick={() => setOpen(false)}
                  >
                    {item.label}
                  </Link>
                ) : (
                  <button
                    key={item.label}
                    type="button"
                    className="flex min-h-11 w-full items-center gap-3 rounded-neu px-3 py-2.5 text-left text-sm font-semibold text-foreground transition-shadow hover:bg-surface hover:shadow-neu-raised-sm"
                    onClick={() => {
                      item.onClick?.();
                      setOpen(false);
                    }}
                  >
                    {item.label}
                  </button>
                ),
              )}
            </nav>

            {account ? (
              <div className="space-y-3 border-t border-hairline p-4">
                {account.usageLabel ? (
                  <p className="text-xs font-semibold text-muted-foreground">
                    {account.usageLabel}
                  </p>
                ) : null}
                <LogoutForm>
                  <SubmitButton
                    variant="secondary"
                    className="w-full"
                    pendingLabel="Logging out…"
                  >
                    Log out
                  </SubmitButton>
                </LogoutForm>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
