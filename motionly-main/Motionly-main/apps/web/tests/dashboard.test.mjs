import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("DashboardClient.tsx onboarding, and search handed to /projects", async () => {
  const source = await readFile(
    new URL("components/DashboardClient.tsx", root),
    "utf8",
  );

  // Search and sort run on /projects over every project (FE-27), tested as
  // behaviour in tests/components/dashboard-client.test.tsx.

  // Verify onboarding steps are defined
  assert.match(source, /Upload Video/);
  assert.match(source, /AI Drafting/);
  assert.match(source, /Fine-Tune/);
  assert.match(source, /Export & Download/);
  // Motionly does not post to social platforms.
  assert.doesNotMatch(source, /publish directly|Export & Post/i);
});

test("DashboardClient.tsx confirms deletes through the shared ConfirmDialog", async () => {
  const source = await readFile(
    new URL("components/DashboardClient.tsx", root),
    "utf8",
  );

  // The hand-rolled dialog (its own backdrop button, trap and double-submit
  // refs) is replaced by the shared ConfirmDialog, whose focus trap, Escape,
  // run-once and no-dismiss-while-pending behaviour is tested in
  // tests/components/confirm-dialog.test.tsx and dashboard-client.test.tsx.
  assert.match(source, /import \{ ConfirmDialog \} from "\.\/confirm-dialog"/);
  assert.match(source, /<ConfirmDialog/);
  assert.match(source, /onConfirm=\{confirmDelete\}/);
  assert.match(source, /const confirmDelete = async \(\) =>/);
  assert.doesNotMatch(source, /Close delete confirmation overlay/);
});
