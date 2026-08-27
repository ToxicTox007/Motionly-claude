import type {
  ProposalDecisionType,
  WorkflowCommand,
  WorkflowDetail,
  WorkflowRunState,
  WorkflowSummary,
} from "@motionly/shared";

export const FIRST_DRAFT_WORKFLOW_TYPE: "first_draft";
export const FIRST_DRAFT_WORKFLOW_VERSION: "first-draft-v2";
export const FIRST_DRAFT_STATE_ORDER: readonly [
  "uploaded",
  "analyzing",
  "proposal_ready",
  "awaiting_approval",
  "assembling",
  "draft_rendering",
  "draft_ready",
];

export function isCanonicalFirstDraftWorkflow(
  workflow: WorkflowSummary | WorkflowDetail | null | undefined,
  projectId: string,
): boolean;
export function selectLatestFirstDraftWorkflow<T extends WorkflowSummary>(
  workflows: readonly T[],
  projectId: string,
): T | null;
export function assertCanonicalWorkflowIdentity<T extends WorkflowDetail>(
  workflow: T,
  identity: { projectId: string; expectedWorkflowId?: string },
): T;
export function isCanonicalDraftReady(
  workflow: WorkflowDetail | null | undefined,
): boolean;
export function proposalIdentityFromWorkflow(
  workflow: WorkflowDetail | null | undefined,
): { proposalId: string; linkedRevision: number } | null;
export function isProposalRevisionCompatible(
  linkedRevision: number,
  currentRevision: number,
): boolean;
export function assertWorkflowCommandTransition<T extends WorkflowDetail>(
  current: WorkflowDetail,
  next: T,
  command: WorkflowCommand,
): T;
export function workflowStateIndex(state: WorkflowRunState): number;
export function workflowCommandIdempotencyKey(
  workflow: WorkflowSummary | WorkflowDetail,
  command: WorkflowCommand,
): string;
export function proposalDecisionIdempotencyKey(
  intent: {
    workspaceId: string;
    projectId: string;
    proposalId: string;
    proposalRevision: number;
    decision: ProposalDecisionType;
    actionIds: readonly string[];
  },
  cryptoImpl?: Crypto,
): Promise<string>;
