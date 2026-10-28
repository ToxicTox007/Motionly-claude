"use client";

import { Menu, X } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useFocusTrap } from "@/lib/useFocusTrap";

export function ThemeMenu({
  items,
  className,
}: {
  items?: Array<{ href?: string; label: string; onClick?: () => void }>;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const focusTrapRef = useFocusTrap(open, () => setOpen(false));

  return (
    <div className={cn("relative lg:hidden", className)}>
      {/* Trigger button */}
      <button
        type="button"
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 min-w-11 items-center justify-center rounded-neu bg-surface text-foreground shadow-neu-raised transition-shadow hover:shadow-neu-raised-lg active:shadow-neu-pressed"
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
            className="fixed inset-0 z-40 cursor-default bg-background/60 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          />

          {/* Slide-in panel */}
          <div
            ref={focusTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation menu"
            className="fixed inset-y-0 right-0 z-50 flex w-[min(21rem,88vw)] animate-slide-in-right flex-col overflow-y-auto bg-background shadow-neu-raised-lg"
          >
            {/* Panel header */}
            <div className="flex shrink-0 items-center justify-between px-4 py-4">
              <p className="text-sm font-bold text-foreground">Menu</p>
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => setOpen(false)}
                className="flex size-9 items-center justify-center rounded-neu-sm bg-surface text-foreground shadow-neu-raised-sm transition-shadow hover:shadow-neu-raised active:shadow-neu-pressed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
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
                    className="flex min-h-11 items-center gap-3 rounded-neu px-3 py-2.5 text-sm font-semibold text-foreground transition-shadow hover:bg-surface hover:shadow-neu-raised-sm"
                    onClick={() => setOpen(false)}
                  >
                    {item.label}
                  </a>
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
          </div>
        </>
      ) : null}
    </div>
  );
}
