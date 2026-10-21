// Web source-contract tests for the Phase G follow-up "missing UI" (the UI for
// four API features nothing in the app called): billing payment history +
// in-app receipts and the admin refund surface (Task 23), and the team
// leave/transfer-ownership controls (Task 21). Following the project's
// convention for server/client components, these assert the pieces that matter
// without a DOM: that each of the four routes now has a product call site, that
// the surfaces are role-gated and accessible (labels, focus-trapped dialogs,
// announced status), and that the demo notice and amount validation are wired.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

test("lib/billing reads payment history from GET /billing/history", async () => {
  const lib = await source("lib/billing.ts");
  assert.match(lib, /export async function getPaymentHistory/);
  assert.match(lib, /\/billing\/history/);
  assert.match(lib, /body\.receipts \?\? \[\]/);
});

test("the billing page shows owner-only payment history", async () => {
  const page = await source("app/(app)/billing/page.tsx");
  // History is fetched only for the owner and passed to the client table.
  assert.match(page, /getPaymentHistory/);
  assert.match(page, /isOwner \? await getPaymentHistory\(\)/);
  assert.match(page, /<PaymentHistory/);
  assert.match(page, /isOwner \? \(\s*<PaymentHistory/);
});

test("the payment history table is accessible with date, plan, amount, status and a receipt view", async () => {
  const client = await source("app/(app)/billing/PaymentHistory.tsx");
  assert.match(client, /data-testid="payment-history"/);
  assert.match(client, /<caption className="sr-only">/);
  // The four required columns.
  assert.match(client, /Date/);
  assert.match(client, /Plan/);
  assert.match(client, /Amount/);
  assert.match(client, /Status/);
  // Amounts are formatted through the pure helper (INR).
  assert.match(client, /receiptRow/);
  assert.match(client, /from "@\/lib\/payment-history\.mjs"/);
  // A per-row receipt view opens a focus-trapped dialog.
  assert.match(client, /View receipt/);
  assert.match(client, /useFocusTrap\(true, onClose\)/);
  assert.match(client, /role="dialog"/);
  assert.match(client, /aria-modal="true"/);
  assert.match(client, /aria-labelledby="receipt-title"/);
  // Each receipt button is labelled.
  assert.match(client, /aria-label=\{`View receipt for /);
  // Demo notice on the payment surface.
  assert.match(client, /Demo payment — no money is charged/);
});

test("the admin refund action posts to POST /billing/refunds with validation", async () => {
  const actions = await source("app/(app)/admin/users/actions.ts");
  assert.match(actions, /"use server"/);
  assert.match(actions, /export async function issueRefund/);
  assert.match(actions, /\/billing\/refunds/);
  assert.match(actions, /getAdminApiContext/);
  // Amount is validated with the shared pure helper before sending.
  assert.match(actions, /validateRefundRupees/);
  assert.match(actions, /refund_workspace_required/);
  // Empty amount omits amount_paise so the API refunds the full plan price.
  assert.match(actions, /amount\.amountPaise != null/);
  assert.match(actions, /\{ amount_paise: amount\.amountPaise \}/);
});

test("the admin refund surface confirms before issuing and shows the demo notice", async () => {
  const panel = await source("app/(app)/admin/users/RefundPanel.tsx");
  assert.match(panel, /data-testid="admin-refund"/);
  assert.match(panel, /issueRefund/);
  // Amount validation client-side with the same helper.
  assert.match(panel, /validateRefundRupees/);
  // A focus-trapped confirmation dialog.
  assert.match(panel, /useFocusTrap\(true, onClose\)/);
  assert.match(panel, /role="alertdialog"/);
  assert.match(panel, /aria-modal="true"/);
  assert.match(panel, /Confirm refund/);
  // Labelled inputs and announced status.
  assert.match(panel, /htmlFor="refund-workspace"/);
  assert.match(panel, /htmlFor="refund-amount"/);
  assert.match(panel, /role="status"/);
  assert.match(panel, /role="alert"/);
  // Demo notice.
  assert.match(panel, /Demo payment — no money is charged/);
  // It is rendered on the admin users page.
  const page = await source("app/(app)/admin/users/page.tsx");
  assert.match(page, /<RefundPanel/);
});

test("a member can leave the workspace via POST /team/leave and the API's refusal is shown", async () => {
  const actions = await source("app/(app)/account/team/actions.ts");
  assert.match(actions, /export async function leaveWorkspace/);
  assert.match(actions, /\/team\/leave/);
  // The API's human message (the last-owner refusal) is surfaced.
  assert.match(actions, /message: body\?\.error\?\.message/);

  const client = await source("app/(app)/account/team/TeamClient.tsx");
  assert.match(client, /leaveWorkspace/);
  assert.match(client, /const onLeave = \(\) =>/);
  // The leave control is confirmed and labelled.
  assert.match(client, /Leave workspace/);
  assert.match(client, /aria-labelledby="leave-heading"/);
  // The last-owner refusal message is shown verbatim in the error region.
  assert.match(client, /result\.message \?\? result\.error/);
  // On success the member leaves and is sent away (loses access).
  assert.match(client, /router\.push\("\/dashboard"\)/);
});

test("the owner can transfer ownership via POST /team/transfer-ownership, owner-only, naming the new owner", async () => {
  const actions = await source("app/(app)/account/team/actions.ts");
  assert.match(actions, /export async function transferOwnership/);
  assert.match(actions, /\/team\/transfer-ownership/);
  assert.match(actions, /context\.role !== "owner"/);
  assert.match(actions, /body: JSON\.stringify\(\{ user_id: userId \}\)/);

  const client = await source("app/(app)/account/team/TeamClient.tsx");
  assert.match(client, /transferOwnership/);
  assert.match(client, /const onTransfer = /);
  // Owner-only control.
  assert.match(client, /viewerRole === "owner" \? \(/);
  assert.match(
    client,
    /aria-label=\{`Transfer ownership to \$\{member\.name\}`\}/,
  );
  // The confirmation dialog names the new owner.
  assert.match(client, /will become the workspace owner/);
  assert.match(client, /confirmLabel: `Make \$\{member\.name\} owner`/);
});
