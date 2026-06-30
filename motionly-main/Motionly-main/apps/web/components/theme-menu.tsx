"use client";

import { Menu, Moon, Sun, X } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useFocusTrap } from "@/lib/useFocusTrap";

function initialTheme(): "light" | "dark" {
  if (typeof document !== "undefined") {
    const rootTheme = document.documentElement.dataset.theme;
    if (rootTheme === "light" || rootTheme === "dark") return rootTheme;
  }
  if (typeof window !== "undefined") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return "light";
}

export function ThemeMenu({
  items,
  className,
}: {
  items?: Array<{ href?: string; label: string; onClick?: () => void }>;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(initialTheme);
  const focusTrapRef = useFocusTrap(open, () => setOpen(false));

  function applyTheme(nextTheme: "light" | "dark") {
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("motionly-theme", nextTheme);
  }

  return (
    <div className={cn("relative lg:hidden", className)}>
      {/* Trigger button */}
      <button
        type="button"
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="rounded-lg bg-surface-elevated border border-border hover:border-primary/40 transition-all min-h-[40px] min-w-[40px] flex items-center justify-center text-foreground"
      >
        {open ? (
          <X className="size-[18px]" strokeWidth={1.5} />
        ) : (
          <Menu className="size-[18px]" strokeWidth={1.5} />
        )}
      </button>

      {open ? (
        <>
          {/* Backdrop */}
          <button
            type="button"
            aria-label="Close menu overlay"
            className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm cursor-default"
            onClick={() => setOpen(false)}
          />

          {/* Slide-in panel */}
          <div
            ref={focusTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation menu"
            className="fixed inset-y-0 right-0 z-50 w-[min(20rem,88vw)] bg-surface/95 backdrop-blur-2xl border-l border-border shadow-xl flex flex-col overflow-y-auto animate-slide-in-right"
          >
            {/* Panel header */}
            <div className="flex items-center justify-between px-4 py-4 border-b border-border shrink-0">
              <p className="text-sm font-semibold text-muted-foreground tracking-wide">
                Menu
              </p>
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => setOpen(false)}
                className="size-8 rounded-full flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
              >
                <X className="size-[18px]" strokeWidth={1.5} />
              </button>
            </div>

            {/* Nav items */}
            <nav className="flex-1 p-3 space-y-0.5">
              {items?.map((item) =>
                item.href ? (
                  <a
                    key={item.label}
                    href={item.href}
                    className="rounded-lg hover:bg-muted transition-colors px-3 py-2.5 flex items-center gap-3 font-medium text-sm text-foreground"
                    onClick={() => setOpen(false)}
                  >
                    {item.label}
                  </a>
                ) : (
                  <button
                    key={item.label}
                    type="button"
                    className="w-full rounded-lg hover:bg-muted transition-colors px-3 py-2.5 flex items-center gap-3 font-medium text-sm text-foreground text-left"
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

            {/* Theme toggle pill at bottom */}
            <div className="p-4 border-t border-border shrink-0">
              <p className="text-xs font-medium text-muted-foreground mb-2 px-1">
                Appearance
              </p>
              <div className="flex items-center gap-1 rounded-xl bg-muted/50 p-1">
                {/* Light option */}
                <button
                  type="button"
                  aria-label="Switch to light mode"
                  aria-pressed={theme === "light"}
                  onClick={() => applyTheme("light")}
                  className={cn(
                    "flex-1 flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-[120ms]",
                    theme === "light"
                      ? "bg-primary/15 text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Sun className="size-[16px]" strokeWidth={1.5} />
                  <span>Light</span>
                </button>

                {/* Dark option */}
                <button
                  type="button"
                  aria-label="Switch to dark mode"
                  aria-pressed={theme === "dark"}
                  onClick={() => applyTheme("dark")}
                  className={cn(
                    "flex-1 flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-[120ms]",
                    theme === "dark"
                      ? "bg-primary/15 text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Moon className="size-[16px]" strokeWidth={1.5} />
                  <span>Dark</span>
                </button>
              </div>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
