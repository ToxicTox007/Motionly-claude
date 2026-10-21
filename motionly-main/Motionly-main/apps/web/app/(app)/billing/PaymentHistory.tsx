"use client";

import { useState } from "react";
import { Receipt } from "lucide-react";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { receiptRow } from "@/lib/payment-history.mjs";
import type { PaymentReceiptSummary } from "@/lib/billing";

const DEMO_NOTICE = "Demo payment — no money is charged";

// Owner-only in-app payment history (Task 23). An accessible table of date,
// plan, amount (INR), and status, with a per-row "View receipt" button that
// opens a focus-trapped receipt dialog. Amounts and dates are formatted by the
// pure payment-history helpers so the rupee formatting lives in one place.
export function PaymentHistory({
  receipts,
  demoMode,
}: {
  receipts: PaymentReceiptSummary[];
  demoMode: boolean;
}) {
  const [active, setActive] = useState<PaymentReceiptSummary | null>(null);
  const rows = receipts.map((receipt) => receiptRow(receipt));

  return (
    <section
      aria-labelledby="history-heading"
      className="rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6"
      data-testid="payment-history"
    >
      <div className="flex items-center gap-2">
        <Receipt className="size-5 text-primary" aria-hidden="true" />
        <h2 id="history-heading" className="text-lg font-bold text-foreground">
          Payment history
        </h2>
      </div>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm font-medium text-muted-foreground">
          No payments yet. Receipts appear here after you upgrade.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Payment history: date, plan, amount in INR, and status, with a
              receipt view for each payment.
            </caption>
            <thead>
              <tr className="text-xs font-semibold text-muted-foreground">
                <th scope="col" className="py-2 pr-4">
                  Date
                </th>
                <th scope="col" className="py-2 pr-4">
                  Plan
                </th>
                <th scope="col" className="py-2 pr-4">
                  Amount
                </th>
                <th scope="col" className="py-2 pr-4">
                  Status
                </th>
                <th scope="col" className="py-2">
                  <span className="sr-only">Receipt</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {rows.map((row, index) => (
                <tr key={row.id}>
                  <td className="py-3 pr-4 tabular-nums text-muted-foreground">
                    {row.dateLabel}
                  </td>
                  <td className="py-3 pr-4 font-semibold text-foreground">
                    {row.planLabel ?? "—"}
                  </td>
                  <td className="py-3 pr-4 font-semibold tabular-nums text-foreground">
                    {row.signedAmount} {row.currency}
                  </td>
                  <td className="py-3 pr-4">
                    <span
                      className={
                        row.kind === "refund"
                          ? "inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold text-warning shadow-neu-inset-sm"
                          : "inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold text-success shadow-neu-inset-sm"
                      }
                    >
                      {row.status}
                    </span>
                  </td>
                  <td className="py-3">
                    <button
                      type="button"
                      onClick={() => setActive(receipts[index])}
                      aria-label={`View receipt for ${row.kindLabel.toLowerCase()} of ${row.amount} on ${row.dateLabel}`}
                      className="inline-flex min-h-9 items-center gap-2 rounded-neu bg-surface px-3 text-xs font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed focus:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                    >
                      View receipt
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {active ? (
        <ReceiptDialog
          receipt={active}
          demoMode={demoMode}
          onClose={() => setActive(null)}
        />
      ) : null}
    </section>
  );
}

function ReceiptDialog({
  receipt,
  demoMode,
  onClose,
}: {
  receipt: PaymentReceiptSummary;
  demoMode: boolean;
  onClose: () => void;
}) {
  const ref = useFocusTrap(true, onClose);
  const row = receiptRow(receipt);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 p-4 backdrop-blur-sm"
      role="presentation"
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="receipt-title"
        className="w-full max-w-md rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
      >
        <h2 id="receipt-title" className="text-lg font-bold text-foreground">
          Receipt
        </h2>
        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="font-semibold text-muted-foreground">Type</dt>
          <dd className="text-foreground">{row.kindLabel}</dd>
          <dt className="font-semibold text-muted-foreground">Date</dt>
          <dd className="tabular-nums text-foreground">{row.dateLabel}</dd>
          <dt className="font-semibold text-muted-foreground">Plan</dt>
          <dd className="text-foreground">{row.planLabel ?? "—"}</dd>
          <dt className="font-semibold text-muted-foreground">Amount</dt>
          <dd className="font-semibold tabular-nums text-foreground">
            {row.signedAmount} {row.currency}
          </dd>
          <dt className="font-semibold text-muted-foreground">Status</dt>
          <dd className="text-foreground">{row.status}</dd>
          {row.reason ? (
            <>
              <dt className="font-semibold text-muted-foreground">Reason</dt>
              <dd className="text-foreground">{row.reason}</dd>
            </>
          ) : null}
          {receipt.gateway_event_id ? (
            <>
              <dt className="font-semibold text-muted-foreground">Reference</dt>
              <dd className="break-all font-mono text-xs text-foreground">
                {receipt.gateway_event_id}
              </dd>
            </>
          ) : null}
        </dl>
        {demoMode ? (
          <p className="mt-4 text-xs font-semibold text-muted-foreground">
            {DEMO_NOTICE}
          </p>
        ) : null}
        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex min-h-10 items-center rounded-neu bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-shadow duration-200 hover:brightness-105 active:shadow-neu-pressed focus:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
