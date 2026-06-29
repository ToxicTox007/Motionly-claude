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
      <button
        type="button"
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="pixel-button inline-flex min-h-11 min-w-11 items-center justify-center bg-surface text-foreground"
      >
        {open ? <X className="size-4" /> : <Menu className="size-4" />}
      </button>

      {open ? (
        <>
          <button
            type="button"
            aria-label="Close menu overlay"
            className="fixed inset-0 z-40 cursor-default bg-foreground/30 backdrop-blur-[2px]"
            onClick={() => setOpen(false)}
          />
          <div
            ref={focusTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation menu"
            className="pixel-panel-strong fixed inset-y-0 right-0 z-50 flex w-[min(20rem,88vw)] max-w-full flex-col overflow-y-auto border-y-0 border-r-0 p-3 shadow-[-6px_0_0_hsl(var(--shadow-color)/0.08)] sm:inset-y-3 sm:right-3 sm:h-auto sm:max-h-[calc(100dvh-1.5rem)] sm:border sm:shadow-[8px_8px_0_hsl(var(--shadow-color)/0.12)]"
          >
            <div className="mb-3 flex items-center justify-between border-b border-border pb-3">
              <p className="text-sm font-black uppercase tracking-[0.18em] text-muted-foreground">
                Menu
              </p>
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => setOpen(false)}
                className="pixel-button flex min-h-10 min-w-10 items-center justify-center bg-surface text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="space-y-1">
              {items?.map((item) =>
                item.href ? (
                  <a
                    key={item.label}
                    href={item.href}
                    className="flex min-h-11 items-center justify-between border border-transparent px-3 text-sm font-semibold text-foreground transition hover:border-border hover:bg-muted"
                    onClick={() => setOpen(false)}
                  >
                    {item.label}
                  </a>
                ) : (
                  <button
                    key={item.label}
                    type="button"
                    className="flex min-h-11 w-full items-center justify-between border border-transparent px-3 text-left text-sm font-semibold text-foreground transition hover:border-border hover:bg-muted"
                    onClick={() => {
                      item.onClick?.();
                      setOpen(false);
                    }}
                  >
                    {item.label}
                  </button>
                ),
              )}
            </div>
            <div className="mt-auto border-t border-border pt-2">
              <button
                type="button"
                className="flex min-h-11 w-full items-center justify-between border border-transparent px-3 text-left text-sm font-semibold text-foreground transition hover:border-border hover:bg-muted"
                onClick={() => applyTheme(theme === "dark" ? "light" : "dark")}
              >
                <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>
                {theme === "dark" ? (
                  <Sun className="size-4" />
                ) : (
                  <Moon className="size-4" />
                )}
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
