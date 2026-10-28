"use client";

import { useState, useTransition } from "react";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { formatInr, validateRefundRupees } from "@/lib/payment-history.mjs";
import { issueRefund } from "./actions";

const DEMO_NOTICE = "Demo payment — no money is charged";

// Admin refund surface (Task 23). An admin enters the target workspace, an
// optional amount in rupees (empty = everything still refundable on the
// workspace's latest payment), and an optional reason,
// then confirms in a focus-trapped dialog before the refund is sent. The amount
// is validated client-side with the same pure helper the server action uses;
// the API audit-logs the refund. 'Demo payment — no money is charged' is shown
// in demo mode.
export function RefundPanel({ demoMode }: { demoMode: boolean }) {
  const [workspaceId, setWorkspaceId] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pending, startTransition] = useTransition();

  const onReview = (event: React.FormEvent) => {
    event.preventDefault();
    setStatus(null);
    setError(null);
    if (!workspaceId.trim()) {
      setError("workspace id required");
      return;
    }
    const validated = validateRefundRupees(amount);
    if (!validated.ok) {
      setError("refund amount invalid");
      return;
    }
    setConfirmOpen(true);
  };

  const onConfirm = () => {
    startTransition(async () => {
      const result = await issueRefund(workspaceId.trim(), amount, reason);
      setConfirmOpen(false);
      if (!result.ok) {
        setStatus(null);
        setError(result.error.replaceAll("_", " "));
        return;
      }
      setError(null);
      setStatus("Refund issued and recorded in payment history.");
      setAmount("");
      setReason("");
    });
  };

  const validated = validateRefundRupees(amount);
  const amountLabel =
    validated.ok && validated.amountPaise != null
      ? formatInr(validated.amountPaise)
      : "the remaining amount of the latest payment";

  return (
    <section
      aria-labelledby="refund-heading"
      className="mb-8 rounded-neu-lg bg-surface p-5 shadow-neu-raised ring-1 ring-danger/30 sm:p-6"
      data-testid="admin-refund"
    >
      <h2 id="refund-heading" className="mb-2 text-lg font-bold text-danger">
        Issue a refund
      </h2>
      <p className="mb-3 text-sm font-medium text-muted-foreground leading-relaxed">
        Refund a workspace&apos;s latest payment. Leave the amount blank to
        refund everything still refundable on it.{" "}
        {demoMode ? `${DEMO_NOTICE}.` : null}
      </p>

      <div aria-live="polite" role="status">
        {status ? (
          <p className="mb-3 rounded-neu bg-surface px-3 py-2 text-sm font-semibold text-foreground shadow-neu-inset-sm">
            {status}
          </p>
        ) : null}
      </div>
      {error ? (
        <p
          role="alert"
          className="mb-3 rounded-neu bg-surface px-3 py-2 text-sm font-semibold text-danger shadow-neu-inset-sm ring-1 ring-danger/30"
        >
          {error}
        </p>
      ) : null}

      <form onSubmit={onReview} className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[16rem]">
          <label
            htmlFor="refund-workspace"
            className="block text-xs font-semibold text-muted-foreground"
          >
            Workspace ID
          </label>
          <input
            id="refund-workspace"
            type="text"
            value={workspaceId}
            onChange={(event) => setWorkspaceId(event.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
            className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
        </div>
        <div>
          <label
            htmlFor="refund-amount"
            className="block text-xs font-semibold text-muted-foreground"
          >
            Amount (₹)
          </label>
          <input
            id="refund-amount"
            type="text"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            placeholder="Remaining"
            aria-describedby="refund-amount-hint"
            className="mt-1.5 min-h-11 w-32 rounded-neu border border-control bg-surface px-4 text-sm font-medium tabular-nums text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
          <span id="refund-amount-hint" className="sr-only">
            Enter a positive amount in rupees, or leave blank to refund the
            remaining amount of the latest payment.
          </span>
        </div>
        <div className="flex-1 min-w-[12rem]">
          <label
            htmlFor="refund-reason"
            className="block text-xs font-semibold text-muted-foreground"
          >
            Reason (optional)
          </label>
          <input
            id="refund-reason"
            type="text"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
        </div>
        <button
          type="submit"
          disabled={pending}
          className="inline-flex min-h-11 items-center rounded-neu bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
        >
          Issue refund
        </button>
      </form>

      {confirmOpen ? (
        <ConfirmRefund
          amountLabel={amountLabel}
          workspaceId={workspaceId.trim()}
          demoMode={demoMode}
          pending={pending}
          onConfirm={onConfirm}
          onClose={() => setConfirmOpen(false)}
        />
      ) : null}
    </section>
  );
}

function ConfirmRefund({
  amountLabel,
  workspaceId,
  demoMode,
  pending,
  onConfirm,
  onClose,
}: {
  amountLabel: string;
  workspaceId: string;
  demoMode: boolean;
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const ref = useFocusTrap(true, onClose);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 p-4 backdrop-blur-sm"
      role="presentation"
    >
      <div
        ref={ref}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="refund-confirm-title"
        aria-describedby="refund-confirm-body"
        className="w-full max-w-md rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
      >
        <h2 id="refund-confirm-title" className="text-lg font-bold text-danger">
          Issue refund
        </h2>
        <p
          id="refund-confirm-body"
          className="mt-2 text-sm font-medium text-muted-foreground leading-relaxed"
        >
          Refund {amountLabel} to workspace{" "}
          <span className="font-mono text-foreground">{workspaceId}</span>? This
          is recorded in the workspace&apos;s payment history and audit log.
          {demoMode ? ` ${DEMO_NOTICE}.` : null}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={pending}
            className="inline-flex min-h-11 items-center rounded-neu bg-surface px-5 text-sm font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className="inline-flex min-h-11 items-center rounded-neu bg-danger px-5 text-sm font-semibold text-danger-foreground shadow-neu-raised transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
          >
            {pending ? "Working…" : "Confirm refund"}
          </button>
        </div>
      </div>
    </div>
  );
}
