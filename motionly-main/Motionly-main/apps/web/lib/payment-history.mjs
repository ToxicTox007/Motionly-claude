// Pure presentation logic for billing payment history & receipts (Task 23),
// shared by the billing page (owner-only payment history) and the admin refund
// surface. Framework-free so it can be unit-tested directly and used by both
// server and client components.
//
// Amounts arrive from the API in INR paise (₹1 = 100 paise); every amount the
// user sees is formatted here so the rupee formatting lives in exactly one
// place.

/** Format an INR paise amount as a rupee string, e.g. 49900 -> "₹499.00". */
export function formatInr(paise) {
  const value = Math.abs(Math.round(Number(paise) || 0)) / 100;
  return `₹${value.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** A receipt's human kind label. The API sends `kind` as "charge" | "refund". */
export function receiptKindLabel(kind) {
  return kind === "refund" ? "Refund" : "Payment";
}

/**
 * Normalize an API PaymentReceipt into the fields the history table and the
 * receipt view render. A refund is a negative movement shown with a leading
 * minus; a charge is positive. `plan` may be null (e.g. a manual refund without
 * a plan tag).
 */
export function receiptRow(receipt) {
  const kind = receipt?.kind === "refund" ? "refund" : "charge";
  const amount = formatInr(receipt?.amount_paise);
  return {
    id: String(receipt?.id ?? ""),
    kind,
    kindLabel: receiptKindLabel(kind),
    plan: receipt?.plan ?? null,
    planLabel: planLabel(receipt?.plan),
    amount,
    // Refunds reduce the balance; show the sign so a reversal reads correctly.
    signedAmount: kind === "refund" ? `−${amount}` : amount,
    currency: (receipt?.currency ?? "INR").toUpperCase(),
    status: kind === "refund" ? "Refunded" : "Paid",
    reason: receipt?.reason ?? null,
    createdAt: receipt?.created_at ?? null,
    dateLabel: formatDate(receipt?.created_at),
    demoNotice: receipt?.demo_notice ?? null,
  };
}

/** Map the API's plan key to a display name; null when there is no plan tag. */
export function planLabel(plan) {
  if (!plan) return null;
  const names = {
    free: "Free",
    creator: "Creator",
    pro: "Pro",
    agency: "Agency",
  };
  return names[String(plan)] ?? String(plan);
}

/** A stable, locale-aware date label; "—" when the timestamp is missing. */
export function formatDate(value) {
  if (!value) return "—";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "—";
  return new Date(parsed).toLocaleDateString("en-IN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Validate a refund amount entered by an admin, in whole rupees. Returns the
 * amount in paise when valid, or an error code the surface turns into a
 * message. An empty amount means "refund the full plan price" (the API default)
 * and is allowed (returns { ok: true, amountPaise: null }).
 */
export function validateRefundRupees(input) {
  const raw = String(input ?? "").trim();
  if (raw === "") return { ok: true, amountPaise: null };
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) {
    return { ok: false, error: "refund_amount_invalid" };
  }
  const rupees = Number(raw);
  if (!Number.isFinite(rupees) || rupees <= 0) {
    return { ok: false, error: "refund_amount_invalid" };
  }
  return { ok: true, amountPaise: Math.round(rupees * 100) };
}
