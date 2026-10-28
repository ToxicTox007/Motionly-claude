import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("DashboardClient.tsx implements search, sort, and onboarding correctly", async () => {
  const source = await readFile(
    new URL("components/DashboardClient.tsx", root),
    "utf8",
  );

  // Verify search and sort state variables
  assert.match(source, /const \[searchQuery, setSearchQuery\] = useState/);
  assert.match(source, /const \[sortBy, setSortBy\] = useState/);

  // Verify filter logic
  assert.match(source, /filteredProjects = useMemo/);
  assert.match(source, /\.includes\(query\)/);

  // Verify sorting options logic
  assert.match(source, /sortBy === "updated"/);
  assert.match(source, /a\.title\.localeCompare\(b\.title\)/);

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
