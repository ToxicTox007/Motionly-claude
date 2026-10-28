"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { messageFor } from "@/lib/error-messages";
import { createResetLink, type ResetLinkState } from "./actions";
import { CopyLink } from "./CopyLink";

const IDLE: ResetLinkState = { status: "idle" };

/**
 * "Create reset link" for one account. The one-time link comes back as action
 * state and lives only in this component: it is never put in the URL, so it
 * cannot end up in browser history, server logs or a Referer header, and a
 * refresh or Back does not show it again. Closing the panel forgets it.
 */
export function ResetLinkPanel({
  email,
  requestId,
  primary = false,
}: {
  email: string;
  requestId?: string;
  primary?: boolean;
}) {
  const router = useRouter();
  const [state, formAction, pending] = useActionState(createResetLink, IDLE);
  // The latest result, shown until the admin closes it.
  const [shown, setShown] = useState<ResetLinkState | null>(null);
  const [seen, setSeen] = useState(state);
  if (seen !== state) {
    setSeen(state);
    setShown(state.status === "idle" ? null : state);
  }
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (shown?.status === "ok") panelRef.current?.focus();
  }, [shown]);

  const close = () => {
    setShown(null);
    // The request list may have changed (a request is resolved by its link).
    router.refresh();
  };

  return (
    <>
      <form action={formAction}>
        <input type="hidden" name="email" value={email} />
        {requestId ? (
          <input type="hidden" name="requestId" value={requestId} />
        ) : null}
        <button
          type="submit"
          disabled={pending}
          aria-busy={pending}
          className={
            primary
              ? "min-h-10 rounded-neu bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-60"
              : "min-h-10 rounded-neu bg-surface px-4 text-xs font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-60"
          }
        >
          {pending ? "Creating…" : "Create reset link"}
        </button>
      </form>
      {shown?.status === "error" ? (
        <p
          role="alert"
          className="basis-full text-xs font-semibold text-danger"
        >
          {messageFor("admin", shown.error)}
        </p>
      ) : null}
      {shown?.status === "ok" ? (
        <div
          ref={panelRef}
          tabIndex={-1}
          role="group"
          aria-label={`One-time reset link for ${email}`}
          className="basis-full rounded-neu-lg bg-surface p-4 shadow-neu-inset-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          <p className="text-sm font-semibold text-foreground">
            One-time reset link for {email} (valid 1 hour)
          </p>
          <p className="mb-3 mt-1 text-xs font-medium text-muted-foreground">
            Send this to the user over a trusted channel. It works once and
            expires in an hour; creating a new one invalidates it. It is not
            shown again after you close this panel.
          </p>
          <CopyLink link={shown.link} />
          <button
            type="button"
            onClick={close}
            className="mt-3 min-h-10 rounded-neu bg-surface px-4 text-xs font-semibold text-foreground shadow-neu-raised-sm transition-all duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
          >
            Done
          </button>
        </div>
      ) : null}
    </>
  );
}
