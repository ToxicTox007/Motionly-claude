"use client";

import Link from "next/link";
import { useEffect } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/button";

// The project's own boundary: the project or its active upload could not be
// read. It never shows the error's message; server errors carry an opaque
// digest that matches the server log instead. retry() re-fetches the
// segment's server content before re-rendering (reset() alone would re-render
// the same failed payload).
export default function ProjectError({
  error,
  retry,
}: {
  error: unknown;
  reset: () => void;
  retry: () => void;
}) {
  const digest =
    error instanceof Error
      ? (error as Error & { digest?: string }).digest
      : undefined;

  useEffect(() => {
    console.error("Project workspace failed to load", digest ?? "");
  }, [digest]);

  return (
    <main
      id="main"
      className="flex min-h-screen items-center justify-center bg-background px-4 py-12"
    >
      <section
        role="alert"
        aria-labelledby="project-error-title"
        className="w-full max-w-lg rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
      >
        <div className="flex items-start gap-4">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-neu text-warning shadow-neu-inset-sm">
            <AlertTriangle
              className="size-5"
              strokeWidth={1.75}
              aria-hidden="true"
            />
          </div>
          <div className="min-w-0">
            <h1
              id="project-error-title"
              className="text-xl font-bold tracking-tight text-foreground"
            >
              Project data is unavailable
            </h1>
            <p className="mt-2 text-sm font-medium text-muted-foreground">
              Motionly could not load the authoritative project state. Your
              saved timeline has not been replaced or cleared.
            </p>
            {digest ? (
              <p className="mt-3 text-xs font-medium text-muted-foreground">
                Reference: <span className="font-mono">{digest}</span>
              </p>
            ) : null}
          </div>
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Link
            href="/projects"
            className="inline-flex min-h-11 items-center justify-center rounded-neu bg-surface px-4 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
          >
            Back to projects
          </Link>
          <Button type="button" onClick={() => retry()}>
            <RotateCcw className="mr-2 size-4" aria-hidden="true" />
            Retry loading
          </Button>
        </div>
      </section>
    </main>
  );
}
