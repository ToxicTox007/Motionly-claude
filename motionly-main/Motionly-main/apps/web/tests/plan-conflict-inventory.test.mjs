import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

// ---------------------------------------------------------------------------
// B22: every web call site that mutates a plan must (a) send the expected
// revision and (b) route a 409 into the ConflictDialog — never swallow it and
// never blindly retry. This test enumerates the known plan-mutation endpoints
// and the files that call them; a new mutation that forgets either rule fails.
// ---------------------------------------------------------------------------

// Plan-content endpoints. Hitting any of these without `expected_revision` is a
// silent-data-loss bug. (Layer endpoints like /captions/.../segments,
// /broll/.../region, /music/regions mutate their own resource and trigger a
// separate compile-timeline rebuild which IS covered below.)
const PLAN_MUTATION_ENDPOINTS = [
  "/cuts",
  "/caption-highlights",
  "/manual-elements",
  "/timeline",
  "/compile-timeline",
];

// Files that contain plan-mutation call sites.
const MUTATION_SOURCE_FILES = [
  "components/editor-client.tsx",
  "components/editor/timeline/TimelinePanel.tsx",
];

async function readSource(rel) {
  return readFile(new URL(rel, root), "utf8");
}

test("every plan-mutation source file sends expected_revision on writes", async () => {
  for (const rel of MUTATION_SOURCE_FILES) {
    const source = await readSource(rel);
    // Any file that PATCHes/POSTs a plan endpoint must also assemble an
    // expected_revision into the request body.
    const touchesPlan = PLAN_MUTATION_ENDPOINTS.some((ep) =>
      source.includes(ep),
    );
    if (!touchesPlan) continue;
    assert.match(
      source,
      /expected_revision: expectedRev|expected_revision: latestRevision|expected_revision: revision/,
      `${rel} mutates a plan but never sends expected_revision`,
    );
    assert.match(
      source,
      /getExpectedRevision/,
      `${rel} must read the tracked revision via getExpectedRevision`,
    );
  }
});

test("every plan-mutation source file routes conflicts to the dialog", async () => {
  for (const rel of MUTATION_SOURCE_FILES) {
    const source = await readSource(rel);
    const touchesPlan = PLAN_MUTATION_ENDPOINTS.some((ep) =>
      source.includes(ep),
    );
    if (!touchesPlan) continue;
    // Conflicts must be handed to handleConflict / onConflictDetected.
    assert.match(
      source,
      /handleConflict\(|onConflictDetected\?\.\(|onConflictDetected\(/,
      `${rel} mutates a plan but does not route 409s to the conflict handler`,
    );
  }
});

test("compile-timeline (canonical rebuild) no longer swallows conflicts silently", async () => {
  const source = await readSource("components/editor-client.tsx");
  // The compile-timeline catch must not be a bare console.error swallow.
  // It must register a conflict handler for the "timeline_rebuild" source.
  assert.match(source, /handleConflict\(error, "timeline_rebuild"/);
});

test("manual-element saves register a rollback and retry on conflict", async () => {
  const source = await readSource("components/editor-client.tsx");
  assert.match(source, /onConflictDetected\(error, "manual_elements", \{/);
  // Rollback restores the canvas to the pre-edit elements.
  assert.match(source, /rollback:/);
  // Retry re-sends the same change.
  assert.match(source, /retry:/);
});

test("timeline rebuilds are coalesced, not fired per layer change", async () => {
  const source = await readSource("components/editor-client.tsx");
  // The layer handlers must go through the coalescer, not call the raw
  // synchronize function directly.
  assert.match(source, /createRebuildCoalescer/);
  assert.match(source, /requestTimelineRebuild\(\)/);
  assert.doesNotMatch(
    source,
    /void synchronizeCanonicalTimeline\(\)/,
    "layer handlers must use the coalesced requestTimelineRebuild, not raw sync",
  );
});

test("force save is not a no-op: it re-sends the change via a retry handler", async () => {
  const hook = await readSource("hooks/useRevisionTracking.ts");
  // forceSave must fetch the latest revision and invoke the registered retry.
  assert.match(hook, /const forceSave = useCallback/);
  assert.match(hook, /fetchLatestRevision\?\.\(\)/);
  assert.match(hook, /handlers\.retry\(latest\)/);

  const editor = await readSource("components/editor-client.tsx");
  // The dialog's Force Save must be wired to the real forceSave, not clearConflict.
  assert.match(editor, /onConflictForceSave=\{forceSave\}/);
  assert.doesNotMatch(editor, /onConflictForceSave=\{clearConflict\}/);
});
