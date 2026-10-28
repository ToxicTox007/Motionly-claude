"use client";

import { useRef, type ComponentPropsWithoutRef } from "react";
import { useFormStatus } from "react-dom";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/button";

type SubmitButtonProps = Omit<
  ComponentPropsWithoutRef<typeof Button>,
  "type"
> & {
  /** Shown (with a spinner) while the form's action runs. */
  pendingLabel?: string;
};

/**
 * A submit button for a server-action form. While the action runs it is
 * disabled and aria-busy, and a second click in the same instant (before
 * React re-renders) is ignored, so the action runs once.
 */
export function SubmitButton({
  children,
  pendingLabel,
  disabled,
  onClick,
  ...props
}: SubmitButtonProps) {
  const { pending } = useFormStatus();
  const submittedRef = useRef(false);

  return (
    <Button
      {...props}
      type="submit"
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      onClick={(event) => {
        if (pending || submittedRef.current) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
        if (event.defaultPrevented) return;
        // Covers a second click that lands before React shows the pending
        // state; released shortly after in case validation stopped the submit.
        submittedRef.current = true;
        window.setTimeout(() => {
          submittedRef.current = false;
        }, 1000);
      }}
    >
      {pending ? (
        <>
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          {pendingLabel ?? children}
        </>
      ) : (
        children
      )}
    </Button>
  );
}
