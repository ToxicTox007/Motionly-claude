"use client";

import Link from "next/link";
import { useEffect } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button, ButtonLink } from "@/components/button";

// Fallback for any signed-in page that fails while rendering. It replaces only
// the page, not the root layout, and never shows the error's message (server
// errors carry an opaque digest that matches the server log instead).
// retry() re-fetches the segment's server content before re-rendering.
export default function SignedInError({
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
    console.error("Signed-in page failed to render", digest ?? "");
  }, [digest]);

  return (
    <main
      id="main"
      className="flex min-h-screen items-center justify-center bg-background px-4 py-12"
    >
      <section
        role="alert"
        aria-labelledby="signed-in-error-title"
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
              id="signed-in-error-title"
              className="text-xl font-bold tracking-tight text-foreground"
            >
              This page could not load
            </h1>
            <p className="mt-2 text-sm font-medium text-muted-foreground">
              Motionly could not load this page right now. Nothing you saved has
              been changed. Try again, or go back to your dashboard.
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
            href="/"
            className="inline-flex min-h-11 items-center px-2 text-sm font-semibold text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            Home
          </Link>
          <ButtonLink href="/dashboard" variant="secondary">
            Go to dashboard
          </ButtonLink>
          <Button type="button" onClick={() => retry()}>
            <RotateCcw className="size-4" aria-hidden="true" />
            Try again
          </Button>
        </div>
      </section>
    </main>
  );
}
