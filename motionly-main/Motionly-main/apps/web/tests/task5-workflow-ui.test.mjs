import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  assertCanonicalWorkflowIdentity,
  assertWorkflowCommandTransition,
  isCanonicalDraftReady,
  isProposalRevisionCompatible,
  proposalDecisionIdempotencyKey,
  proposalIdentityFromWorkflow,
  selectLatestFirstDraftWorkflow,
  workflowCommandIdempotencyKey,
} from "../lib/workflow-ui.mjs";

const root = new URL("../", import.meta.url);
const base = {
  id: "run-new",
  project_id: "project-1",
  workflow_type: "first_draft",
  workflow_version: "first-draft-v2",
  state: "awaiting_approval",
  revision: 8,
  uploaded_video_id: "upload-1",
  source_media_revision: 3,
  root_workflow_run_id: "run-root",
  retry_of_workflow_run_id: null,
  allowed_actions: ["cancel", "approve_proposal", "reject_proposal"],
};

test("latest canonical first-draft selection preserves latest-first API order across terminal states", () => {
  const olderActive = { ...base, id: "run-old", state: "analyzing" };
  const latestCancelled = { ...base, id: "run-new", state: "cancelled" };
  assert.equal(
    selectLatestFirstDraftWorkflow([latestCancelled, olderActive], "project-1")
      ?.id,
    "run-new",
  );
  assert.equal(
    selectLatestFirstDraftWorkflow(
      [
        { ...base, project_id: "other" },
        { ...base, workflow_version: "v1" },
      ],
      "project-1",
    ),
    null,
  );
});

test("canonical detail rejects project and selected-run identity mismatches", () => {
  assert.throws(
    () =>
      assertCanonicalWorkflowIdentity(
        { ...base, project_id: "other" },
        { projectId: "project-1" },
      ),
    /identity/,
  );
  assert.throws(
    () =>
      assertCanonicalWorkflowIdentity(base, {
        projectId: "project-1",
        expectedWorkflowId: "other-run",
      }),
    /selected run/,
  );
});

test("first draft is ready only with matching canonical render and export IDs", () => {
  const ready = {
    ...base,
    state: "draft_ready",
    draft_render: { id: "render-1" },
    draft_export: { id: "export-1", render_job_id: "render-1" },
  };
  assert.equal(isCanonicalDraftReady(ready), true);
  assert.equal(isCanonicalDraftReady({ ...ready, state: "assembling" }), false);
  assert.equal(isCanonicalDraftReady({ ...ready, draft_render: null }), false);
  assert.equal(
    isCanonicalDraftReady({
      ...ready,
      draft_export: { id: "export-1", render_job_id: "unrelated" },
    }),
    false,
  );
});

test("proposal recovery comes only from the workflow artifact identity", () => {
  const detail = {
    ...base,
    artifacts: [
      { type: "proposal", id: "proposal-1", revision: 4 },
      { type: "draft_export", id: "export-1", revision: 1 },
    ],
  };
  assert.deepEqual(proposalIdentityFromWorkflow(detail), {
    proposalId: "proposal-1",
    linkedRevision: 4,
  });
  assert.equal(isProposalRevisionCompatible(1, 3), true);
  assert.equal(isProposalRevisionCompatible(4, 4), true);
  assert.equal(isProposalRevisionCompatible(4, 3), false);
  assert.equal(
    proposalIdentityFromWorkflow({ ...detail, state: "assembling" }),
    null,
  );
  assert.equal(
    proposalIdentityFromWorkflow({ ...detail, artifacts: [] }),
    null,
  );
});

test("workflow commands preserve canonical run and source lineage", () => {
  const cancelled = {
    ...base,
    state: "cancelled",
    revision: base.revision + 1,
    allowed_actions: ["retry"],
  };
  assert.equal(
    assertWorkflowCommandTransition(base, cancelled, "cancel"),
    cancelled,
  );

  const retried = {
    ...base,
    id: "run-retry",
    state: "uploaded",
    revision: 1,
    retry_of_workflow_run_id: base.id,
    allowed_actions: ["cancel"],
  };
  assert.equal(
    assertWorkflowCommandTransition(base, retried, "retry"),
    retried,
  );
  assert.throws(
    () =>
      assertWorkflowCommandTransition(
        base,
        { ...retried, retry_of_workflow_run_id: "other-run" },
        "retry",
      ),
    /retry lineage/,
  );
  assert.throws(
    () =>
      assertWorkflowCommandTransition(
        base,
        { ...retried, source_media_revision: 4 },
        "retry",
      ),
    /immutable lineage/,
  );
  assert.throws(
    () =>
      assertWorkflowCommandTransition(
        base,
        { ...cancelled, id: "other-run" },
        "cancel",
      ),
    /cancellation/,
  );
});

test("workflow and proposal intent keys are deterministic, bounded, and order independent", async () => {
  assert.equal(
    workflowCommandIdempotencyKey(base, "cancel"),
    workflowCommandIdempotencyKey({ ...base }, "cancel"),
  );
  const intent = {
    workspaceId: "workspace-1",
    projectId: "project-1",
    proposalId: "proposal-1",
    proposalRevision: 4,
    decision: "partial_approve",
    actionIds: ["action-b", "action-a"],
  };
  const first = await proposalDecisionIdempotencyKey(intent, webcrypto);
  const second = await proposalDecisionIdempotencyKey(
    { ...intent, actionIds: ["action-a", "action-b", "action-a"] },
    webcrypto,
  );
  assert.equal(first, second);
  assert.match(first, /^proposal-decision:[a-f0-9]{64}$/);
  assert.ok(first.length <= 128);
  assert.notEqual(
    first,
    await proposalDecisionIdempotencyKey(
      { ...intent, decision: "reject" },
      webcrypto,
    ),
  );
});

test("source contracts hydrate list then detail, poll with headers, and never persist proposals", async () => {
  const [page, hook, aiStep, client, panelLoader] = await Promise.all([
    readFile(new URL("app/(app)/projects/[projectId]/page.tsx", root), "utf8"),
    readFile(new URL("hooks/useWorkflowStatus.ts", root), "utf8"),
    readFile(new URL("components/editor/ai-step.tsx", root), "utf8"),
    readFile(new URL("components/editor-client.tsx", root), "utf8"),
    readFile(new URL("lib/editor-panel-loader.ts", root), "utf8"),
  ]);
  // The workflow list is one of the independently loaded editor panels.
  assert.match(panelLoader, /\/workflows\?limit=50/);
  assert.match(page, /selectLatestFirstDraftWorkflow/);
  assert.match(page, /initialWorkflow=/);
  assert.match(hook, /authenticated-adaptive-polling/);
  assert.match(hook, /startAdaptivePolling/);
  assert.match(hook, /expected_revision: current\.revision/);
  assert.match(hook, /pendingCommandRef\.current/);
  assert.match(hook, /assertWorkflowCommandTransition/);
  assert.match(hook, /setWorkflow\(next\)/);
  assert.doesNotMatch(hook, /accessToken[^\n]*[?&=]/);
  assert.doesNotMatch(
    aiStep,
    /sessionStorage|PendingProposalIdentity|crypto\.randomUUID/,
  );
  assert.match(aiStep, /proposalIdentityFromWorkflow/);
  assert.match(aiStep, /isProposalRevisionCompatible/);
  assert.match(aiStep, /onWorkflowRefresh/);
  assert.match(client, /useWorkflowStatus/);
  // The toolbar's commands are memoized around the hook's runCommand.
  assert.match(client, /runWorkflowCommand = workflowStatus\.runCommand/);
  assert.match(client, /runWorkflowCommand\("cancel"\)/);
  assert.match(client, /runWorkflowCommand\("retry"\)/);
});
