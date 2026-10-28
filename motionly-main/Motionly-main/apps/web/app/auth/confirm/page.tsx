"use client";

// The admin's reset link points here with the GoTrue recovery token in the URL
// *fragment* (#token_hash=...&type=recovery). The fragment never leaves the
// browser, so the token stays out of proxy and server access logs. This page
// reads it client-side, immediately clears it from the address bar
// (history.replaceState — a reload or a copied URL then carries no token), and
// POSTs the token_hash to the server route handler /app-api/auth/confirm. The
// CHECK happens there: the server verifies the token and sets the recovery
// session cookie, then this page sends the visitor to /reset-password. An
// invalid, expired, reused, or missing link shows a clear error — never a 500.

import Link from "next/link";
import Image from "next/image";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert } from "@/components/brutal-ui";
import { Loader2 } from "lucide-react";
import { PageTitle } from "@/components/page-title";
import { RESET_LINK_ERROR_MESSAGES } from "@/lib/password-reset.mjs";

function parseHash(hash: string): {
  tokenHash: string | null;
  type: string | null;
} {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return {
    tokenHash: params.get("token_hash"),
    type: params.get("type"),
  };
}

export default function ConfirmRecoveryPage() {
  const router = useRouter();
  const [errorReason, setErrorReason] = useState<
    keyof typeof RESET_LINK_ERROR_MESSAGES | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      const { tokenHash, type } = parseHash(window.location.hash);
      // Clear the token-bearing fragment from the address bar before doing
      // anything else, so a reload or a copied URL carries no token.
      if (window.location.hash) {
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      }
      if (!tokenHash || type !== "recovery") {
        if (!cancelled) setErrorReason("missing");
        return;
      }
      try {
        // Submit the token in a POST body (never a query string) so the CHECK
        // runs on the server, which sets the recovery session cookie.
        const response = await fetch("/app-api/auth/confirm", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ token_hash: tokenHash }),
          cache: "no-store",
        });
        if (cancelled) return;
        if (!response.ok) {
          const payload = (await response.json().catch(() => null)) as {
            reason?: keyof typeof RESET_LINK_ERROR_MESSAGES;
          } | null;
          setErrorReason(payload?.reason ?? "invalid");
          return;
        }
        // The server set the recovery session cookie; continue to choose a
        // password. The guard on /reset-password finds that session.
        router.replace("/reset-password");
      } catch {
        if (!cancelled) setErrorReason("invalid");
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <main className="min-h-screen flex items-center justify-center px-6 py-16 sm:px-8 bg-background">
      <PageTitle title="Confirm reset" />
      <div className="w-full max-w-[420px] text-center animate-fade-up">
        <Link
          href="/"
          className="group mb-8 inline-flex items-center gap-2.5 justify-center"
        >
          <div className="flex size-9 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
            <Image
              src="/icon.svg"
              alt=""
              width={28}
              height={28}
              className="size-7"
            />
          </div>
          <span className="text-base font-bold text-foreground">Motionly</span>
        </Link>

        {errorReason ? (
          <>
            <h1 className="mt-2 text-2xl font-bold tracking-tight text-foreground">
              Reset link problem
            </h1>
            <Alert tone="danger" className="mt-5 text-left">
              {RESET_LINK_ERROR_MESSAGES[errorReason]}
            </Alert>
            <p className="mt-5 text-sm text-muted-foreground">
              <Link
                href="/forgot-password"
                className="font-semibold text-primary hover:underline"
              >
                Request another reset
              </Link>
            </p>
          </>
        ) : (
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="size-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">
              Checking your reset link...
            </p>
          </div>
        )}
      </div>
    </main>
  );
}
