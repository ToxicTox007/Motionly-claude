"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";
import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset?: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en" data-theme="dark">
      <body
        style={{
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "hsl(190 66% 6%)",
          color: "hsl(172 32% 92%)",
          fontFamily: "monospace",
          WebkitFontSmoothing: "antialiased",
          padding: "1rem",
          margin: 0,
        }}
      >
        <main
          style={{
            width: "100%",
            maxWidth: "440px",
            textAlign: "center",
            border: "4px solid hsl(172 32% 92%)",
            background: "hsl(184 45% 10%)",
            padding: "2.5rem 2rem",
            boxShadow: "8px 8px 0px hsl(172 32% 92%)",
            borderRadius: "4px",
          }}
        >
          {/* Error icon */}
          <div
            style={{
              width: "72px",
              height: "72px",
              borderRadius: "4px",
              background: "hsl(352 80% 58%)",
              border: "2px solid hsl(172 32% 92%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 1.5rem",
              boxShadow: "4px 4px 0px hsl(172 32% 92%)",
            }}
          >
            <AlertTriangle
              style={{
                width: "32px",
                height: "32px",
                color: "white",
                strokeWidth: 2,
              }}
            />
          </div>

          {/* Status code */}
          <p
            style={{
              fontSize: "10px",
              fontWeight: 900,
              letterSpacing: 0,
              textTransform: "uppercase",
              color: "hsl(192 78% 4%)",
              background: "hsl(174 98% 46%)",
              padding: "0.25rem 0.5rem",
              borderRadius: "2px",
              display: "inline-block",
              marginBottom: "1rem",
              border: "2px solid hsl(172 32% 92%)",
            }}
          >
            System error
          </p>

          <h1
            style={{
              fontSize: "1.75rem",
              fontWeight: 900,
              lineHeight: 1.1,
              letterSpacing: 0,
              textTransform: "uppercase",
              margin: "0 0 1rem",
              color: "hsl(172 32% 92%)",
            }}
          >
            Something went off track.
          </h1>

          <p
            style={{
              fontSize: "0.875rem",
              lineHeight: 1.6,
              color: "hsl(173 19% 70%)",
              margin: "0 0 2rem",
            }}
          >
            The error has been reported. You can try refreshing the page or
            head back to your dashboard.
          </p>

          {/* Actions */}
          <div
            style={{
              display: "flex",
              gap: "0.75rem",
              justifyContent: "center",
              flexWrap: "wrap",
            }}
          >
            <a
              href="/dashboard"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.5rem",
                height: "44px",
                padding: "0 1.25rem",
                borderRadius: "4px",
                background: "hsl(174 98% 46%)",
                color: "hsl(192 78% 4%)",
                fontSize: "0.875rem",
                fontWeight: 900,
                textDecoration: "none",
                border: "2px solid hsl(172 32% 92%)",
                boxShadow: "3px 3px 0px hsl(172 32% 92%)",
                transition: "transform 100ms ease",
              }}
            >
              <ArrowLeft style={{ width: "16px", height: "16px", strokeWidth: 2 }} />
              Go to dashboard
            </a>
            {reset && (
              <button
                onClick={reset}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  height: "44px",
                  padding: "0 1.125rem",
                  borderRadius: "4px",
                  background: "hsl(176 41% 97%)",
                  color: "hsl(192 78% 4%)",
                  fontSize: "0.875rem",
                  fontWeight: 900,
                  border: "2px solid black",
                  cursor: "pointer",
                  boxShadow: "3px 3px 0px hsl(172 32% 92%)",
                  transition: "transform 100ms ease",
                }}
              >
                <RefreshCw style={{ width: "15px", height: "15px", strokeWidth: 2 }} />
                Try again
              </button>
            )}
          </div>

          {error.digest && (
            <p
              style={{
                marginTop: "2rem",
                fontSize: "10px",
                color: "hsl(173 19% 70%)",
                fontFamily: "monospace",
              }}
            >
              Error ID: {error.digest}
            </p>
          )}
        </main>
      </body>
    </html>
  );
}
