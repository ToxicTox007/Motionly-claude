import type { ReactNode } from "react";
import { LoadingPixelSkeleton } from "@/components/editor/ui";
import { cn } from "@/lib/utils";

// Building blocks for route loading screens. The shimmer is decorative:
// assistive tech hears one "Loading …" status instead of dozens of empty
// boxes, and aria-busy marks the region until the page replaces it.

export function RouteSkeleton({
  label,
  children,
}: {
  /** Completes "Loading …", e.g. "team". */
  label: string;
  children: ReactNode;
}) {
  return (
    <div aria-busy="true">
      <p role="status" className="sr-only">
        Loading {label}…
      </p>
      <div aria-hidden="true" className="space-y-6">
        {children}
      </div>
    </div>
  );
}

export function CardSkeleton({
  lines = 2,
  action = false,
  className,
}: {
  lines?: number;
  action?: boolean;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "space-y-3 rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6",
        className,
      )}
    >
      <LoadingPixelSkeleton className="h-6 w-40" />
      {Array.from({ length: lines }, (_, index) => (
        <LoadingPixelSkeleton
          key={index}
          className={cn("h-4", index === lines - 1 ? "w-2/3" : "w-full")}
        />
      ))}
      {action ? <LoadingPixelSkeleton className="h-11 w-36" /> : null}
    </section>
  );
}

export function FormSkeleton({
  fields = 2,
  className,
}: {
  fields?: number;
  className?: string;
}) {
  return (
    <section
      data-skeleton="form"
      className={cn(
        "space-y-5 rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6",
        className,
      )}
    >
      <LoadingPixelSkeleton className="h-6 w-44" />
      {Array.from({ length: fields }, (_, index) => (
        <div key={index} className="space-y-2">
          <LoadingPixelSkeleton className="h-3 w-24" />
          <LoadingPixelSkeleton className="h-11 w-full" />
        </div>
      ))}
      <LoadingPixelSkeleton className="h-11 w-40" />
    </section>
  );
}

export function TableSkeleton({
  columns = 4,
  rows = 4,
  className,
}: {
  columns?: number;
  rows?: number;
  className?: string;
}) {
  const cells = Array.from({ length: columns }, (_, index) => index);
  return (
    <section
      data-skeleton="table"
      className={cn(
        "overflow-hidden rounded-neu-lg bg-surface shadow-neu-raised",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-4 border-b border-hairline p-4">
        <LoadingPixelSkeleton className="h-6 w-36" />
        <LoadingPixelSkeleton className="h-9 w-28" />
      </div>
      <div className="flex gap-4 border-b border-hairline bg-muted px-4 py-3">
        {cells.map((cell) => (
          <LoadingPixelSkeleton
            key={cell}
            className={cn("h-4", cell === 0 ? "w-40" : "w-20")}
          />
        ))}
      </div>
      {Array.from({ length: rows }, (_, row) => (
        <div
          key={row}
          className="flex items-center gap-4 border-b border-hairline px-4 py-4 last:border-b-0"
        >
          {cells.map((cell) => (
            <LoadingPixelSkeleton
              key={cell}
              className={cn("h-4", cell === 0 ? "w-48" : "w-16")}
            />
          ))}
        </div>
      ))}
    </section>
  );
}
