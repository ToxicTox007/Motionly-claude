"use client";

import { useId, useRef, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/button";
import { Modal } from "@/components/modal";

/**
 * Asks before an irreversible action. Built on the shared Modal (focus trap,
 * Escape, focus return). Confirm runs once — repeated clicks while it is
 * pending do nothing — and the dialog cannot be dismissed mid-action.
 * `typedConfirmation` asks the viewer to type a word first (for bulk or
 * high-blast-radius actions).
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  pendingLabel = "Working…",
  tone = "danger",
  typedConfirmation,
  pending = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: ReactNode;
  body: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  tone?: "danger" | "primary";
  typedConfirmation?: string;
  /** The action is running (e.g. a server action's pending state). */
  pending?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [running, setRunning] = useState(false);
  const runningRef = useRef(false);
  const inputId = useId();
  const busy = pending || running;
  const typedOk = !typedConfirmation || typed.trim() === typedConfirmation;

  const confirm = async () => {
    if (runningRef.current || busy || !typedOk) return;
    runningRef.current = true;
    setRunning(true);
    try {
      await onConfirm();
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  };

  const cancel = () => {
    setTyped("");
    onCancel();
  };

  return (
    <Modal
      open={open}
      title={title}
      description={typeof body === "string" ? body : undefined}
      onClose={cancel}
      closeDisabled={busy}
      footer={
        <>
          <Button
            type="button"
            variant="secondary"
            onClick={cancel}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant={tone === "danger" ? "danger" : "primary"}
            onClick={() => void confirm()}
            disabled={busy || !typedOk}
            aria-busy={busy || undefined}
          >
            {busy ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                {pendingLabel}
              </>
            ) : (
              confirmLabel
            )}
          </Button>
        </>
      }
    >
      {typeof body === "string" ? null : body}
      {typedConfirmation ? (
        <div className={typeof body === "string" ? "" : "mt-4"}>
          <label
            htmlFor={inputId}
            className="block text-xs font-semibold text-foreground"
          >
            Type <strong>{typedConfirmation}</strong> to confirm
          </label>
          <input
            id={inputId}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoComplete="off"
            className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
        </div>
      ) : null}
    </Modal>
  );
}
