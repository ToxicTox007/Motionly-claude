import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/** Page numbers to show: the first, the last and the current page's
 * neighbours, with null where a gap is collapsed. */
function visiblePages(page: number, pageCount: number): Array<number | null> {
  const pages = new Set([1, pageCount, page - 1, page, page + 1]);
  const sorted = [...pages]
    .filter((value) => value >= 1 && value <= pageCount)
    .sort((a, b) => a - b);
  const result: Array<number | null> = [];
  sorted.forEach((value, index) => {
    if (index > 0 && value - sorted[index - 1] > 1) result.push(null);
    result.push(value);
  });
  return result;
}

const linkClass =
  "inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-neu bg-surface px-3 text-sm font-semibold tabular-nums text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed";

/**
 * Link-based pagination: every page is a real URL, so it works without
 * JavaScript and back/forward restores the page. Renders nothing for a single
 * page.
 */
export function Pagination({
  page,
  pageCount,
  hrefFor,
  className,
}: {
  page: number;
  pageCount: number;
  hrefFor: (page: number) => string;
  className?: string;
}) {
  if (pageCount <= 1) return null;
  return (
    <nav
      aria-label="Pagination"
      className={cn(
        "flex flex-wrap items-center justify-center gap-2",
        className,
      )}
    >
      {page > 1 ? (
        <Link
          href={hrefFor(page - 1)}
          rel="prev"
          aria-label="Previous page"
          className={linkClass}
        >
          <ChevronLeft className="size-4" aria-hidden="true" />
          <span aria-hidden="true">Previous</span>
        </Link>
      ) : null}
      <ul className="flex flex-wrap items-center gap-2">
        {visiblePages(page, pageCount).map((value, index) =>
          value === null ? (
            <li
              key={`gap-${index}`}
              aria-hidden="true"
              className="px-1 text-sm font-semibold text-muted-foreground"
            >
              …
            </li>
          ) : (
            <li key={value}>
              <Link
                href={hrefFor(value)}
                aria-label={`Page ${value}`}
                aria-current={value === page ? "page" : undefined}
                className={cn(
                  linkClass,
                  value === page &&
                    "bg-primary text-primary-foreground shadow-neu-glow hover:shadow-neu-glow",
                )}
              >
                {value}
              </Link>
            </li>
          ),
        )}
      </ul>
      {page < pageCount ? (
        <Link
          href={hrefFor(page + 1)}
          rel="next"
          aria-label="Next page"
          className={linkClass}
        >
          <span aria-hidden="true">Next</span>
          <ChevronRight className="size-4" aria-hidden="true" />
        </Link>
      ) : null}
    </nav>
  );
}
