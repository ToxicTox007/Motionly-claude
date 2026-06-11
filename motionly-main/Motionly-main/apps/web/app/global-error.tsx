"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body className="grid min-h-screen place-items-center bg-background px-4 text-foreground">
        <main className="pixel-panel-strong pixel-corners w-full max-w-md p-6 text-center">
          <div className="mx-auto mb-5 grid size-12 grid-cols-3 gap-1">
            {Array.from({ length: 9 }).map((_, index) => (
              <span
                key={index}
                className={index === 4 ? "bg-primary" : "bg-muted"}
              />
            ))}
          </div>
          <p className="text-xs font-black uppercase tracking-widest text-muted-foreground">
            System notice
          </p>
          <h1 className="mt-2 text-2xl font-black">Something went off track.</h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            The error was reported. Refresh the page or return to the dashboard.
          </p>
          <a
            href="/dashboard"
            className="pixel-button mt-6 inline-flex h-10 items-center justify-center bg-primary px-4 text-sm font-bold text-primary-foreground"
          >
            Back to dashboard
          </a>
        </main>
      </body>
    </html>
  );
}
