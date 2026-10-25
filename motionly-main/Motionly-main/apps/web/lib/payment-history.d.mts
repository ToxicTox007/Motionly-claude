export type ReceiptRow = {
  id: string;
  kind: "charge" | "refund";
  kindLabel: string;
  plan: string | null;
  planLabel: string | null;
  amount: string;
  signedAmount: string;
  currency: string;
  status: string;
  reason: string | null;
  createdAt: string | null;
  dateLabel: string;
  demoNotice: string | null;
};

export type RefundAmountValidation =
  | { ok: true; amountPaise: number | null }
  | { ok: false; error: string };

export function formatInr(paise: unknown): string;

export function receiptKindLabel(kind: unknown): string;

export function receiptRow(receipt: unknown): ReceiptRow;

export function planLabel(plan: unknown): string | null;

export function formatDate(value: unknown): string;

export function validateRefundRupees(input: unknown): RefundAmountValidation;

export function pastDueGraceMessage(
  graceEndsAt: string | null | undefined,
  now?: Date,
): string;
