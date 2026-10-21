import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  MANIFEST_UPDATED,
  PANEL_COLLABORATION,
  PANEL_PLAN,
  PLAN_REVISION_UPDATED,
  panelReloadForBroadcast,
  shouldApplyRevision,
} from "../lib/collaboration-reload.mjs";

const root = new URL("../", import.meta.url);

test("manifest_updated maps to the collaboration panel with its revision", () => {
  const result = panelReloadForBroadcast({
    event: MANIFEST_UPDATED,
    payload: { project_id: "p1", workspace_id: "w1", revision: 4 },
  });
  assert.deepEqual(result, {
    panel: PANEL_COLLABORATION,
    revision: 4,
    planId: null,
  });
});

test("plan_revision_updated maps to the plan panel with plan id and revision", () => {
  const result = panelReloadForBroadcast({
    event: PLAN_REVISION_UPDATED,
    payload: {
      project_id: "p1",
      workspace_id: "w1",
      plan_id: "plan-7",
      revision: 9,
    },
  });
  assert.deepEqual(result, {
    panel: PANEL_PLAN,
    revision: 9,
    planId: "plan-7",
  });
});

test("the two events map to different panels", () => {
  const a = panelReloadForBroadcast({
    event: MANIFEST_UPDATED,
    payload: { revision: 1 },
  });
  const b = panelReloadForBroadcast({
    event: PLAN_REVISION_UPDATED,
    payload: { revision: 1 },
  });
  assert.notEqual(a.panel, b.panel);
});

test("an unknown or malformed event is ignored (null)", () => {
  assert.equal(panelReloadForBroadcast({ event: "nonsense" }), null);
  assert.equal(panelReloadForBroadcast({}), null);
  assert.equal(panelReloadForBroadcast(null), null);
  assert.equal(
    panelReloadForBroadcast({ event: PLAN_REVISION_UPDATED }).revision,
    null,
  );
});

test("shouldApplyRevision ignores a stale echo of this client's own save", () => {
  // The client already tracks revision 5; a broadcast at 5 (its own save
  // echoing back) or lower must NOT trigger a reload.
  assert.equal(shouldApplyRevision(5, 5), false);
  assert.equal(shouldApplyRevision(4, 5), false);
  // A genuinely newer revision from a peer IS applied.
  assert.equal(shouldApplyRevision(6, 5), true);
  // No tracked revision yet, or no incoming revision: act to be safe.
  assert.equal(shouldApplyRevision(3, null), true);
  assert.equal(shouldApplyRevision(null, 5), true);
});

test("ScalePanel wires the private project channel to the reload mapping", async () => {
  const source = await readFile(
    new URL("components/editor/ScalePanel.tsx", root),
    "utf8",
  );
  // The listener joins the Task 25 PRIVATE project topic (not the old
  // non-private, unfed topic name), so RLS authorizes it.
  assert.match(source, /channel\(\s*`project:\$\{projectId\}`/);
  assert.match(source, /private:\s*true/);
  // It consumes the shared reload mapping rather than hand-rolling the logic.
  assert.match(
    source,
    /panelReloadForBroadcast[\s\S]*from "@\/lib\/collaboration-reload\.mjs"/,
  );
  assert.match(source, /shouldApplyRevision/);
  // Both broadcast events are handled.
  assert.match(source, /MANIFEST_UPDATED/);
  assert.match(source, /PLAN_REVISION_UPDATED/);
  // Online indicator names come from the workspace members endpoint.
  assert.match(source, /\/team\/members/);
});
