"use client";

import Link from "next/link";
import { useEffect } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/button";

export default function ProjectError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Project workspace failed to load", error);
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <section
        role="alert"
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
            <h1 className="text-xl font-bold tracking-tight text-foreground">
              Project data is unavailable
            </h1>
            <p className="mt-2 text-sm font-medium text-muted-foreground">
              Motionly could not load the authoritative project state. Your
              saved timeline has not been replaced or cleared.
            </p>
          </div>
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Link
            href="/projects"
            className="inline-flex min-h-11 items-center justify-center rounded-neu bg-surface px-4 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
          >
            Back to projects
          </Link>
          <Button type="button" onClick={reset}>
            <RotateCcw className="mr-2 size-4" aria-hidden="true" />
            Retry loading
          </Button>
        </div>
      </section>
    </main>
  );
}
