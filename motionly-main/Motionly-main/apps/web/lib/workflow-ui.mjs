export const FIRST_DRAFT_WORKFLOW_TYPE = "first_draft";
export const FIRST_DRAFT_WORKFLOW_VERSION = "first-draft-v2";

export const FIRST_DRAFT_STATE_ORDER = Object.freeze([
  "uploaded",
  "analyzing",
  "proposal_ready",
  "awaiting_approval",
  "assembling",
  "draft_rendering",
  "draft_ready",
]);

const DISCOVERABLE_FIRST_DRAFT_STATES = new Set([
  ...FIRST_DRAFT_STATE_ORDER,
  "failed",
  "cancelled",
]);

export function isCanonicalFirstDraftWorkflow(workflow, projectId) {
  return Boolean(
    workflow &&
    workflow.project_id === projectId &&
    workflow.workflow_type === FIRST_DRAFT_WORKFLOW_TYPE &&
    workflow.workflow_version === FIRST_DRAFT_WORKFLOW_VERSION &&
    DISCOVERABLE_FIRST_DRAFT_STATES.has(workflow.state),
  );
}

/** The workflows endpoint is canonical latest-first; preserve its API order. */
export function selectLatestFirstDraftWorkflow(workflows, projectId) {
  return (
    workflows.find((workflow) =>
      isCanonicalFirstDraftWorkflow(workflow, projectId),
    ) ?? null
  );
}

export function assertCanonicalWorkflowIdentity(
  workflow,
  { projectId, expectedWorkflowId } = {},
) {
  if (!isCanonicalFirstDraftWorkflow(workflow, projectId)) {
    throw new Error("Workflow response identity does not match this project.");
  }
  if (expectedWorkflowId && workflow.id !== expectedWorkflowId) {
    throw new Error(
      "Workflow detail identity does not match the selected run.",
    );
  }
  return workflow;
}

export function isCanonicalDraftReady(workflow) {
  return Boolean(
    workflow &&
    workflow.state === "draft_ready" &&
    workflow.draft_render?.id &&
    workflow.draft_export?.id &&
    workflow.draft_export.render_job_id === workflow.draft_render.id,
  );
}

export function proposalIdentityFromWorkflow(workflow) {
  if (
    !workflow ||
    !["proposal_ready", "awaiting_approval"].includes(workflow.state)
  ) {
    return null;
  }
  const artifact = workflow.artifacts?.find(
    (candidate) => candidate.type === "proposal",
  );
  if (
    !artifact?.id ||
    !Number.isInteger(artifact.revision) ||
    artifact.revision < 1
  ) {
    return null;
  }
  return { proposalId: artifact.id, linkedRevision: artifact.revision };
}

export function isProposalRevisionCompatible(linkedRevision, currentRevision) {
  return (
    Number.isInteger(linkedRevision) &&
    Number.isInteger(currentRevision) &&
    linkedRevision >= 1 &&
    currentRevision >= linkedRevision
  );
}

export function assertWorkflowCommandTransition(current, next, command) {
  assertCanonicalWorkflowIdentity(next, { projectId: current.project_id });
  if (
    next.uploaded_video_id !== current.uploaded_video_id ||
    next.source_media_revision !== current.source_media_revision ||
    next.root_workflow_run_id !== current.root_workflow_run_id
  ) {
    throw new Error("Workflow command response changed immutable lineage.");
  }
  if (command === "cancel") {
    if (next.id !== current.id || next.state !== "cancelled") {
      throw new Error("Workflow cancellation returned a conflicting run.");
    }
  } else if (command === "retry") {
    if (
      next.id === current.id ||
      next.retry_of_workflow_run_id !== current.id
    ) {
      throw new Error("Workflow retry returned invalid retry lineage.");
    }
  } else {
    throw new Error("Unsupported workflow command.");
  }
  return next;
}

export function workflowStateIndex(state) {
  return FIRST_DRAFT_STATE_ORDER.indexOf(state);
}

export function workflowCommandIdempotencyKey(workflow, command) {
  if (!workflow?.id || !Number.isInteger(workflow.revision)) {
    throw new Error("Workflow command identity is incomplete.");
  }
  if (command !== "cancel" && command !== "retry") {
    throw new Error("Unsupported workflow command.");
  }
  return `workflow-${command}:${workflow.id}:${workflow.revision}`;
}

function canonicalDecisionIntent({
  workspaceId,
  projectId,
  proposalId,
  proposalRevision,
  decision,
  actionIds,
}) {
  return [
    "proposal-decision-v1",
    workspaceId,
    projectId,
    proposalId,
    String(proposalRevision),
    decision,
    [...new Set(actionIds)].sort().join(","),
  ].join("\u001f");
}

export async function proposalDecisionIdempotencyKey(
  intent,
  cryptoImpl = globalThis.crypto,
) {
  if (!cryptoImpl?.subtle || typeof TextEncoder === "undefined") {
    throw new Error("Web Crypto is required for durable proposal decisions.");
  }
  const digest = await cryptoImpl.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonicalDecisionIntent(intent)),
  );
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return `proposal-decision:${hex}`;
}
