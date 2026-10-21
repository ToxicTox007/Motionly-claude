"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";
import { activeHref } from "@/lib/nav-active.mjs";
import { cn } from "@/lib/utils";

// A single navigation row in the app-shell sidebar. The active row is sunken
// with a teal icon well and aria-current="page"; idle rows rise on hover. The
// link's text content stays exactly the label (the icon is an aria-hidden SVG)
// so the browser suite's text-match navigation keeps working.
export function SidebarLink({
  href,
  label,
  icon,
  allHrefs,
}: {
  href: string;
  label: string;
  icon: PixelIconName;
  allHrefs: string[];
}) {
  const pathname = usePathname();
  const active = activeHref(pathname ?? "", allHrefs) === href;

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex min-h-11 items-center gap-3 rounded-neu-md px-3 py-2 text-sm font-semibold transition-shadow duration-200",
        active
          ? "bg-surface text-foreground shadow-neu-inset-sm"
          : "bg-surface text-muted-foreground shadow-none hover:text-foreground hover:shadow-neu-raised-sm",
      )}
    >
      <span
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-neu-sm transition-colors",
          active
            ? "bg-primary text-primary-foreground shadow-neu-glow"
            : "bg-muted text-muted-foreground shadow-neu-inset-sm",
        )}
      >
        <PixelIcon name={icon} aria-hidden="true" className="size-4" />
      </span>
      {label}
    </Link>
  );
}
