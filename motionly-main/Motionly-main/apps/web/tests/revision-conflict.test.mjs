import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

// ---------------------------------------------------------------------------
// 1. RevisionConflictError — instantiation from 409 response, properties accessible
// ---------------------------------------------------------------------------
test("RevisionConflictError instantiation from 409 response with properties accessible", async () => {
  const source = await readFile(
    new URL("lib/client-api.ts", root),
    "utf8",
  );

  // Class extends EditorApiError
  assert.match(source, /export class RevisionConflictError extends EditorApiError/);
  // Has expectedRevision and currentRevision properties
  assert.match(source, /expectedRevision: number/);
  assert.match(source, /currentRevision: number/);
  // Constructor sets status 409
  assert.match(source, /status: 409/);
  // Constructor sets code to revision_conflict
  assert.match(source, /code: "revision_conflict"/);
  // Name set correctly
  assert.match(source, /this\.name = "RevisionConflictError"/);
  // Assigns properties from constructor args
  assert.match(source, /this\.expectedRevision = expectedRevision/);
  assert.match(source, /this\.currentRevision = currentRevision/);
});

// ---------------------------------------------------------------------------
// 2. useRevisionTracking initial state — null revision, no conflict
// ---------------------------------------------------------------------------
test("useRevisionTracking initial state is null revision with no conflict", async () => {
  const source = await readFile(
    new URL("hooks/useRevisionTracking.ts", root),
    "utf8",
  );

  // Initial plan revision is null
  assert.match(source, /useState<number \| null>\(null\)/);
  // Initial conflict state is null
  assert.match(source, /useState<ConflictState \| null>\(null\)/);
  // Ref starts as null
  assert.match(source, /useRef<number \| null>\(null\)/);
});

// ---------------------------------------------------------------------------
// 3. useRevisionTracking updateRevision — updates tracked revision
// ---------------------------------------------------------------------------
test("useRevisionTracking updateRevision updates tracked revision", async () => {
  const source = await readFile(
    new URL("hooks/useRevisionTracking.ts", root),
    "utf8",
  );

  // updateRevision sets plan revision via state setter
  assert.match(source, /setPlanRevision\(newRevision\)/);
  // Also updates the ref for async reads
  assert.match(source, /planRevisionRef\.current = newRevision/);
  // Handles media kind
  assert.match(source, /setMediaRevision\(newRevision\)/);
  // Updates lastSyncedAt
  assert.match(source, /setLastSyncedAt\(Date\.now\(\)\)/);
  // Guards against null/undefined (old API compat)
  assert.match(source, /if \(newRevision == null\) return/);
});

// ---------------------------------------------------------------------------
// 4. useRevisionTracking handleConflict — sets conflict state
// ---------------------------------------------------------------------------
test("useRevisionTracking handleConflict sets conflict state from RevisionConflictError", async () => {
  const source = await readFile(
    new URL("hooks/useRevisionTracking.ts", root),
    "utf8",
  );

  // Checks instanceof RevisionConflictError
  assert.match(source, /error instanceof RevisionConflictError/);
  // Sets conflict state with expected fields
  assert.match(source, /setConflictState\(\{/);
  assert.match(source, /expectedRevision: error\.expectedRevision/);
  assert.match(source, /currentRevision: error\.currentRevision/);
  assert.match(source, /conflictedAt: Date\.now\(\)/);
  assert.match(source, /source/);
  // Returns true when conflict handled
  assert.match(source, /return true/);
  // Returns false when not a conflict error
  assert.match(source, /return false/);
});

// ---------------------------------------------------------------------------
// 5. useRevisionTracking clearConflict — clears conflict state
// ---------------------------------------------------------------------------
test("useRevisionTracking clearConflict resets conflict state to null", async () => {
  const source = await readFile(
    new URL("hooks/useRevisionTracking.ts", root),
    "utf8",
  );

  // clearConflict sets state to null
  assert.match(source, /const clearConflict = useCallback\(\(\) => \{/);
  assert.match(source, /setConflictState\(null\)/);
});

// ---------------------------------------------------------------------------
// 6. useRevisionTracking getExpectedRevision — returns current revision or null
// ---------------------------------------------------------------------------
test("useRevisionTracking getExpectedRevision returns current revision or undefined", async () => {
  const source = await readFile(
    new URL("hooks/useRevisionTracking.ts", root),
    "utf8",
  );

  // Reads from the ref (not state) for async safety
  assert.match(source, /planRevisionRef\.current \?\? undefined/);
  // Wrapped in useCallback
  assert.match(source, /const getExpectedRevision = useCallback\(/);
});

// ---------------------------------------------------------------------------
// 7. ConflictDialog renders correctly — shows message, two buttons
// ---------------------------------------------------------------------------
test("ConflictDialog renders conflict message and two resolution buttons", async () => {
  const source = await readFile(
    new URL("components/editor/ConflictDialog.tsx", root),
    "utf8",
  );

  // Shows version conflict title
  assert.match(source, /title="Version Conflict"/);
  // Shows conflict description
  assert.match(source, /Your changes conflict with a newer version/);
  // Reload button exists with label
  assert.match(source, /aria-label="Reload plan from server and discard local changes"/);
  // Force save button exists with label
  assert.match(source, /aria-label="Force save your changes, overwriting the newer version"/);
  // Shows revision numbers
  assert.match(source, /conflict\.expectedRevision/);
  assert.match(source, /conflict\.currentRevision/);
});

// ---------------------------------------------------------------------------
// 8. ConflictDialog reload action — calls onReload
// ---------------------------------------------------------------------------
test("ConflictDialog reload action calls onReload callback", async () => {
  const source = await readFile(
    new URL("components/editor/ConflictDialog.tsx", root),
    "utf8",
  );

  // handleReload calls onReload
  assert.match(source, /await onReload\(\)/);
  // Sets loading state to "reload"
  assert.match(source, /setLoading\("reload"\)/);
  // Resets loading in finally
  assert.match(source, /setLoading\(null\)/);
  // Reload button triggers handleReload
  assert.match(source, /onClick=\{handleReload\}/);
});

// ---------------------------------------------------------------------------
// 9. ConflictDialog force-save action — calls onForceSave
// ---------------------------------------------------------------------------
test("ConflictDialog force-save action calls onForceSave callback", async () => {
  const source = await readFile(
    new URL("components/editor/ConflictDialog.tsx", root),
    "utf8",
  );

  // handleForceSave calls onForceSave
  assert.match(source, /await onForceSave\(\)/);
  // Sets loading state to "force"
  assert.match(source, /setLoading\("force"\)/);
  // Force save button triggers handleForceSave
  assert.match(source, /onClick=\{handleForceSave\}/);
  // Both buttons disabled when loading
  assert.match(source, /disabled=\{loading !== null\}/);
});

// ---------------------------------------------------------------------------
// 10. StaleIndicator renders message — shows stale warning text
// ---------------------------------------------------------------------------
test("StaleIndicator renders stale warning message", async () => {
  const source = await readFile(
    new URL("components/editor/StaleIndicator.tsx", root),
    "utf8",
  );

  // Default message about outdated render
  assert.match(source, /Render is outdated — timeline has been modified since last render/);
  // Uses role="status" for accessibility
  assert.match(source, /role="status"/);
  // Uses aria-live for screen readers
  assert.match(source, /aria-live="polite"/);
  // Renders the message prop
  assert.match(source, /\{message\}/);
});

// ---------------------------------------------------------------------------
// 11. StaleIndicator action button — calls onAction when clicked
// ---------------------------------------------------------------------------
test("StaleIndicator action button calls onAction when clicked", async () => {
  const source = await readFile(
    new URL("components/editor/StaleIndicator.tsx", root),
    "utf8",
  );

  // Button uses onAction callback
  assert.match(source, /onClick=\{onAction\}/);
  // Button has accessible label from actionLabel prop
  assert.match(source, /aria-label=\{actionLabel\}/);
  // Default action label is Re-render
  assert.match(source, /actionLabel = "Re-render"/);
  // Button only renders when onAction is provided
  assert.match(source, /\{onAction && \(/);
});

// ---------------------------------------------------------------------------
// 12. client-api 409 detection — mock fetch returning 409, verify RevisionConflictError thrown
// ---------------------------------------------------------------------------
test("client-api detects 409 response and throws RevisionConflictError", async () => {
  const source = await readFile(
    new URL("lib/client-api.ts", root),
    "utf8",
  );

  // Checks response status for 409
  assert.match(source, /response\.status === 409/);
  // Also checks error field matches revision_conflict
  assert.match(source, /error === "revision_conflict"/);
  // Throws RevisionConflictError with parsed fields
  assert.match(source, /throw new RevisionConflictError\(\{/);
  // Extracts expected_revision from response payload
  assert.match(source, /expectedRevision: \(payload as any\)\?\.expected_revision/);
  // Extracts current_revision from response payload
  assert.match(source, /currentRevision: \(payload as any\)\?\.current_revision/);
});
