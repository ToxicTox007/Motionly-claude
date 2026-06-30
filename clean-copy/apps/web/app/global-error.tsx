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
          background: "hsl(240 12% 7%)",
          color: "hsl(220 20% 96%)",
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
            border: "4px solid white",
            background: "black",
            padding: "2.5rem 2rem",
            boxShadow: "8px 8px 0px #fff",
            borderRadius: "8px",
          }}
        >
          {/* Error icon */}
          <div
            style={{
              width: "72px",
              height: "72px",
              borderRadius: "4px",
              background: "hsl(346 84% 60% / 0.15)",
              border: "2px solid hsl(346 84% 60%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 1.5rem",
              boxShadow: "4px 4px 0px #fff",
            }}
          >
            <AlertTriangle
              style={{
                width: "32px",
                height: "32px",
                color: "hsl(346 84% 65%)",
                strokeWidth: 2,
              }}
            />
          </div>

          {/* Status code */}
          <p
            style={{
              fontSize: "10px",
              fontWeight: 900,
              letterSpacing: "0.15em",
              textTransform: "uppercase",
              color: "black",
              background: "white",
              padding: "0.25rem 0.5rem",
              borderRadius: "2px",
              display: "inline-block",
              marginBottom: "1rem",
              border: "2px solid white",
            }}
          >
            System error
          </p>

          <h1
            style={{
              fontSize: "1.75rem",
              fontWeight: 900,
              lineHeight: 1.1,
              letterSpacing: "-0.03em",
              textTransform: "uppercase",
              margin: "0 0 1rem",
              color: "white",
            }}
          >
            Something went off track.
          </h1>

          <p
            style={{
              fontSize: "0.875rem",
              lineHeight: 1.6,
              color: "hsl(220 12% 70%)",
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
                background: "hsl(252 85% 65%)",
                color: "white",
                fontSize: "0.875rem",
                fontWeight: 900,
                textDecoration: "none",
                border: "2px solid white",
                boxShadow: "3px 3px 0px #fff",
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
                  background: "white",
                  color: "black",
                  fontSize: "0.875rem",
                  fontWeight: 900,
                  border: "2px solid black",
                  cursor: "pointer",
                  boxShadow: "3px 3px 0px #fff",
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
                color: "hsl(220 12% 50%)",
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
