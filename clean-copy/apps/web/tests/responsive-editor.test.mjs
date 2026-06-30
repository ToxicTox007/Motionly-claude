import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("editor-layout.tsx implements responsive shells and sheets correctly", async () => {
  const source = await readFile(
    new URL("components/editor-layout.tsx", root),
    "utf8",
  );

  // Verify imports
  assert.match(source, /import.*useFocusTrap.*from.*@\/lib\/useFocusTrap/);
  assert.match(source, /const \[activeSheet, setActiveSheet\] = useState/);

  // Verify mobile responsive shell visibility classes
  assert.match(source, /lg:hidden/);
  assert.match(source, /hidden lg:grid/);

  // Verify mobile tab bar button labels
  assert.match(source, /Preview/);
  assert.match(source, /Elements/);
  assert.match(source, /Settings/);

  // Verify slide-out drawers
  assert.match(source, /activeSheet === "left"/);
  assert.match(source, /activeSheet === "right"/);
  assert.match(source, /Element Palette/);
  assert.match(source, /Editor Settings/);
});

test("editor-layout.tsx implements accessible drawers with focus traps", async () => {
  const source = await readFile(
    new URL("components/editor-layout.tsx", root),
    "utf8",
  );

  // Verify focus trap bindings on drawers
  assert.match(source, /ref=\{leftTrapRef\}/);
  assert.match(source, /ref=\{rightTrapRef\}/);

  // Verify ARIA dialog declarations on drawers
  const dialogMatches = source.match(/role="dialog"/g);
  assert.equal(
    dialogMatches?.length,
    2,
    "Both left and right overlay sheets should have dialog roles",
  );

  const modalMatches = source.match(/aria-modal="true"/g);
  assert.equal(
    modalMatches?.length,
    2,
    "Both overlay sheets should have aria-modal set to true",
  );
});
