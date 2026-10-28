"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { createClient } from "@/lib/supabase/browser";
import { INVITE_TOKEN_STORAGE_KEY, inviteTokenFromHash } from "@/lib/team.mjs";
import { acceptInvite } from "./actions";

type Phase =
  | "loading"
  | "no_token"
  | "signed_out"
  | "ready"
  | "joining"
  | "joined"
  | "error";

// A signed-out visitor is sent to log in / sign up with `next=/invite`, and the
// token is kept in sessionStorage so it survives the detour (the fragment is
// dropped on navigation). Both auth links therefore carry the same safe `next`.
const INVITE_NEXT = "/invite";

// Resolve the invite token during render (not in an effect): the fragment wins,
// then the stored copy from a login/signup detour. Reading window here is safe
// because this is a client component; the lazy initializer runs only once, in
// the browser.
function resolveToken(): string | null {
  if (typeof window === "undefined") return null;
  const fromHash = inviteTokenFromHash(window.location.hash);
  if (fromHash) {
    window.sessionStorage.setItem(INVITE_TOKEN_STORAGE_KEY, fromHash);
    return fromHash;
  }
  return window.sessionStorage.getItem(INVITE_TOKEN_STORAGE_KEY);
}

export function InviteClient() {
  const router = useRouter();
  const [token] = useState<string | null>(resolveToken);
  const [phase, setPhase] = useState<Phase>(token ? "loading" : "no_token");
  const [workspaceName, setWorkspaceName] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  // Only async work here: check the viewer's sign-in state (and tidy the token
  // out of the address bar). setState happens in the async callback, not
  // synchronously in the effect body, so there is no cascading render.
  useEffect(() => {
    if (!token) return;
    let active = true;
    // Drop the token from the address bar so it isn't shoulder-surfed or kept
    // in history; the stored copy in sessionStorage is what we use from here.
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    (async () => {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!active) return;
      setPhase(session?.access_token ? "ready" : "signed_out");
    })();

    return () => {
      active = false;
    };
  }, [token]);

  const join = () => {
    if (!token) return;
    setErrorCode(null);
    setPhase("joining");
    startTransition(async () => {
      const result = await acceptInvite(token);
      if (!result.ok) {
        if (!result.signedIn) {
          setPhase("signed_out");
          return;
        }
        setErrorCode(result.error);
        setPhase("error");
        return;
      }
      // Joined: the token is spent, so clear it and land on the workspace.
      window.sessionStorage.removeItem(INVITE_TOKEN_STORAGE_KEY);
      setWorkspaceName(result.workspaceName);
      setPhase("joined");
      router.replace("/dashboard");
    });
  };

  return (
    <div className="w-full max-w-md rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg">
      <div className="mb-5 flex items-center gap-3">
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
      </div>

      <h1 className="text-2xl font-bold tracking-tight text-foreground">
        Join a workspace
      </h1>

      <div aria-live="polite" role="status" className="mt-4">
        {phase === "loading" ? (
          <p className="text-sm text-muted-foreground">Checking your invite…</p>
        ) : null}

        {phase === "no_token" ? (
          <p className="text-sm font-semibold text-danger">
            This invite link is missing its token. Ask whoever invited you to
            send the full link again.
          </p>
        ) : null}

        {phase === "signed_out" ? (
          <p className="text-sm text-muted-foreground">
            You&apos;ve been invited to a Motionly workspace. Sign in or create
            an account to join — we&apos;ll bring you right back here.
          </p>
        ) : null}

        {phase === "ready" || phase === "joining" ? (
          <p className="text-sm text-muted-foreground">
            You&apos;ve been invited to a Motionly workspace. Accept the invite
            to join.
          </p>
        ) : null}

        {phase === "joined" ? (
          <p className="text-sm font-semibold text-foreground">
            You&apos;ve joined {workspaceName ?? "the workspace"}. Taking you to
            the dashboard…
          </p>
        ) : null}
      </div>

      {phase === "error" ? (
        <p role="alert" className="mt-3 text-sm font-semibold text-danger">
          {errorCode === "invitation_not_found"
            ? "This invite link is no longer valid. It may have expired or been revoked."
            : errorCode === "invitation_already_used"
              ? "This invite has already been used."
              : errorCode === "seat_limit_reached"
                ? "This workspace has no seats left. Ask the workspace owner to upgrade their plan."
                : "We couldn't accept this invite. Please try again."}
        </p>
      ) : null}

      <div className="mt-6 flex flex-wrap gap-3">
        {phase === "signed_out" ? (
          <>
            <Link
              href={`/login?next=${encodeURIComponent(INVITE_NEXT)}`}
              className="inline-flex min-h-11 items-center rounded-neu bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
            >
              Log in
            </Link>
            <Link
              href={`/signup?next=${encodeURIComponent(INVITE_NEXT)}`}
              className="inline-flex min-h-11 items-center rounded-neu bg-surface px-4 text-sm font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
            >
              Sign up
            </Link>
          </>
        ) : null}

        {phase === "ready" || phase === "joining" || phase === "error" ? (
          <button
            type="button"
            onClick={join}
            disabled={pending || phase === "joining"}
            className="inline-flex min-h-11 items-center rounded-neu bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50 disabled:shadow-neu-raised-sm"
          >
            {phase === "joining" ? "Joining…" : "Join workspace"}
          </button>
        ) : null}

        {phase === "no_token" ? (
          <Link
            href="/dashboard"
            className="inline-flex min-h-11 items-center rounded-neu bg-surface px-4 text-sm font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
          >
            Go to dashboard
          </Link>
        ) : null}
      </div>
    </div>
  );
}
