// Pure logic for billing payment history & receipts (Task 23). Amounts arrive
// in INR paise; refunds are negative movements; the admin refund amount is
// entered in whole rupees and validated before it is sent. All of this must
// behave identically on the server (history page) and in the browser (the
// admin refund dialog), so it lives in a framework-free module tested here.
import assert from "node:assert/strict";
import test from "node:test";

import {
  formatInr,
  formatDate,
  planLabel,
  receiptKindLabel,
  receiptRow,
  validateRefundRupees,
} from "../lib/payment-history.mjs";

test("INR paise are formatted as rupees with two decimals", () => {
  assert.equal(formatInr(49900), "₹499.00");
  assert.equal(formatInr(0), "₹0.00");
  // A negative paise value (a refund) is shown as a magnitude; the sign is
  // applied separately by receiptRow.
  assert.equal(formatInr(-49900), "₹499.00");
  assert.equal(formatInr(199900), "₹1,999.00");
});

test("a charge receipt reads as a positive Paid payment", () => {
  const row = receiptRow({
    id: "r1",
    kind: "charge",
    plan: "agency",
    amount_paise: 49900,
    currency: "inr",
    created_at: "2026-10-20T00:00:00Z",
    demo_notice: "Demo payment — no money is charged",
  });
  assert.equal(row.kind, "charge");
  assert.equal(row.kindLabel, "Payment");
  assert.equal(row.planLabel, "Agency");
  assert.equal(row.amount, "₹499.00");
  assert.equal(row.signedAmount, "₹499.00");
  assert.equal(row.currency, "INR");
  assert.equal(row.status, "Paid");
  assert.equal(row.demoNotice, "Demo payment — no money is charged");
});

test("a refund receipt reads as a negative Refunded movement", () => {
  const row = receiptRow({
    id: "r2",
    kind: "refund",
    plan: null,
    amount_paise: 49900,
    currency: "INR",
    reason: "Customer request",
    created_at: "2026-10-20T00:00:00Z",
  });
  assert.equal(row.kind, "refund");
  assert.equal(row.kindLabel, "Refund");
  assert.equal(row.planLabel, null);
  // A reversal is shown with a leading minus so it reads as money returned.
  assert.equal(row.signedAmount, "−₹499.00");
  assert.equal(row.status, "Refunded");
  assert.equal(row.reason, "Customer request");
});

test("receipt kind and plan labels have readable fallbacks", () => {
  assert.equal(receiptKindLabel("charge"), "Payment");
  assert.equal(receiptKindLabel("refund"), "Refund");
  assert.equal(planLabel(null), null);
  assert.equal(planLabel("creator"), "Creator");
  assert.equal(planLabel("mystery"), "mystery");
});

test("a missing or unparseable date renders as an em dash", () => {
  assert.equal(formatDate(null), "—");
  assert.equal(formatDate("not-a-date"), "—");
  assert.notEqual(formatDate("2026-10-20T00:00:00Z"), "—");
});

test("an empty refund amount means refund the remaining captured amount", () => {
  const result = validateRefundRupees("");
  assert.equal(result.ok, true);
  assert.equal(result.amountPaise, null);
});

test("a valid refund amount in rupees converts to paise", () => {
  assert.deepEqual(validateRefundRupees("499"), {
    ok: true,
    amountPaise: 49900,
  });
  assert.deepEqual(validateRefundRupees("49.50"), {
    ok: true,
    amountPaise: 4950,
  });
});

test("a non-positive or malformed refund amount is rejected", () => {
  assert.deepEqual(validateRefundRupees("0"), {
    ok: false,
    error: "refund_amount_invalid",
  });
  assert.deepEqual(validateRefundRupees("-5"), {
    ok: false,
    error: "refund_amount_invalid",
  });
  assert.deepEqual(validateRefundRupees("abc"), {
    ok: false,
    error: "refund_amount_invalid",
  });
  assert.deepEqual(validateRefundRupees("1.234"), {
    ok: false,
    error: "refund_amount_invalid",
  });
});

test("the past-due banner states when paid features stop", async () => {
  const { pastDueGraceMessage } = await import("../lib/payment-history.mjs");
  const now = new Date("2026-10-20T00:00:00Z");
  assert.match(
    pastDueGraceMessage("2026-10-27T00:00:00Z", now),
    /^Paid features stay on until /,
  );
  for (const ended of ["2026-10-19T00:00:00Z", null, undefined, "not-a-date"]) {
    assert.match(pastDueGraceMessage(ended, now), /^Paid features are paused/);
  }
});
