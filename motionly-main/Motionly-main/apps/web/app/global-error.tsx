"use client";

import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import { PageTitle } from "@/components/page-title";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset?: () => void;
}) {
  const base = "hsl(215 24% 90%)";
  const ink = "hsl(222 30% 17%)";
  const muted = "hsl(220 16% 32%)";
  const teal = "hsl(174 98% 44%)";
  const tealText = "hsl(196 60% 8%)";
  const danger = "hsl(352 74% 48%)";
  const raised = "6px 6px 12px hsl(216 24% 72%), -6px -6px 12px #ffffff";
  const raisedLg = "12px 12px 24px hsl(216 24% 72%), -12px -12px 24px #ffffff";
  const inset =
    "inset 4px 4px 8px hsl(216 24% 72%), inset -4px -4px 8px #ffffff";
  const glow =
    "6px 6px 12px hsl(216 24% 72%), -6px -6px 12px #ffffff, 0 0 0 1px hsl(174 98% 44% / 0.35)";

  return (
    <html lang="en" data-theme="light">
      <head>
        <PageTitle title="Something went wrong" />
      </head>
      <body
        style={{
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: base,
          color: ink,
          fontFamily:
            "'Plus Jakarta Sans', ui-sans-serif, system-ui, sans-serif",
          WebkitFontSmoothing: "antialiased",
          padding: "1rem",
          margin: 0,
        }}
      >
        <main
          style={{
            width: "100%",
            maxWidth: "460px",
            textAlign: "center",
            background: base,
            padding: "2.5rem 2rem",
            boxShadow: raisedLg,
            borderRadius: "28px",
          }}
        >
          {/* Error icon */}
          <div
            style={{
              width: "72px",
              height: "72px",
              borderRadius: "18px",
              background: danger,
              color: "#ffffff",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 1.5rem",
              boxShadow: raised,
            }}
          >
            <AlertTriangle
              style={{
                width: "32px",
                height: "32px",
                color: "#ffffff",
                strokeWidth: 1.75,
              }}
            />
          </div>

          {/* Status code */}
          <p
            style={{
              fontSize: "0.75rem",
              fontWeight: 600,
              letterSpacing: 0,
              color: tealText,
              background: teal,
              padding: "0.25rem 0.75rem",
              borderRadius: "9999px",
              display: "inline-block",
              marginBottom: "1rem",
              boxShadow: glow,
            }}
          >
            System error
          </p>

          <h1
            style={{
              fontSize: "1.75rem",
              fontWeight: 700,
              lineHeight: 1.15,
              letterSpacing: "-0.01em",
              margin: "0 0 1rem",
              color: ink,
            }}
          >
            Something went off track.
          </h1>

          <p
            style={{
              fontSize: "0.9375rem",
              lineHeight: 1.6,
              fontWeight: 500,
              color: muted,
              margin: "0 0 2rem",
            }}
          >
            You can try refreshing the page or head back to your dashboard.
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
                minHeight: "44px",
                padding: "0 1.25rem",
                borderRadius: "14px",
                background: teal,
                color: tealText,
                fontSize: "0.875rem",
                fontWeight: 600,
                textDecoration: "none",
                boxShadow: glow,
              }}
            >
              <ArrowLeft
                style={{ width: "16px", height: "16px", strokeWidth: 1.75 }}
              />
              Go to dashboard
            </a>
            {reset && (
              <button
                onClick={reset}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  minHeight: "44px",
                  padding: "0 1.125rem",
                  borderRadius: "14px",
                  background: base,
                  color: ink,
                  fontSize: "0.875rem",
                  fontWeight: 600,
                  border: "none",
                  cursor: "pointer",
                  boxShadow: raised,
                }}
              >
                <RefreshCw
                  style={{ width: "15px", height: "15px", strokeWidth: 1.75 }}
                />
                Try again
              </button>
            )}
          </div>

          {error.digest && (
            <p
              style={{
                marginTop: "2rem",
                padding: "0.5rem 0.75rem",
                borderRadius: "10px",
                boxShadow: inset,
                fontSize: "0.75rem",
                color: muted,
                fontFamily: "ui-monospace, monospace",
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
