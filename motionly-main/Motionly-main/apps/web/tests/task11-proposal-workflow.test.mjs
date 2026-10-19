import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

function section(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
  assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
  return source.slice(start, end);
}

function assertOrdered(source, markers) {
  let previous = -1;
  for (const marker of markers) {
    const index = source.indexOf(marker);
    assert.ok(index > previous, `${marker} must appear in workflow order`);
    previous = index;
  }
}

test("generation and canonical recovery stop at proposal review before timeline or layer mutation", async () => {
  const source = await readFile(
    new URL("components/editor/ai-step.tsx", root),
    "utf8",
  );
  const generate = section(
    source,
    "  async function generatePlan() {",
    "  function beginPlanOperation() {",
  );
  const recovery = section(
    source,
    "  const workflowProposalIdentity = proposalIdentityFromWorkflow(workflow);",
    "  function toggleProposalAction(",
  );

  for (const preDecisionFlow of [generate, recovery]) {
    assert.doesNotMatch(preDecisionFlow, /onPlanGenerated\(/);
    assert.doesNotMatch(preDecisionFlow, /ensureCaptions\(/);
    assert.doesNotMatch(preDecisionFlow, /ensureBroll\(/);
    assert.doesNotMatch(preDecisionFlow, /ensureMusic\(/);
    assert.doesNotMatch(preDecisionFlow, /compile-timeline/);
    assert.match(preDecisionFlow, /showProposalForReview\(/);
  }
  assert.doesNotMatch(source, /sessionStorage|PendingProposalIdentity/);
});

test("rejection sends no actions and exits without mutating active plan or downstream layers", async () => {
  const source = await readFile(
    new URL("components/editor/ai-step.tsx", root),
    "utf8",
  );
  const decision = section(
    source,
    "  async function applyProposalDecision(",
    "  async function ensureCaptions(",
  );
  const rejection = section(
    decision,
    '      if (decision === "reject") {',
    "      if (!response.edit_plan_id",
  );

  assert.match(
    decision,
    /decision === "partial_approve" \? acceptedActionIds : \[\]/,
  );
  assert.match(rejection, /current timeline was not changed/);
  assert.doesNotMatch(rejection, /onPlanGenerated\(/);
  assert.doesNotMatch(rejection, /ensureCaptions\(/);
  assert.doesNotMatch(rejection, /ensureBroll\(/);
  assert.doesNotMatch(rejection, /ensureMusic\(/);
  assert.doesNotMatch(rejection, /compile-timeline/);
  assert.match(
    rejection,
    /if \(workflowOwned\)[\s\S]*await onWorkflowRefresh\(\)[\s\S]*router\.refresh\(\)/,
  );
});

test("approval confirms the authoritative plan and workflow-owned approval bypasses client mutations", async () => {
  const source = await readFile(
    new URL("components/editor/ai-step.tsx", root),
    "utf8",
  );
  const decision = section(
    source,
    "  async function applyProposalDecision(",
    "  async function ensureCaptions(",
  );

  assertOrdered(decision, [
    "const response = await editorApiRequest<ProposalDecisionResponse>",
    "const planList = await editorApiRequest",
    ".find(",
    "onPlanGenerated(approvedPlan)",
    "planAppliedLocally = true;\n      if (workflowOwned)",
    "Workflow assembly is continuing from plan revision ${approvedPlan.revision}.`,\n        );\n        await onWorkflowRefresh()",
    "Standalone proposals retain the legacy client-owned downstream flow",
    "ensureCaptions(signal)",
    "ensureBroll(signal)",
    "ensureMusic(signal)",
    "compile-timeline?expected_revision=${approvedPlan.revision}",
    "const compiledRevision = compiled.revision ?? compiled.plan.revision",
    "onPlanGenerated(compiledPlan)",
  ]);
  assert.match(
    decision,
    /approvedPlan\.revision !== response\.edit_plan_revision/,
  );
  assert.match(decision, /revision: compiledRevision/);
  const workflowBranch = section(
    decision,
    "      if (workflowOwned) {",
    "      // Standalone proposals retain",
  );
  assert.doesNotMatch(
    workflowBranch,
    /ensureCaptions|ensureBroll|ensureMusic|compile-timeline/,
  );
  assert.match(
    decision,
    /planAppliedLocally\s*\?[^:]*timeline synchronization did not finish/s,
  );
});

test("partial approval rejects empty or full selections and exposes dependency constraints", async () => {
  const source = await readFile(
    new URL("components/editor/ai-step.tsx", root),
    "utf8",
  );
  const decision = section(
    source,
    "  async function applyProposalDecision(",
    "  async function ensureCaptions(",
  );

  assert.match(
    decision,
    /decision === "partial_approve"[\s\S]*acceptedActionIds\.length === 0[\s\S]*acceptedActionIds\.length === actions\.length/,
  );
  assert.match(
    decision,
    /All actions are selected\. Use Approve all instead\./,
  );
  assert.match(decision, /selectedProposalDependencyError/);
  assert.match(
    decision,
    /accepted_action_ids:[\s\S]*decision === "partial_approve" \? acceptedActionIds : \[\]/,
  );
  assert.match(source, /Select prerequisites first\./);
  assert.match(source, /Deselect those actions first\./);
  assert.match(
    source,
    /disabled=\{Boolean\(decisionLoading \|\| blockReason\)\}/,
  );
  assert.doesNotMatch(source, /dependency_ids[\s\S]{0,160}\.add\(/);
});

test("proposal card renders rationale, confidence, provenance, costs, warnings, and ordered action details", async () => {
  const source = await readFile(
    new URL("components/editor/ai-step.tsx", root),
    "utf8",
  );

  for (const requiredCopy of [
    "Review AI edit proposal",
    "overall_rationale",
    "Confidence",
    "Provenance",
    "Model revision",
    "Estimated cost",
    "Actual cost",
    "Review warnings",
    "Proposed actions in order",
    "action.rationale",
    "Action risks:",
    "Approve all",
    "Apply selected",
    "Reject",
  ]) {
    assert.ok(
      source.includes(requiredCopy),
      `missing proposal UI: ${requiredCopy}`,
    );
  }
  assert.match(source, /aria-labelledby="proposal-review-title"/);
  assert.match(source, /type="checkbox"/);
  assert.match(source, /left\.ordinal - right\.ordinal/);
  assert.match(source, /degradation_flags/);
  assert.match(source, /risk_flags/);
});

test("proposal decisions use deterministic cross-device keys and exact concurrency expectations", async () => {
  const source = await readFile(
    new URL("components/editor/ai-step.tsx", root),
    "utf8",
  );

  assert.match(source, /proposalDecisionIdempotencyKey\(\{/);
  assert.match(
    source,
    /workspaceId,[\s\S]*projectId,[\s\S]*proposalId: proposal\.id/,
  );
  assert.match(source, /proposalRevision: proposal\.revision/);
  assert.match(source, /actionIds: idempotencyActionIds/);
  assert.doesNotMatch(source, /crypto\.randomUUID|decisionIdempotencyKeysRef/);
  assert.match(source, /expected_proposal_revision: proposal\.revision/);
  assert.match(
    source,
    /expected_base_active_plan_id: proposal\.base_active_plan_id \?\? null/,
  );
  assert.match(
    source,
    /expected_base_active_plan_revision:[\s\S]*proposal\.base_active_plan_revision \?\? null/,
  );
  assert.match(
    source,
    /proposals\/\$\{encodeURIComponent\(proposal\.id\)\}\/decisions/,
  );
});

test("workflow proposal recovery uses the canonical artifact and never browser storage", async () => {
  const source = await readFile(
    new URL("components/editor/ai-step.tsx", root),
    "utf8",
  );

  assert.match(source, /proposalIdentityFromWorkflow\(workflow\)/);
  assert.match(source, /workflowProposalIdentity\?\.proposalId/);
  assert.match(source, /workflowProposalIdentity\?\.linkedRevision/);
  assert.match(source, /nextProposal\.id !== workflowProposalId/);
  assert.match(source, /nextProposal\.project_id !== projectId/);
  assert.match(source, /nextProposal\.workspace_id !== workspaceId/);
  assert.match(source, /isProposalRevisionCompatible\(/);
  assert.match(
    source,
    /workflowProposalLinkedRevision,[\s\S]*nextProposal\.revision/,
  );
  assert.match(source, /showProposalForReview\(nextProposal, true\)/);
  assert.doesNotMatch(
    source,
    /sessionStorage|localStorage|PendingProposalIdentity/,
  );
});

test("shared contracts and polling expose proposal identities without changing legacy success return", async () => {
  const [shared, client] = await Promise.all([
    readFile(new URL("../../packages/shared/src/index.ts", root), "utf8"),
    readFile(new URL("lib/client-api.ts", root), "utf8"),
  ]);

  for (const alias of [
    "ProposalAction",
    "ProposalCostUsage",
    "ProposalDecisionRequest",
    "ProposalDecisionResponse",
    "ProposalProvenance",
    "ProposalResponse",
  ]) {
    assert.match(shared, new RegExp(`export type ${alias}`));
  }
  assert.match(
    shared,
    /interface EditPlanRecordSummary[\s\S]*revision\?: number \| null/,
  );
  assert.match(client, /status: "awaiting_approval"/);
  assert.match(client, /proposalId: response\.proposal_id/);
  assert.match(client, /proposalRevision: response\.proposal_revision/);
  assert.match(client, /return returnIdentity[\s\S]*: response\.plan_id/);
});

test("user-facing copy promises review instead of direct timeline writes", async () => {
  const files = await Promise.all([
    readFile(new URL("components/editor/ai-step.tsx", root), "utf8"),
    readFile(new URL("components/editor-client.tsx", root), "utf8"),
    readFile(new URL("components/project-early-phase.tsx", root), "utf8"),
  ]);
  const copy = files.join("\n");

  assert.doesNotMatch(copy, /directly on(?:to)? the timeline/i);
  assert.doesNotMatch(copy, /without creating a separate review step/i);
  assert.match(copy, /Nothing changes on the timeline until you approve it/);
  assert.match(copy, /review\s+before\s+it\s+reaches\s+the\s+timeline/);
});
