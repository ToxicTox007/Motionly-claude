"use client";

import { useRef, useState, type ReactNode } from "react";
import { useFormStatus } from "react-dom";
import { Button } from "@/components/button";
import { ConfirmDialog } from "@/components/confirm-dialog";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent";

/**
 * A server-action form whose submit is confirmed first. Server pages pass the
 * action and its hidden fields; the trigger opens a ConfirmDialog and only
 * Confirm submits the form (once — the action's pending state disables it).
 */
export function ConfirmFormButton({
  action,
  children,
  triggerLabel,
  triggerAriaLabel,
  triggerVariant = "secondary",
  triggerClassName,
  title,
  body,
  confirmLabel,
  pendingLabel,
  typedConfirmation,
  formClassName,
}: {
  action: (formData: FormData) => void | Promise<void>;
  /** Hidden inputs (and any visible fields) submitted with the form. */
  children?: ReactNode;
  triggerLabel: ReactNode;
  triggerAriaLabel?: string;
  triggerVariant?: ButtonVariant;
  triggerClassName?: string;
  title: ReactNode;
  body: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  typedConfirmation?: string;
  formClassName?: string;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  return (
    <form ref={formRef} action={action} className={formClassName}>
      {children}
      <ConfirmBody
        formRef={formRef}
        triggerLabel={triggerLabel}
        triggerAriaLabel={triggerAriaLabel}
        triggerVariant={triggerVariant}
        triggerClassName={triggerClassName}
        title={title}
        body={body}
        confirmLabel={confirmLabel}
        pendingLabel={pendingLabel}
        typedConfirmation={typedConfirmation}
      />
    </form>
  );
}

// Inside the form so useFormStatus sees its pending state.
function ConfirmBody({
  formRef,
  triggerLabel,
  triggerAriaLabel,
  triggerVariant,
  triggerClassName,
  title,
  body,
  confirmLabel,
  pendingLabel,
  typedConfirmation,
}: {
  formRef: React.RefObject<HTMLFormElement | null>;
  triggerLabel: ReactNode;
  triggerAriaLabel?: string;
  triggerVariant: ButtonVariant;
  triggerClassName?: string;
  title: ReactNode;
  body: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  typedConfirmation?: string;
}) {
  const { pending } = useFormStatus();
  const [open, setOpen] = useState(false);
  // Close once the confirmed action has finished (pending true → false). The
  // dialog stays up while it runs, so the result lands on an open dialog.
  const [wasPending, setWasPending] = useState(pending);
  if (wasPending !== pending) {
    setWasPending(pending);
    if (!pending) setOpen(false);
  }
  return (
    <>
      <Button
        type="button"
        variant={triggerVariant}
        className={triggerClassName}
        aria-label={triggerAriaLabel}
        aria-haspopup="dialog"
        disabled={pending}
        onClick={() => setOpen(true)}
      >
        {triggerLabel}
      </Button>
      <ConfirmDialog
        open={open || pending}
        title={title}
        body={body}
        confirmLabel={confirmLabel}
        pendingLabel={pendingLabel}
        typedConfirmation={typedConfirmation}
        pending={pending}
        onCancel={() => setOpen(false)}
        onConfirm={() => {
          formRef.current?.requestSubmit();
        }}
      />
    </>
  );
}
