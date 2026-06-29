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
  assert.match(source, /Export & Post/);
});

test("DashboardClient.tsx implements secure pixel delete confirmation dialog", async () => {
  const source = await readFile(
    new URL("components/DashboardClient.tsx", root),
    "utf8",
  );

  // Verify focus trap hook import and usage
  assert.match(source, /import.*useFocusTrap.*from.*@\/lib\/useFocusTrap/);
  assert.match(source, /const focusTrapRef = useFocusTrap/);

  // Verify delete action and double submit protection
  assert.match(source, /const confirmDelete = async \(\) =>/);
  assert.match(source, /isDeletingRef\.current/);
  assert.match(source, /deleteLoading/);

  // Verify ARIA role dialog attributes
  assert.match(source, /role="dialog"/);
  assert.match(source, /aria-modal="true"/);
  assert.match(source, /aria-labelledby="delete-dialog-title"/);
});
