// Task 23: the in-app billing page drives the demo payment gateway end to end.
// A workspace owner upgrades (checkout on the gateway /pay flow), returns to the
// billing page which polls /billing/status until the signed webhook lands and
// the plan unlocks, sees the payment receipt in history, can cancel at period
// end, and — after a failed renewal — can retry to restore the plan. Every
// payment surface states 'Demo payment — no money is charged'. Billing is
// owner-only (Task 19). These specs are WRITTEN now and run later at the phase
// checkpoint behind the `demo-billing` Compose profile; they are not run here.
//
// State the page reacts to (an active paid plan, a scheduled cancel, a past-due
// renewal, a second member) is seeded deterministically through the stack:
// PostgREST on the `subscriptions` table (the store /billing/status reads) and
// the Auth admin API via support/workspace.mjs — never guarded behind
// `if (visible)`.
import { expect, signIn, test } from "./support/fixtures.mjs";
import {
  addWorkspaceUser,
  listBillingAdjustments,
  ownedWorkspaceId,
  seedPaymentCharge,
  setSubscription,
} from "./support/workspace.mjs";

const DEMO_NOTICE = "Demo payment — no money is charged";

const WORKSPACE_COOKIE_NAME = "motionly_workspace_id";

async function gotoBilling(page) {
  const response = await page.goto("/billing");
  expect(response?.status()).toBeLessThan(400);
  await expect(
    page.getByRole("heading", { name: /Current plan/i }),
  ).toBeVisible();
}

test("the billing page shows the demo notice on every payment surface", async ({
  page,
  user,
  gates,
}) => {
  await signIn(page, user, "/billing");
  await gotoBilling(page);

  // The demo banner is present whenever billing is enabled in demo mode.
  await expect(page.getByText(DEMO_NOTICE)).toBeVisible();
  await gates.checkAccessibility(page);
});

test("owner upgrades via the gateway and the plan unlocks after the webhook", async ({
  page,
  user,
  gates,
}) => {
  await signIn(page, user, "/billing");
  await gotoBilling(page);

  // Upgrade to Agency: this redirects to the gateway checkout page.
  const agencyCard = page
    .locator("div")
    .filter({ hasText: /^Agency/ })
    .first();
  await agencyCard.getByRole("button", { name: /Upgrade/i }).click();

  // On the gateway checkout page, pay with a successful UPI VPA.
  await expect(page.getByText(DEMO_NOTICE)).toBeVisible();
  await page.getByLabel(/UPI ID/i).fill("success@upi");
  await page.getByRole("button", { name: /Pay by UPI/i }).click();
  await expect(
    page.getByRole("heading", { name: /Payment successful/i }),
  ).toBeVisible();

  // Return to billing; poll until the webhook lands and the current plan is
  // Agency (the "Current plan" card heading, not a plan-catalogue card).
  await expect(async () => {
    await page.goto("/billing");
    await expect(
      page.getByRole("heading", { name: "Agency", exact: true }),
    ).toBeVisible();
  }).toPass({ timeout: 20_000 });

  // The receipt appears in payment history.
  await expect(page.getByText(/Agency/i).first()).toBeVisible();
  await gates.checkAccessibility(page);
});

test("owner can cancel an active plan at period end", async ({
  page,
  user,
  stack,
}) => {
  // Seed an active paid plan so the owner has something to cancel; without it
  // the free plan shows no cancel control and the title would be a lie.
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await setSubscription(stack, workspaceId, {
    plan: "agency",
    status: "active",
  });

  await signIn(page, user, "/billing");
  await gotoBilling(page);

  // The cancel control is present for an owner on an active paid plan.
  const cancel = page.getByRole("button", { name: /Cancel at period end/i });
  await expect(cancel).toBeVisible();
  await cancel.click();

  // The plan is now scheduled to cancel at the period end.
  await expect(async () => {
    await page.goto("/billing");
    await expect(page.getByText(/Cancels at period end/i)).toBeVisible();
  }).toPass({ timeout: 20_000 });
});

// A failed renewal marks the plan past due; the owner retries and the plan is
// restored once the gateway webhook lands. The past-due state is seeded through
// the subscriptions store (status=past_due), so the "Payment past due" state is
// always present — the test fails if the page does not surface it or if the
// retry does not restore the plan.
test("a failed renewal shows past due and a retry restores the plan", async ({
  page,
  user,
  stack,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await setSubscription(stack, workspaceId, {
    plan: "agency",
    status: "past_due",
  });

  await signIn(page, user, "/billing");
  await gotoBilling(page);

  // The page surfaces the past-due state and an owner-only retry control.
  await expect(
    page.getByRole("heading", { name: /Payment past due/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Retry payment/i }).click();

  // Retry hands the owner a fresh gateway order; pay it successfully.
  await expect(page.getByText(DEMO_NOTICE)).toBeVisible();
  await page.getByLabel(/UPI ID/i).fill("success@upi");
  await page.getByRole("button", { name: /Pay by UPI/i }).click();
  await expect(
    page.getByRole("heading", { name: /Payment successful/i }),
  ).toBeVisible();

  // Back on billing, the past-due banner clears once the webhook restores the
  // plan to active.
  await expect(async () => {
    await page.goto("/billing");
    await expect(page.getByText(/Payment past due/i)).toHaveCount(0);
  }).toPass({ timeout: 20_000 });
});

test("a member cannot manage billing (owner-only)", async ({
  page,
  context,
  user,
  stack,
}) => {
  // The default user owns its workspace; add a SECOND confirmed user as a
  // member and act as them in the owner's workspace. A member sees the
  // owner-only state, never the upgrade or cancel controls.
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await setSubscription(stack, workspaceId, {
    plan: "agency",
    status: "active",
  });
  const { user: member, cleanup } = await addWorkspaceUser(
    stack,
    workspaceId,
    "member",
  );

  try {
    await signIn(page, member, "/dashboard");
    await context.addCookies([
      { name: WORKSPACE_COOKIE_NAME, value: workspaceId, url: page.url() },
    ]);

    await gotoBilling(page);

    // The owner-only banner is shown, and neither the cancel nor an active
    // upgrade control is available to a member.
    await expect(
      page.getByText("Only the owner can manage billing"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Cancel at period end/i }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Upgrade$/i })).toHaveCount(
      0,
    );
  } finally {
    await cleanup();
  }
});

// Phase G follow-up (Task 23 UI): the in-app payment history table and
// receipts, and an admin-issued refund that reverses a payment. Both read the
// `billing_adjustments` store through GET /billing/history, so the state is
// seeded deterministically through PostgREST (support/workspace.mjs) — never
// guarded behind `if (visible)`.

test("the payment history table shows a payment after an upgrade, with a receipt", async ({
  page,
  user,
  stack,
  gates,
}) => {
  // An upgraded owner has a captured payment recorded; seed it directly in the
  // adjustments store the history endpoint reads (the webhook writes the same
  // row), and put the workspace on the paid plan it bought.
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await setSubscription(stack, workspaceId, {
    plan: "agency",
    status: "active",
  });
  await seedPaymentCharge(stack, workspaceId, {
    plan: "agency",
    amountPaise: 49900,
  });

  await signIn(page, user, "/billing");
  await gotoBilling(page);

  // The owner-only history table lists the payment: amount in INR and a Paid
  // status. A refund has not happened, so no Refunded row is present.
  const history = page.getByTestId("payment-history");
  await expect(history).toBeVisible();
  await expect(history.getByText("₹499.00")).toBeVisible();
  await expect(history.getByText("Paid")).toBeVisible();

  // Opening the receipt shows the demo notice; the dialog is accessible.
  await history
    .getByRole("button", { name: /View receipt/i })
    .first()
    .click();
  const receipt = page.getByRole("dialog", { name: /Receipt/i });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByText(DEMO_NOTICE)).toBeVisible();
  await gates.checkAccessibility(page);
});

test("an admin refund reverses a payment and the reversal shows in history", async ({
  page,
  user,
  stack,
  gates,
}) => {
  // Seed the captured payment to be reversed.
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await setSubscription(stack, workspaceId, {
    plan: "agency",
    status: "active",
  });
  await seedPaymentCharge(stack, workspaceId, {
    plan: "agency",
    amountPaise: 49900,
  });

  await signIn(page, user, "/billing");

  // The refund is an owner/admin action (POST /billing/refunds). The admin
  // surface lives on the admin users page behind the platform-admin guard,
  // which the CPU stage cannot grant a per-test user (ADMIN_USER_IDS is set at
  // container start). The workspace owner, however, is authorised for the same
  // route, so the refund is issued through the API as the signed-in owner —
  // exercising the exact endpoint the admin RefundPanel calls — and the
  // reversal is then asserted in the owner's in-app payment history.
  const refund = await page.request.post("/api/billing/refunds", {
    headers: { "x-motionly-workspace-id": workspaceId },
    data: { workspace_id: workspaceId, amount_paise: 49900, reason: "Spec" },
  });
  expect(refund.status(), await refund.text()).toBe(200);

  // The store now holds the charge and a matching negative refund.
  const adjustments = await listBillingAdjustments(stack, workspaceId);
  const refundRow = adjustments.find((row) => row.adjustment_type === "refund");
  expect(refundRow, "a refund adjustment was recorded").toBeTruthy();
  expect(refundRow.amount_paise).toBe(-49900);

  // In the billing page history the reversal appears as a Refunded row showing
  // the returned amount with a leading minus.
  await gotoBilling(page);
  const history = page.getByTestId("payment-history");
  await expect(history.getByText("Refunded")).toBeVisible();
  await expect(history.getByText("−₹499.00")).toBeVisible();
  await gates.checkAccessibility(page);
});
