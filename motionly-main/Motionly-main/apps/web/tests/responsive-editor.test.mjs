import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("editor-layout.tsx implements responsive shells and sheets correctly", async () => {
  const [layoutSource, workspaceSource] = await Promise.all([
    readFile(new URL("components/editor-layout.tsx", root), "utf8"),
    readFile(
      new URL("components/editor/layout/EditorWorkspaces.tsx", root),
      "utf8",
    ),
  ]);
  const source = `${layoutSource}\n${workspaceSource}`;

  // Verify imports
  assert.match(source, /import.*useFocusTrap.*from.*@\/lib\/useFocusTrap/);
  assert.match(source, /const \[activeSheet, setActiveSheet\] = useState/);

  // Verify mobile responsive shell visibility classes
  assert.match(source, /lg:hidden/);
  assert.match(source, /hidden[^\"]*lg:grid/);

  // Verify mobile tab bar button labels
  assert.match(source, /Preview/);
  assert.match(source, /Elements/);
  assert.match(source, /Settings/);

  // Verify slide-out drawers
  assert.match(source, /activeSheet === "left"/);
  assert.match(source, /activeSheet === "right"/);
  assert.match(source, /Element palette/i);
  assert.match(source, /Editor settings/i);
});

test("editor-layout.tsx implements accessible drawers with focus traps", async () => {
  const [layoutSource, workspaceSource] = await Promise.all([
    readFile(new URL("components/editor-layout.tsx", root), "utf8"),
    readFile(
      new URL("components/editor/layout/EditorWorkspaces.tsx", root),
      "utf8",
    ),
  ]);
  const source = `${layoutSource}\n${workspaceSource}`;

  // Verify both focus traps are passed into the shared accessible drawer.
  assert.match(source, /panelRef=\{leftTrapRef\}/);
  assert.match(source, /panelRef=\{rightTrapRef\}/);
  assert.match(source, /ref=\{panelRef\}/);

  const dialogMatches = source.match(/role="dialog"/g);
  assert.equal(
    dialogMatches?.length,
    1,
    "The shared drawer should declare a dialog role",
  );

  const modalMatches = source.match(/aria-modal="true"/g);
  assert.equal(modalMatches?.length, 1, "The shared drawer should be modal");
});
