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
        className="flex min-h-11 min-w-11 items-center justify-center rounded border-2 border-foreground bg-surface-elevated text-foreground shadow-brutal-sm transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:bg-surface hover:shadow-brutal active:translate-x-0 active:translate-y-0 active:shadow-none"
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
            className="fixed inset-0 z-40 cursor-default bg-background/75"
            onClick={() => setOpen(false)}
          />

          {/* Slide-in panel */}
          <div
            ref={focusTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation menu"
            className="fixed inset-y-0 right-0 z-50 flex w-[min(21rem,88vw)] animate-slide-in-right flex-col overflow-y-auto border-l-4 border-foreground bg-surface shadow-brutal-lg"
          >
            {/* Panel header */}
            <div className="flex shrink-0 items-center justify-between border-b-4 border-foreground bg-background px-4 py-4">
              <p className="text-sm font-black uppercase text-foreground">
                Menu
              </p>
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => setOpen(false)}
                className="flex size-9 items-center justify-center rounded border-2 border-foreground bg-surface text-foreground shadow-brutal-sm transition-colors hover:bg-surface-elevated focus-visible:outline-none"
              >
                <X className="size-[18px]" strokeWidth={1.5} />
              </button>
            </div>

            {/* Nav items */}
            <nav className="flex-1 space-y-2 p-3">
              {items?.map((item) =>
                item.href ? (
                  <a
                    key={item.label}
                    href={item.href}
                    className="flex min-h-11 items-center gap-3 rounded border-2 border-transparent px-3 py-2.5 text-sm font-black uppercase text-foreground transition-colors hover:border-foreground hover:bg-background hover:shadow-brutal-sm"
                    onClick={() => setOpen(false)}
                  >
                    {item.label}
                  </a>
                ) : (
                  <button
                    key={item.label}
                    type="button"
                    className="flex min-h-11 w-full items-center gap-3 rounded border-2 border-transparent px-3 py-2.5 text-left text-sm font-black uppercase text-foreground transition-colors hover:border-foreground hover:bg-background hover:shadow-brutal-sm"
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
            <div className="shrink-0 border-t-4 border-foreground bg-background p-4">
              <p className="mb-2 px-1 font-mono text-xs font-black uppercase text-muted-foreground">
                Appearance
              </p>
              <div className="flex items-center gap-1 rounded border-2 border-foreground bg-surface p-1 shadow-brutal-sm">
                {/* Light option */}
                <button
                  type="button"
                  aria-label="Switch to light mode"
                  aria-pressed={theme === "light"}
                  onClick={() => applyTheme("light")}
                  className={cn(
                    "flex min-h-10 flex-1 items-center justify-center gap-2 rounded px-3 py-2 text-sm font-black uppercase transition-all duration-[120ms]",
                    theme === "light"
                      ? "bg-primary text-primary-foreground"
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
                    "flex min-h-10 flex-1 items-center justify-center gap-2 rounded px-3 py-2 text-sm font-black uppercase transition-all duration-[120ms]",
                    theme === "dark"
                      ? "bg-primary text-primary-foreground"
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
