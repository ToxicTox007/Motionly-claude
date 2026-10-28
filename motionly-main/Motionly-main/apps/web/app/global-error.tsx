"use client";

// Replaces the root layout when it fails, so it loads the global styles and
// the app font itself and uses the same design tokens as every other page.
// It links with plain <a> elements: the router may be what broke.
import "./globals.css";
import { AlertTriangle, ArrowLeft, Home, RefreshCw } from "lucide-react";
import { buttonClassName } from "@/components/button";
import { PageTitle } from "@/components/page-title";
import { jakarta } from "./fonts";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset?: () => void;
}) {
  return (
    <html lang="en" data-theme="light" className={jakarta.variable}>
      <head>
        <PageTitle title="Something went wrong" />
      </head>
      <body className="m-0 grid min-h-screen place-items-center bg-background p-4 font-sans text-foreground antialiased">
        <main
          id="main"
          className="w-full max-w-md rounded-neu-xl bg-surface px-8 py-10 text-center shadow-neu-raised-lg"
        >
          <div className="mx-auto mb-6 flex size-[72px] items-center justify-center rounded-neu-md bg-danger text-danger-foreground shadow-neu-raised">
            <AlertTriangle
              className="size-8"
              strokeWidth={1.75}
              aria-hidden="true"
            />
          </div>

          <p className="mb-4 inline-block rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground shadow-neu-glow">
            System error
          </p>

          <h1 className="mb-4 text-3xl font-bold leading-tight tracking-tight text-foreground">
            Something went off track.
          </h1>

          <p className="mb-8 text-sm font-medium leading-relaxed text-muted-foreground">
            You can try refreshing the page or head back to your dashboard.
          </p>

          <div className="flex flex-wrap justify-center gap-3">
            <a href="/dashboard" className={buttonClassName()}>
              <ArrowLeft className="size-4" aria-hidden="true" />
              Go to dashboard
            </a>
            {reset ? (
              <button
                type="button"
                onClick={reset}
                className={buttonClassName({ variant: "secondary" })}
              >
                <RefreshCw className="size-4" aria-hidden="true" />
                Try again
              </button>
            ) : null}
            {/* A full page load on purpose: the client router may be broken. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a href="/" className={buttonClassName({ variant: "ghost" })}>
              <Home className="size-4" aria-hidden="true" />
              Home
            </a>
          </div>

          {error.digest ? (
            <p className="mt-8 rounded-neu-sm px-3 py-2 font-mono text-xs text-muted-foreground shadow-neu-inset">
              Error ID: {error.digest}
            </p>
          ) : null}
        </main>
      </body>
    </html>
  );
}
