"use client";

import React, { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Input, Label, Select, TextArea, Slider } from "./ui";
import { Button } from "../button";
import {
  AlertCircle,
  CheckCircle2,
  Eye,
  History,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
} from "lucide-react";
import { env } from "@/lib/env";
import {
  editorApiRequest,
  pollPlanJob,
  resolveQueuedLayer,
  startAdaptivePolling,
} from "@/lib/client-api";
import type {
  BrollSlotSummary,
  BrollDensity,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EditPlanStyleKey,
  MusicSelectionSummary,
  PlanSnapshotSummary,
  ProposalAction,
  ProposalDecisionRequest,
  ProposalDecisionResponse,
  ProposalDecisionType,
  ProposalResponse,
  TranscriptSummary,
  VisualAnalysisStatusSummary,
  WorkflowDetail,
} from "@motionly/shared";
import {
  isProposalRevisionCompatible,
  proposalDecisionIdempotencyKey,
  proposalIdentityFromWorkflow,
} from "@/lib/workflow-ui.mjs";

interface AIStepProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialPlans: EditPlanRecordSummary[];
  activePlanId: string | null;
  transcript: TranscriptSummary | null;
  initialVisualStatus: VisualAnalysisStatusSummary;
  workflow: WorkflowDetail | null;
  onWorkflowRefresh: () => Promise<WorkflowDetail | null>;
  onTranscriptUpdated: (transcript: TranscriptSummary | null) => void;
  onPlanGenerated: (plan: EditPlanRecordSummary) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onBrollSlotsUpdated: (slots: BrollSlotSummary[]) => void;
  onMusicSelectionUpdated: (selection: MusicSelectionSummary) => void;
  onClipSelected: (startMs: number) => void;
  externalProposal?: InSessionProposalIdentity | null;
  onExternalProposalHandled?: () => void;
}

type StylePickerValue = EditPlanStyleKey | "auto";
type ClipGoal =
  | "viral_hook"
  | "educational_moment"
  | "storytelling"
  | "product_mention";
type ClipDuration = 15 | 30 | 60;
type RecommendedClip = {
  id: string;
  rank: number;
  duration_s: ClipDuration;
  start_ms: number;
  end_ms: number;
  score: number;
  headline: string;
  transcript_excerpt: string;
  rationale: string[];
  signals: {
    matched_keywords: string[];
    emotion: string | null;
    emotion_intensity: number | null;
    pace: string | null;
    visual_context: string[];
  };
};
type ClipRecommendationsResponse = {
  goal: ClipGoal;
  source_duration_ms: number;
  signal_availability: {
    transcript: boolean;
    audio_intelligence: boolean;
    visual_analysis: boolean;
  };
  groups: { duration_s: ClipDuration; clips: RecommendedClip[] }[];
};

const clipGoals: { value: ClipGoal; label: string }[] = [
  { value: "viral_hook", label: "Viral hook" },
  { value: "educational_moment", label: "Educational moment" },
  { value: "storytelling", label: "Storytelling" },
  { value: "product_mention", label: "Product mention" },
];
const clipDurations: ClipDuration[] = [15, 30, 60];

type InSessionProposalIdentity = {
  proposalId: string;
  proposalRevision: number | null;
  statusUrl: string | null;
};

const styles: { value: StylePickerValue; label: string }[] = [
  { value: "auto", label: "Auto (Audio + Visual)" },
  { value: "motivational", label: "Motivational" },
  { value: "emotional", label: "Emotional" },
  { value: "cinematic", label: "Cinematic" },
  { value: "educational", label: "Educational" },
  { value: "luxury", label: "Luxury (Warm & Premium)" },
  { value: "storytelling", label: "Storytelling" },
  { value: "dramatic", label: "Dramatic" },
  { value: "fast_paced", label: "Fast-Paced" },
  { value: "clean_minimal", label: "Clean Minimal" },
  { value: "creator_influencer", label: "Creator / Influencer" },
];

const captionLanguages = [
  { value: "original", label: "Original language" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "es", label: "Spanish" },
  { value: "pt", label: "Portuguese" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "ar", label: "Arabic" },
];

const sourceLanguages = [
  { value: "auto", label: "Auto detect" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "es", label: "Spanish" },
  { value: "pt", label: "Portuguese" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "ar", label: "Arabic" },
];

export function AIStep({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialPlans,
  activePlanId,
  transcript,
  initialVisualStatus,
  workflow,
  onWorkflowRefresh,
  onTranscriptUpdated,
  onPlanGenerated,
  onCaptionTrackUpdated,
  onBrollSlotsUpdated,
  onMusicSelectionUpdated,
  onClipSelected,
  externalProposal,
  onExternalProposalHandled,
}: AIStepProps) {
  const router = useRouter();
  const activePlan =
    initialPlans.find((p) => p.id === activePlanId) || initialPlans[0];

  const [style, setStyle] = useState<StylePickerValue>(
    activePlan?.style ?? "auto",
  );
  const [density, setDensity] = useState<BrollDensity>(
    activePlan?.broll_density ?? "medium",
  );
  const [prompt, setPrompt] = useState(activePlan?.prompt ?? "");
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [outputLanguage, setOutputLanguage] = useState("original");
  const [loading, setLoading] = useState(false);
  const [proposal, setProposal] = useState<ProposalResponse | null>(null);
  const [selectedActionIds, setSelectedActionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [decisionLoading, setDecisionLoading] =
    useState<ProposalDecisionType | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [snapshots, setSnapshots] = useState<PlanSnapshotSummary[]>([]);
  const [snapshotName, setSnapshotName] = useState("");
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const isGeneratingRef = useRef(false);
  const isDecidingRef = useRef(false);
  const workflowOwnedProposalIdRef = useRef<string | null>(null);
  const isTranscribingRef = useRef(false);
  const isSnapshotSavingRef = useRef(false);
  const isSnapshotRestoringRef = useRef(false);
  const operationAbortRef = useRef<AbortController | null>(null);
  const consumedExternalProposalRef = useRef<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [visualStatus, setVisualStatus] = useState(initialVisualStatus);
  const [clipGoal, setClipGoal] = useState<ClipGoal>("viral_hook");
  const [clipDuration, setClipDuration] = useState<ClipDuration>(30);
  const [clipRecommendations, setClipRecommendations] =
    useState<ClipRecommendationsResponse | null>(null);
  const [clipLoading, setClipLoading] = useState(false);
  const [clipMessage, setClipMessage] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const hasTranscript =
    transcript?.status === "ready" && Boolean(transcript.words?.length);

  useEffect(() => {
    return () => {
      operationAbortRef.current?.abort();
      operationAbortRef.current = null;
    };
  }, []);

  const densityMap: Record<BrollDensity, number> = {
    none: 0,
    low: 33,
    medium: 66,
    high: 100,
  };

  const reverseDensityMap = (val: number): BrollDensity => {
    if (val < 25) return "none";
    if (val < 50) return "low";
    if (val < 75) return "medium";
    return "high";
  };

  useEffect(() => {
    if (visualStatus.status !== "processing") return;
    let canceled = false;
    const abortController = new AbortController();
    const poll = async () => {
      try {
        const next = await editorApiRequest<VisualAnalysisStatusSummary>(
          `${env.apiBaseUrl}/projects/${projectId}/visual/status`,
          {
            method: "GET",
            workspaceId,
            userId,
            accessToken: apiAccessToken,
            signal: abortController.signal,
          },
        );
        if (!canceled) setVisualStatus(next);
      } catch (error) {
        if (!canceled) console.error(error);
      }
    };
    const stopPolling = startAdaptivePolling(() => poll(), {
      intervalMs: 5000,
      maxIntervalMs: 15000,
    });
    return () => {
      canceled = true;
      abortController.abort();
      stopPolling();
    };
  }, [apiAccessToken, projectId, userId, visualStatus.status, workspaceId]);

  useEffect(() => {
    if (!activePlanId) {
      return;
    }
    let canceled = false;
    const abortController = new AbortController();
    async function loadSnapshots() {
      try {
        const payload = await editorApiRequest<{
          snapshots: PlanSnapshotSummary[];
        }>(`${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`, {
          method: "GET",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Timeline versions could not be loaded.",
          signal: abortController.signal,
        });
        if (!canceled) setSnapshots(payload.snapshots ?? []);
      } catch (error) {
        if (!canceled) console.error(error);
      }
    }
    void loadSnapshots();
    return () => {
      canceled = true;
      abortController.abort();
    };
  }, [activePlanId, apiAccessToken, projectId, userId, workspaceId]);

  function safePlanStatusUrl(value: string) {
    const apiBaseUrl = new URL(env.apiBaseUrl, window.location.origin);
    const statusUrl = new URL(value, apiBaseUrl);
    const expectedProjectPath = `/projects/${encodeURIComponent(projectId)}/plans/jobs/`;
    if (
      statusUrl.origin !== apiBaseUrl.origin ||
      !statusUrl.pathname.includes(expectedProjectPath)
    ) {
      throw new Error(
        "The saved plan status identity is invalid for this project.",
      );
    }
    return statusUrl.toString();
  }

  function showProposalForReview(
    nextProposal: ProposalResponse,
    workflowOwned = false,
  ) {
    const actions = nextProposal.actions ?? [];
    workflowOwnedProposalIdRef.current = workflowOwned ? nextProposal.id : null;
    setProposal(nextProposal);
    setSelectedActionIds(new Set(actions.map((action) => action.id)));
    setMessage(
      workflowOwned
        ? "Workflow proposal ready. Review every action before durable assembly."
        : "AI edit proposal ready. Review every action before changing the timeline.",
    );
  }

  async function waitForProposalReview(
    proposalId: string,
    expectedRevision: number | null,
    signal: AbortSignal,
  ): Promise<ProposalResponse | null> {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      signal.throwIfAborted();
      const nextProposal = await editorApiRequest<ProposalResponse>(
        `${env.apiBaseUrl}/projects/${projectId}/proposals/${encodeURIComponent(proposalId)}`,
        {
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "The AI edit proposal could not be loaded.",
          signal,
        },
      );
      if (
        nextProposal.id !== proposalId ||
        nextProposal.project_id !== projectId ||
        nextProposal.workspace_id !== workspaceId
      ) {
        throw new Error(
          "Proposal response identity does not match this project.",
        );
      }
      if (
        nextProposal.state === "awaiting_approval" &&
        (expectedRevision == null || nextProposal.revision >= expectedRevision)
      ) {
        return nextProposal;
      }
      if (nextProposal.state === "failed") {
        throw new Error(
          nextProposal.failure_code
            ? `Proposal generation failed (${formatFlag(nextProposal.failure_code)}).`
            : "Proposal generation failed.",
        );
      }
      if (nextProposal.state === "rejected") {
        setProposal(null);
        setMessage("Proposal rejected. Your current timeline was not changed.");
        return null;
      }
      if (
        nextProposal.state === "approved" ||
        nextProposal.state === "partially_approved"
      ) {
        setProposal(null);
        setMessage(
          "This proposal was already approved. Refreshing the authoritative timeline.",
        );
        router.refresh();
        return null;
      }
      await waitForDelay(Math.min(3000, 1000 + attempt * 100), signal);
    }
    throw new Error("Proposal generation timed out.");
  }

  async function loadInSessionProposal(
    pending: InSessionProposalIdentity,
    signal: AbortSignal,
  ) {
    let proposalId = pending.proposalId;
    let proposalRevision = pending.proposalRevision;
    if (pending.statusUrl) {
      const result = await pollPlanJob(
        pending.statusUrl,
        {
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          signal,
        },
        (progress) => {
          if (!signal.aborted) setMessage(`Generating proposal: ${progress}%`);
        },
        true,
      );
      if (result.status !== "awaiting_approval") {
        throw new Error(
          "The server returned a plan without the required approval proposal.",
        );
      }
      proposalId = result.proposalId;
      proposalRevision = result.proposalRevision;
    }
    return waitForProposalReview(proposalId, proposalRevision, signal);
  }

  useEffect(() => {
    if (
      !externalProposal ||
      consumedExternalProposalRef.current === externalProposal.proposalId
    ) {
      return;
    }
    consumedExternalProposalRef.current = externalProposal.proposalId;
    const controller = beginPlanOperation();
    setLoading(true);
    setMessage("Loading the client-feedback AI proposal for review…");
    onExternalProposalHandled?.();
    void (async () => {
      try {
        const nextProposal = await loadInSessionProposal(
          {
            proposalId: externalProposal.proposalId,
            proposalRevision: externalProposal.proposalRevision,
            statusUrl: externalProposal.statusUrl
              ? safePlanStatusUrl(externalProposal.statusUrl)
              : null,
          },
          controller.signal,
        );
        if (nextProposal && operationAbortRef.current === controller) {
          showProposalForReview(nextProposal);
        }
      } catch (error) {
        if (
          operationAbortRef.current === controller &&
          !controller.signal.aborted
        ) {
          setMessage((error as Error).message);
        }
      } finally {
        finishPlanOperation(controller);
      }
    })();
    return () => controller.abort();
    // The external proposal is deliberately consumed once by ID; helper functions use current props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalProposal, onExternalProposalHandled]);

  async function generatePlan() {
    if (isGeneratingRef.current || loading || !hasTranscript || proposal)
      return;
    const operationController = beginPlanOperation();
    const { signal } = operationController;
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        plan: null;
        proposal_id: string;
        proposal_revision: number;
        status: string;
        job_id: string;
        status_url: string;
        proposal_url: string;
      }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError:
          "AI edit proposal could not be generated. Please try again.",
        signal,
        body: {
          prompt,
          ...(style === "auto" ? {} : { style }),
          broll_density: density,
          target_duration_s: 35,
        },
      });

      if (!payload.proposal_id || payload.proposal_revision == null) {
        throw new Error("Proposal generation returned no approval identity.");
      }
      const pending: InSessionProposalIdentity = {
        proposalId: payload.proposal_id,
        proposalRevision: payload.proposal_revision,
        statusUrl: payload.status_url
          ? safePlanStatusUrl(payload.status_url)
          : null,
      };
      setMessage("Generating an AI edit proposal for your review...");
      const nextProposal = await loadInSessionProposal(pending, signal);
      signal.throwIfAborted();
      if (nextProposal && operationAbortRef.current === operationController) {
        showProposalForReview(nextProposal);
      }
    } catch (error) {
      if (operationAbortRef.current !== operationController || signal.aborted)
        return;
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      finishPlanOperation(operationController);
    }
  }

  function beginPlanOperation() {
    isGeneratingRef.current = true;
    operationAbortRef.current?.abort();
    const controller = new AbortController();
    operationAbortRef.current = controller;
    return controller;
  }

  function finishPlanOperation(controller: AbortController) {
    if (operationAbortRef.current !== controller) return;
    operationAbortRef.current = null;
    isGeneratingRef.current = false;
    setLoading(false);
  }

  const workflowProposalIdentity = proposalIdentityFromWorkflow(workflow);
  const workflowProposalId = workflowProposalIdentity?.proposalId ?? null;
  const workflowProposalLinkedRevision =
    workflowProposalIdentity?.linkedRevision ?? null;

  useEffect(() => {
    if (!workflowProposalId || workflowProposalLinkedRevision == null) {
      const clearTimer = window.setTimeout(() => {
        if (
          workflowOwnedProposalIdRef.current &&
          workflow?.state !== "proposal_ready" &&
          workflow?.state !== "awaiting_approval"
        ) {
          workflowOwnedProposalIdRef.current = null;
          setProposal(null);
          setSelectedActionIds(new Set());
        }
      }, 0);
      return () => window.clearTimeout(clearTimer);
    }
    const controller = new AbortController();
    void editorApiRequest<ProposalResponse>(
      `${env.apiBaseUrl}/projects/${projectId}/proposals/${encodeURIComponent(workflowProposalId)}`,
      {
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "The workflow proposal could not be loaded.",
        signal: controller.signal,
      },
    )
      .then((nextProposal) => {
        if (
          nextProposal.id !== workflowProposalId ||
          nextProposal.project_id !== projectId ||
          nextProposal.workspace_id !== workspaceId ||
          !isProposalRevisionCompatible(
            workflowProposalLinkedRevision,
            nextProposal.revision,
          )
        ) {
          throw new Error(
            "Workflow proposal identity or revision lineage does not match the canonical artifact.",
          );
        }
        if (nextProposal.state === "awaiting_approval") {
          showProposalForReview(nextProposal, true);
        } else if (nextProposal.state === "failed") {
          throw new Error(
            nextProposal.failure_code
              ? `Workflow proposal failed (${formatFlag(nextProposal.failure_code)}).`
              : "Workflow proposal failed.",
          );
        } else {
          setMessage("The workflow proposal is still being prepared.");
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMessage((error as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // Proposal recovery is keyed only by the canonical artifact identity.
  }, [
    apiAccessToken,
    projectId,
    userId,
    workflow?.state,
    workflowProposalId,
    workflowProposalLinkedRevision,
    workspaceId,
  ]);

  function toggleProposalAction(action: ProposalAction) {
    if (!proposal || decisionLoading) return;
    const actions = proposal.actions ?? [];
    const blockReason = actionSelectionBlockReason(
      action,
      actions,
      selectedActionIds,
    );
    if (blockReason) {
      setMessage(blockReason);
      return;
    }
    setSelectedActionIds((current) => {
      const next = new Set(current);
      if (next.has(action.id)) next.delete(action.id);
      else next.add(action.id);
      return next;
    });
  }

  async function applyProposalDecision(decision: ProposalDecisionType) {
    if (!proposal || isDecidingRef.current || decisionLoading) return;
    const actions = [...(proposal.actions ?? [])].sort(
      (left, right) => left.ordinal - right.ordinal,
    );
    const acceptedActionIds = actions
      .filter((action) => selectedActionIds.has(action.id))
      .map((action) => action.id);
    if (
      decision === "partial_approve" &&
      (acceptedActionIds.length === 0 ||
        acceptedActionIds.length === actions.length)
    ) {
      setMessage(
        acceptedActionIds.length === 0
          ? "Select at least one action before applying a partial proposal."
          : "All actions are selected. Use Approve all instead.",
      );
      return;
    }
    const invalidSelection = selectedProposalDependencyError(
      actions,
      new Set(acceptedActionIds),
    );
    if (decision === "partial_approve" && invalidSelection) {
      setMessage(invalidSelection);
      return;
    }

    const workflowOwned =
      workflowOwnedProposalIdRef.current === proposal.id &&
      workflowProposalId === proposal.id &&
      workflow?.state === "awaiting_approval";
    const idempotencyActionIds =
      decision === "reject"
        ? []
        : decision === "partial_approve"
          ? acceptedActionIds
          : actions.map((action) => action.id);

    isDecidingRef.current = true;
    setDecisionLoading(decision);
    setMessage(null);
    const operationController = beginPlanOperation();
    const { signal } = operationController;
    let planAppliedLocally = false;
    try {
      const idempotencyKey = await proposalDecisionIdempotencyKey({
        workspaceId,
        projectId,
        proposalId: proposal.id,
        proposalRevision: proposal.revision,
        decision,
        actionIds: idempotencyActionIds,
      });
      signal.throwIfAborted();
      const decisionPayload: ProposalDecisionRequest = {
        decision,
        expected_proposal_revision: proposal.revision,
        expected_base_active_plan_id: proposal.base_active_plan_id ?? null,
        expected_base_active_plan_revision:
          proposal.base_active_plan_revision ?? null,
        idempotency_key: idempotencyKey,
        accepted_action_ids:
          decision === "partial_approve" ? acceptedActionIds : [],
      };
      const response = await editorApiRequest<ProposalDecisionResponse>(
        `${env.apiBaseUrl}/projects/${projectId}/proposals/${encodeURIComponent(proposal.id)}/decisions`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "The proposal decision could not be applied.",
          signal,
          body: { ...decisionPayload },
        },
      );
      signal.throwIfAborted();

      if (decision === "reject") {
        workflowOwnedProposalIdRef.current = null;
        setProposal(null);
        setSelectedActionIds(new Set());
        setMessage("Proposal rejected. Your current timeline was not changed.");
        if (workflowOwned) {
          await onWorkflowRefresh();
          router.refresh();
        }
        return;
      }
      if (!response.edit_plan_id || response.edit_plan_revision == null) {
        throw new Error(
          "The proposal was approved, but the authoritative plan identity is missing.",
        );
      }

      const planList = await editorApiRequest<{
        plans: EditPlanRecordSummary[];
      }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "The approved timeline could not be confirmed.",
        signal,
      });
      const approvedPlan = planList.plans.find(
        (item) => item.id === response.edit_plan_id,
      );
      if (!approvedPlan) {
        throw new Error(
          "The proposal was approved, but its authoritative plan could not be loaded.",
        );
      }
      if (approvedPlan.revision == null) {
        throw new Error("The approved plan did not include a revision.");
      }
      if (approvedPlan.revision !== response.edit_plan_revision) {
        throw new Error(
          "The approved plan revision changed before it could be synchronized.",
        );
      }

      onPlanGenerated(approvedPlan);
      planAppliedLocally = true;
      if (workflowOwned) {
        workflowOwnedProposalIdRef.current = null;
        setProposal(null);
        setSelectedActionIds(new Set());
        setMessage(
          `Proposal approved. Workflow assembly is continuing from plan revision ${approvedPlan.revision}.`,
        );
        await onWorkflowRefresh();
        router.refresh();
        return;
      }

      // Standalone proposals retain the legacy client-owned downstream flow.
      const followUpResults = await Promise.allSettled([
        ensureCaptions(signal),
        ensureBroll(signal),
        ensureMusic(signal),
      ]);
      signal.throwIfAborted();
      const compiled = await editorApiRequest<{
        plan: EditPlanRecordSummary;
        revision?: number | null;
      }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/${approvedPlan.id}/compile-timeline?expected_revision=${approvedPlan.revision}`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError:
            "The proposal was approved, but its media layers could not be synchronized.",
          signal,
          body: {},
        },
      );
      signal.throwIfAborted();
      const compiledRevision = compiled.revision ?? compiled.plan.revision;
      if (compiledRevision == null) {
        throw new Error(
          "The compiled timeline did not return its new revision.",
        );
      }
      if (operationAbortRef.current !== operationController) return;
      const compiledPlan = {
        ...compiled.plan,
        revision: compiledRevision,
      };
      onPlanGenerated(compiledPlan);
      const failedFollowUps = followUpResults.filter(
        (result) => result.status === "rejected",
      );
      workflowOwnedProposalIdRef.current = null;
      setProposal(null);
      setSelectedActionIds(new Set());
      setMessage(
        failedFollowUps.length === 0
          ? `Proposal approved. AI edit v${compiledPlan.version} is ready at revision ${compiledRevision}.`
          : `Proposal approved. AI edit v${compiledPlan.version} is ready, but some media steps still need attention.`,
      );
      router.refresh();
    } catch (error) {
      if (operationAbortRef.current !== operationController || signal.aborted)
        return;
      console.error(error);
      setMessage(
        planAppliedLocally
          ? `The proposal was approved, but timeline synchronization did not finish: ${(error as Error).message}`
          : (error as Error).message,
      );
    } finally {
      isDecidingRef.current = false;
      setDecisionLoading(null);
      finishPlanOperation(operationController);
    }
  }

  async function ensureCaptions(signal: AbortSignal) {
    const payload = await editorApiRequest<{
      track?: CaptionTrackSummary | null;
      status: "ready" | "queued";
      status_url?: string | null;
    }>(`${env.apiBaseUrl}/projects/${projectId}/captions/generate`, {
      method: "POST",
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      friendlyError: "Captions could not be prepared yet.",
      signal,
      body: {
        transcript_id: transcript?.id ?? null,
        preset: defaultCaptionPreset(style),
        hide_filler_words: false,
        output_language: outputLanguage,
      },
    });
    const track = await resolveQueuedLayer({
      submission: payload,
      immediate: payload.track,
      apiBaseUrl: env.apiBaseUrl,
      options: { workspaceId, userId, accessToken: apiAccessToken, signal },
      load: async () => {
        const result = await editorApiRequest<{
          tracks: CaptionTrackSummary[];
        }>(`${env.apiBaseUrl}/projects/${projectId}/captions`, {
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          signal,
        });
        return result.tracks[0] ?? null;
      },
      resourceLabel: "Captions",
      onProgress: (progress) => setMessage(`Preparing captions: ${progress}%`),
    });
    onCaptionTrackUpdated(track);
  }

  async function ensureBroll(signal: AbortSignal) {
    const payload = await editorApiRequest<{
      slots?: BrollSlotSummary[] | null;
      status: "ready" | "queued";
      status_url?: string | null;
    }>(`${env.apiBaseUrl}/projects/${projectId}/broll/generate`, {
      method: "POST",
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      friendlyError: "B-roll could not be generated for this edit.",
      signal,
      body: {
        density,
      },
    });
    const slots = await resolveQueuedLayer({
      submission: payload,
      immediate: payload.slots,
      apiBaseUrl: env.apiBaseUrl,
      options: { workspaceId, userId, accessToken: apiAccessToken, signal },
      load: async () => {
        const result = await editorApiRequest<{ slots: BrollSlotSummary[] }>(
          `${env.apiBaseUrl}/projects/${projectId}/broll`,
          { workspaceId, userId, accessToken: apiAccessToken, signal },
        );
        return result.slots;
      },
      resourceLabel: "B-roll",
      onProgress: (progress) => setMessage(`Preparing B-roll: ${progress}%`),
    });
    onBrollSlotsUpdated(slots);
  }

  async function ensureMusic(signal: AbortSignal) {
    const payload = await editorApiRequest<{
      selection?: MusicSelectionSummary | null;
      status: "ready" | "queued";
      status_url?: string | null;
    }>(`${env.apiBaseUrl}/projects/${projectId}/music/select`, {
      method: "POST",
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      friendlyError:
        "No production music track is available for this edit yet.",
      signal,
      body: {
        include_music: true,
        music_volume: 0.25,
        sfx_intensity: 0.2,
        rights_decision: false,
        force: false,
      },
    });
    const selection = await resolveQueuedLayer({
      submission: payload,
      immediate: payload.selection,
      apiBaseUrl: env.apiBaseUrl,
      options: { workspaceId, userId, accessToken: apiAccessToken, signal },
      load: () =>
        editorApiRequest<MusicSelectionSummary | null>(
          `${env.apiBaseUrl}/projects/${projectId}/music/selection`,
          { workspaceId, userId, accessToken: apiAccessToken, signal },
        ),
      resourceLabel: "Music selection",
      onProgress: (progress) => setMessage(`Preparing music: ${progress}%`),
    });
    onMusicSelectionUpdated(selection);
  }

  async function findClipRecommendations() {
    if (clipLoading || !hasTranscript) return;
    setClipLoading(true);
    setClipMessage(null);
    try {
      const payload = await editorApiRequest<ClipRecommendationsResponse>(
        `${env.apiBaseUrl}/projects/${projectId}/clips/recommendations?goal=${encodeURIComponent(clipGoal)}`,
        {
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Best moments could not be found. Please try again.",
        },
      );
      setClipRecommendations(payload);
      if (!payload.groups.some((group) => group.duration_s === clipDuration)) {
        setClipDuration(payload.groups[0]?.duration_s ?? 15);
      }
      const total = payload.groups.reduce(
        (count, group) => count + group.clips.length,
        0,
      );
      setClipMessage(
        total
          ? `Found ${total} ranked moment${total === 1 ? "" : "s"}.`
          : "No self-contained moments were found for this goal.",
      );
    } catch (error) {
      console.error(error);
      setClipMessage((error as Error).message);
    } finally {
      setClipLoading(false);
    }
  }

  function applyRecommendedClip(clip: RecommendedClip) {
    const timeRange = `${formatClipTimestamp(clip.start_ms)}–${formatClipTimestamp(clip.end_ms)}`;
    const focusInstruction = `Focus the edit on ${timeRange} (${clip.duration_s}s): "${clip.transcript_excerpt}"`;
    setPrompt((current) =>
      current.includes(focusInstruction)
        ? current
        : [current.trim(), focusInstruction].filter(Boolean).join("\n"),
    );
    setSelectedClipId(clip.id);
    onClipSelected(clip.start_ms);
    setClipMessage(
      "Moment added to your AI edit instructions and preview cue.",
    );
  }

  async function requestTranscript(force = false) {
    if (isTranscribingRef.current || transcriptLoading) return;
    isTranscribingRef.current = true;
    setTranscriptLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        transcript_id: string;
        status: TranscriptSummary["status"];
        message?: string;
      }>(`${env.apiBaseUrl}/projects/${projectId}/transcriptions`, {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "Transcription could not be started. Please try again.",
        body: {
          force,
          language_hint: sourceLanguage,
        },
      });
      onTranscriptUpdated({
        id: String(payload.transcript_id),
        status: payload.status,
        language: "unknown",
        provider: "pending",
        model: "pending",
        progress_pct: payload.status === "queued" ? 15 : 55,
        words: [],
      });
      setMessage(payload.message ?? "Transcription queued.");
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      isTranscribingRef.current = false;
      setTranscriptLoading(false);
    }
  }

  async function saveSnapshot() {
    if (!activePlan) {
      setMessage("Generate an AI edit before saving a timeline version.");
      return;
    }
    if (isSnapshotSavingRef.current || snapshotLoading) return;
    isSnapshotSavingRef.current = true;
    setSnapshotLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ snapshot: PlanSnapshotSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Timeline version could not be saved.",
          body: {
            plan_id: activePlan.id,
            name: snapshotName.trim() || `Timeline v${activePlan.version}`,
            notes: `Saved from editor with ${activePlan.summary.cut_count} cuts.`,
          },
        },
      );
      setSnapshots((current) => [
        payload.snapshot,
        ...current.filter((snapshot) => snapshot.id !== payload.snapshot.id),
      ]);
      setSnapshotName("");
      setMessage(`Snapshot saved: ${payload.snapshot.name}`);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      isSnapshotSavingRef.current = false;
      setSnapshotLoading(false);
    }
  }

  async function restoreSnapshot(snapshotId: string) {
    if (isSnapshotRestoringRef.current || snapshotLoading) return;
    isSnapshotRestoringRef.current = true;
    setSnapshotLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots/${snapshotId}/restore`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Timeline version could not be restored.",
          body: {},
        },
      );
      onPlanGenerated(payload.plan);
      setMessage(`Restored timeline version ${payload.plan.version}.`);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      isSnapshotRestoringRef.current = false;
      setSnapshotLoading(false);
    }
  }

  const transcriptStatus = transcript?.status ?? "queued";
  const transcriptProgress = transcript?.progress_pct ?? 5;
  const transcriptMessage =
    transcript?.status === "failed"
      ? transcript.error_message || "Transcription failed."
      : transcript?.status === "captions_unavailable"
        ? transcript.error_message ||
          "Captions are unavailable for this upload."
        : transcript?.status === "processing"
          ? "Transcribing audio and preparing timed words."
          : transcript?.status === "queued"
            ? "Queued for background transcription."
            : !transcript
              ? "Starting background transcription."
              : "Transcript ready.";

  const orderedProposalActions = [...(proposal?.actions ?? [])].sort(
    (left, right) => left.ordinal - right.ordinal,
  );
  const selectedProposalActions = orderedProposalActions.filter((action) =>
    selectedActionIds.has(action.id),
  );
  const proposalWarnings = [
    ...(proposal?.degradation_flags ?? []).map(
      (flag) => `Degraded: ${formatFlag(flag)}`,
    ),
    ...(proposal?.risk_flags ?? []).map((flag) => `Risk: ${formatFlag(flag)}`),
  ];
  const partialSelectionError = proposal
    ? selectedProposalActions.length === 0
      ? "Select at least one action."
      : selectedProposalActions.length === orderedProposalActions.length
        ? "All actions are selected; use Approve all."
        : selectedProposalDependencyError(
            orderedProposalActions,
            selectedActionIds,
          )
    : null;
  const isErrorMessage =
    message && /failed|could not|try|invalid|missing/i.test(message);

  return (
    <div className="space-y-6" aria-live="polite">
      <div className="space-y-2">
        <h2 className="text-xl font-black tracking-tight">AI Edit</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          AI analyzes the video and prepares an edit proposal for your review.
          Nothing changes on the timeline until you approve it.
        </p>
      </div>

      {/* Visual analysis status */}
      <div
        className="card rounded-xl flex items-start gap-3 p-4 text-sm"
        data-testid="visual-analysis-status"
      >
        {visualStatus.status === "processing" ? (
          <Loader2
            className="mt-0.5 size-4 animate-spin text-primary"
            strokeWidth={1.5}
          />
        ) : visualStatus.status === "ready" ? (
          <CheckCircle2
            className="mt-0.5 size-4 text-success"
            strokeWidth={1.5}
          />
        ) : (
          <AlertCircle
            className="mt-0.5 size-4 text-warning"
            strokeWidth={1.5}
          />
        )}
        <div>
          <div className="flex items-center gap-2 font-bold">
            <Eye className="size-4" strokeWidth={1.5} />
            Visual analysis: {visualStatus.status}
          </div>
          <p className="mt-1 text-muted-foreground">{visualStatus.message}</p>
        </div>
      </div>

      {/* Best moments */}
      <section
        className="card rounded-xl space-y-4 p-5"
        aria-labelledby="best-moments-title"
        data-testid="best-moments"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 id="best-moments-title" className="text-base font-black">
              Best moments
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Rank 15, 30, and 60-second highlights from transcript, delivery,
              silence, keywords, and ready visual analysis.
            </p>
          </div>
          <Button
            onClick={() => void findClipRecommendations()}
            disabled={!hasTranscript || clipLoading}
            aria-busy={clipLoading}
            className="min-h-10 px-3"
          >
            {clipLoading ? (
              <Loader2 className="mr-2 size-4 animate-spin" strokeWidth={2} />
            ) : (
              <Sparkles className="mr-2 size-4" strokeWidth={2} />
            )}
            Find moments
          </Button>
        </div>

        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <div className="space-y-2">
            <Label>Clip goal</Label>
            <Select
              label="Clip goal"
              value={clipGoal}
              onChange={(value) => {
                setClipGoal(value as ClipGoal);
                setClipRecommendations(null);
                setSelectedClipId(null);
                setClipMessage(null);
              }}
              options={clipGoals}
            />
          </div>
          <div
            className="flex rounded-lg border border-foreground p-1"
            role="tablist"
          >
            {clipDurations.map((duration) => {
              const available = Boolean(
                clipRecommendations?.groups.some(
                  (group) =>
                    group.duration_s === duration && group.clips.length,
                ),
              );
              return (
                <button
                  key={duration}
                  type="button"
                  role="tab"
                  aria-selected={clipDuration === duration}
                  disabled={clipRecommendations !== null && !available}
                  onClick={() => setClipDuration(duration)}
                  className={`rounded px-2 py-1 text-xs font-black transition-colors ${
                    clipDuration === duration
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
                  }`}
                >
                  {duration}s
                </button>
              );
            })}
          </div>
        </div>

        {clipMessage ? (
          <p
            className="rounded border border-foreground/30 bg-muted/40 px-3 py-2 text-xs"
            role="status"
          >
            {clipMessage}
          </p>
        ) : null}

        {clipRecommendations ? (
          <div className="space-y-3">
            <p className="text-[11px] font-medium text-muted-foreground">
              Signals used: transcript
              {clipRecommendations.signal_availability.audio_intelligence
                ? " + audio intelligence"
                : ""}
              {clipRecommendations.signal_availability.visual_analysis
                ? " + visual analysis"
                : ""}
              {!clipRecommendations.signal_availability.visual_analysis
                ? ". Visual analysis will improve future recommendations when ready."
                : "."}
            </p>
            {clipRecommendations.groups
              .find((group) => group.duration_s === clipDuration)
              ?.clips.map((clip) => (
                <article
                  key={clip.id}
                  className={`rounded-lg border p-3 ${
                    selectedClipId === clip.id
                      ? "border-primary bg-primary/5"
                      : "border-foreground/30 bg-surface"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-black">
                        #{clip.rank} · {formatClipTimestamp(clip.start_ms)}–
                        {formatClipTimestamp(clip.end_ms)}
                        <span className="ml-2 text-primary">
                          {clip.score}/100
                        </span>
                      </p>
                      <p className="mt-1 truncate text-sm font-bold">
                        {clip.headline}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => onClipSelected(clip.start_ms)}
                      className="shrink-0 rounded border border-foreground px-2 py-1 text-[11px] font-bold hover:bg-muted"
                    >
                      Preview
                    </button>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                    {clip.transcript_excerpt}
                  </p>
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    {clip.rationale[0]}
                  </p>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <span className="text-[10px] font-bold uppercase text-muted-foreground">
                      {clip.signals.matched_keywords.slice(0, 3).join(" · ") ||
                        "Transcript continuity"}
                    </span>
                    <button
                      type="button"
                      onClick={() => applyRecommendedClip(clip)}
                      className="rounded bg-foreground px-2 py-1 text-[11px] font-bold text-background hover:opacity-85"
                    >
                      Use in AI edit
                    </button>
                  </div>
                </article>
              ))}
          </div>
        ) : null}
      </section>

      {/* Settings card */}
      <div className="card rounded-xl p-5 space-y-4">
        <div className="space-y-2">
          <Label>Edit Style</Label>
          <Select
            label="Edit Style"
            value={style}
            onChange={(val) => setStyle(val as StylePickerValue)}
            options={styles}
          />
        </div>

        <div className="space-y-2">
          <Label>Source Language</Label>
          <Select
            label="Source Language"
            value={sourceLanguage}
            onChange={setSourceLanguage}
            options={sourceLanguages}
          />
        </div>

        <div className="space-y-2">
          <Label>Caption Output Language</Label>
          <Select
            label="Caption Output Language"
            value={outputLanguage}
            onChange={setOutputLanguage}
            options={captionLanguages}
          />
        </div>

        <div className="space-y-2">
          <Slider
            label="B-Roll Intensity"
            value={densityMap[density]}
            onChange={(val) => setDensity(reverseDensityMap(val))}
          />
          <p className="text-[10px] text-muted-foreground text-right italic font-medium">
            {density === "none"
              ? "No extra media"
              : density === "low"
                ? "Minimal inserts"
                : density === "medium"
                  ? "Balanced storytelling"
                  : "High-energy visual flow"}
          </p>
        </div>

        <div className="space-y-2">
          <Label>Custom Instructions (Optional)</Label>
          <TextArea
            aria-label="Custom AI edit instructions"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. Focus on the product demo, keep the tone humorous..."
            className="min-h-36 font-mono text-[13px]"
          />
        </div>
      </div>

      {/* Transcript status */}
      {!hasTranscript ? (
        <div
          className="card rounded-lg space-y-3 p-4 border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
          role={transcript?.status === "failed" ? "alert" : "status"}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-bold capitalize">
                {transcriptStatus.replace(/_/g, " ")}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {transcriptMessage}
              </p>
            </div>
            {transcript?.status === "failed" ? (
              <AlertCircle
                className="mt-0.5 size-4 text-danger"
                strokeWidth={2}
              />
            ) : (
              <Loader2
                className={
                  transcript?.status === "ready"
                    ? "size-4 text-primary"
                    : "size-4 animate-spin text-primary"
                }
                strokeWidth={2}
              />
            )}
          </div>

          {/* Progress bar */}
          <div
            className="progress-bar h-3 border-2 border-foreground bg-muted rounded-full overflow-hidden"
            role="progressbar"
            aria-label="Transcription progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.max(5, Math.min(transcriptProgress, 100))}
          >
            <div
              className="progress-fill"
              style={{
                width: `${Math.max(5, Math.min(transcriptProgress, 100))}%`,
              }}
            />
          </div>

          <div className="flex items-center justify-between text-[11px] font-bold font-mono text-muted-foreground">
            <span>
              {Math.max(5, Math.min(transcriptProgress, 100))}% complete
            </span>
            <div className="flex items-center gap-2">
              {!transcript ? (
                <Button
                  onClick={() => requestTranscript(false)}
                  disabled={transcriptLoading}
                  aria-busy={transcriptLoading}
                  className="min-h-11 px-3"
                >
                  {transcriptLoading ? (
                    <Loader2
                      className="mr-2 size-4 animate-spin"
                      strokeWidth={2}
                    />
                  ) : null}
                  Start
                </Button>
              ) : null}
              {transcript?.status === "failed" ||
              transcript?.status === "captions_unavailable" ? (
                <Button
                  onClick={() => requestTranscript(true)}
                  disabled={transcriptLoading}
                  className="h-8 px-3"
                >
                  {transcriptLoading ? (
                    <Loader2
                      className="mr-2 size-4 animate-spin"
                      strokeWidth={2}
                    />
                  ) : (
                    <RefreshCw className="mr-2 size-4" strokeWidth={2} />
                  )}
                  Retry
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {/* Proposal review */}
      {proposal ? (
        <section
          className="card rounded-xl space-y-4 border-2 border-foreground p-5 shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff]"
          aria-labelledby="proposal-review-title"
          data-testid="proposal-review-card"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 id="proposal-review-title" className="text-base font-black">
                Review AI edit proposal
              </h3>
              <p className="mt-1 text-xs font-bold uppercase text-primary">
                Awaiting your approval
              </p>
            </div>
            <span className="rounded border border-foreground px-2 py-1 text-[10px] font-black uppercase">
              Revision {proposal.revision}
            </span>
          </div>

          <p className="text-sm leading-relaxed text-foreground">
            {proposal.overall_rationale ||
              "Review the proposed timeline actions before applying them."}
          </p>

          <dl className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <dt className="font-bold text-muted-foreground">Confidence</dt>
              <dd className="font-mono font-black">
                {formatConfidence(proposal.confidence)}
              </dd>
            </div>
            <div>
              <dt className="font-bold text-muted-foreground">Provenance</dt>
              <dd className="font-mono font-black">
                {[proposal.provenance.provider, proposal.provenance.model]
                  .filter(Boolean)
                  .join(" / ") || "Not reported"}
              </dd>
            </div>
            <div>
              <dt className="font-bold text-muted-foreground">
                Model revision
              </dt>
              <dd className="break-all font-mono font-black">
                {proposal.provenance.model_revision ||
                  proposal.provenance.prompt_version}
              </dd>
            </div>
            <div>
              <dt className="font-bold text-muted-foreground">
                Estimated cost
              </dt>
              <dd className="font-mono font-black">
                {formatUsd(proposal.cost?.estimated_cost_usd)}
              </dd>
            </div>
            <div>
              <dt className="font-bold text-muted-foreground">Actual cost</dt>
              <dd className="font-mono font-black">
                {formatUsd(proposal.cost?.actual_cost_usd)}
              </dd>
            </div>
          </dl>

          {proposalWarnings.length ? (
            <div
              className="rounded border-2 border-warning bg-warning/10 p-3"
              aria-label="Proposal warnings"
            >
              <p className="text-xs font-black uppercase text-warning">
                Review warnings
              </p>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-xs">
                {proposalWarnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <fieldset className="space-y-2">
            <legend className="text-xs font-black uppercase">
              Proposed actions in order
            </legend>
            {orderedProposalActions.map((action, index) => {
              const rationaleId = `proposal-action-${action.id}-rationale`;
              const dependencyId = `proposal-action-${action.id}-dependency`;
              const blockReason = actionSelectionBlockReason(
                action,
                orderedProposalActions,
                selectedActionIds,
              );
              return (
                <label
                  key={action.id}
                  className="flex items-start gap-3 rounded border-2 border-foreground bg-surface p-3"
                >
                  <input
                    type="checkbox"
                    className="mt-1 size-4 accent-primary"
                    checked={selectedActionIds.has(action.id)}
                    disabled={Boolean(decisionLoading || blockReason)}
                    onChange={() => toggleProposalAction(action)}
                    aria-describedby={`${rationaleId}${blockReason ? ` ${dependencyId}` : ""}`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-black">
                      {index + 1}. {formatProposalTool(action.tool_name)}
                    </span>
                    <span
                      id={rationaleId}
                      className="mt-1 block text-xs leading-relaxed text-muted-foreground"
                    >
                      {action.rationale}
                    </span>
                    <span className="mt-1 block text-[10px] font-bold uppercase text-muted-foreground">
                      Confidence {formatConfidence(action.confidence)}
                    </span>
                    {(action.risk_flags ?? []).length ? (
                      <span className="mt-1 block text-[10px] font-bold text-warning">
                        Action risks:{" "}
                        {(action.risk_flags ?? []).map(formatFlag).join(", ")}
                      </span>
                    ) : null}
                    {blockReason ? (
                      <span
                        id={dependencyId}
                        className="mt-1 block text-[10px] font-bold text-warning"
                      >
                        {blockReason}
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </fieldset>

          <div className="space-y-1 text-xs" aria-live="polite">
            <p className="font-bold">
              {selectedProposalActions.length} of{" "}
              {orderedProposalActions.length} actions selected
            </p>
            {partialSelectionError ? (
              <p className="text-muted-foreground">{partialSelectionError}</p>
            ) : null}
          </div>

          <div className="grid gap-2 sm:grid-cols-3">
            <Button
              type="button"
              onClick={() => applyProposalDecision("approve")}
              disabled={Boolean(
                decisionLoading || !orderedProposalActions.length,
              )}
              aria-busy={decisionLoading === "approve"}
            >
              {decisionLoading === "approve" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : null}
              Approve all
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => applyProposalDecision("partial_approve")}
              disabled={Boolean(decisionLoading || partialSelectionError)}
              aria-busy={decisionLoading === "partial_approve"}
            >
              {decisionLoading === "partial_approve" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : null}
              Apply selected
            </Button>
            <Button
              type="button"
              variant="danger"
              onClick={() => applyProposalDecision("reject")}
              disabled={Boolean(decisionLoading)}
              aria-busy={decisionLoading === "reject"}
            >
              {decisionLoading === "reject" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : null}
              Reject
            </Button>
          </div>
        </section>
      ) : null}

      {/* Generate button */}
      <button
        onClick={generatePlan}
        disabled={
          loading ||
          !hasTranscript ||
          Boolean(proposal) ||
          Boolean(decisionLoading)
        }
        aria-busy={loading}
        aria-describedby={message ? "ai-edit-message" : undefined}
        className="w-full h-12 rounded-md bg-primary text-primary-foreground font-black text-sm border-2 border-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? (
          <Loader2
            className="size-5 animate-spin text-primary-foreground"
            strokeWidth={2}
          />
        ) : (
          <Sparkles className="size-5" strokeWidth={2} />
        )}
        <span>
          {loading
            ? "Generating proposal…"
            : proposal
              ? "Proposal ready for review"
              : "Generate AI Edit Proposal"}
        </span>
      </button>

      {/* Version history */}
      <div className="card rounded-lg p-4 space-y-3 border-2 border-foreground bg-surface shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff]">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-black uppercase">Version History</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Save named timeline versions and restore earlier edits. AI
              proposals are reviewed before they become timeline versions.
            </p>
          </div>
          <History className="size-4 text-foreground" strokeWidth={2} />
        </div>

        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="Snapshot name"
            value={snapshotName}
            onChange={(event) => setSnapshotName(event.target.value)}
            placeholder={
              activePlan ? `Timeline v${activePlan.version}` : "Version name"
            }
            disabled={!activePlan || snapshotLoading}
          />
          <Button
            type="button"
            variant="secondary"
            onClick={saveSnapshot}
            disabled={!activePlan || snapshotLoading}
            className="min-h-11 gap-2 whitespace-nowrap"
          >
            {snapshotLoading ? (
              <Loader2 className="size-4 animate-spin" strokeWidth={2} />
            ) : (
              <Save className="size-4" strokeWidth={2} />
            )}
            Save
          </Button>
        </div>

        {snapshots.length ? (
          <div className="space-y-2">
            {snapshots.slice(0, 5).map((snapshot) => (
              <div
                key={snapshot.id}
                className="card rounded border-2 border-foreground flex items-center justify-between gap-3 px-3 py-2 bg-surface shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]"
              >
                <div className="min-w-0">
                  <p className="truncate text-xs font-black">{snapshot.name}</p>
                  <p className="text-[10px] font-bold text-muted-foreground font-mono">
                    Timeline v{snapshot.source_plan_version} ·{" "}
                    {snapshot.summary.cut_count} cuts
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => restoreSnapshot(snapshot.id)}
                  disabled={snapshotLoading}
                  className="pixel-button text-xs h-8 px-3"
                >
                  Restore
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded border-2 border-dashed border-foreground p-3 text-xs font-bold text-muted-foreground text-center font-mono">
            No saved snapshots yet.
          </p>
        )}
      </div>

      {/* Status / error message */}
      {message ? (
        <p
          id="ai-edit-message"
          role={isErrorMessage ? "alert" : "status"}
          className={
            isErrorMessage
              ? "rounded border-2 border-danger bg-danger/10 p-4 text-sm text-danger shadow-[2px_2px_0px_#000]"
              : "rounded border-2 border-foreground bg-muted p-3 text-sm font-bold text-foreground font-mono shadow-[2px_2px_0px_#000]"
          }
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}

function formatClipTimestamp(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function defaultCaptionPreset(style: StylePickerValue) {
  switch (style) {
    case "auto":
      return "creator_ticker";
    case "dramatic":
    case "motivational":
      return "impact_stamp";
    case "fashion":
      return "vogue_white";
    case "educational":
      return "educational_card";
    case "clean_minimal":
    case "founder":
      return "founder_clean";
    case "fast_paced":
    case "creator_influencer":
    case "travel":
      return "creator_ticker";
    case "storytelling":
    case "podcast":
      return "founder_clean";
    case "cinematic":
    case "luxury":
      return "minimal_line";
    case "emotional":
    default:
      return "luxury_minimal";
  }
}

function actionSelectionBlockReason(
  action: ProposalAction,
  actions: ProposalAction[],
  selectedActionIds: Set<string>,
) {
  if (selectedActionIds.has(action.id)) {
    const selectedDependents = actions.filter(
      (candidate) =>
        selectedActionIds.has(candidate.id) &&
        (candidate.dependency_ids ?? []).includes(action.id),
    );
    if (selectedDependents.length) {
      return `Required by ${selectedDependents
        .map((candidate) => formatProposalTool(candidate.tool_name))
        .join(", ")}. Deselect those actions first.`;
    }
    return null;
  }

  const missingDependencies = (action.dependency_ids ?? [])
    .filter((dependencyId) => !selectedActionIds.has(dependencyId))
    .map((dependencyId) =>
      actions.find((candidate) => candidate.id === dependencyId),
    )
    .filter((candidate): candidate is ProposalAction => Boolean(candidate));
  if (!missingDependencies.length) return null;
  return `Requires ${missingDependencies
    .map((candidate) => formatProposalTool(candidate.tool_name))
    .join(", ")}. Select prerequisites first.`;
}

function selectedProposalDependencyError(
  actions: ProposalAction[],
  selectedActionIds: Set<string>,
) {
  for (const action of actions) {
    if (!selectedActionIds.has(action.id)) continue;
    const missingDependency = (action.dependency_ids ?? []).find(
      (dependencyId) => !selectedActionIds.has(dependencyId),
    );
    if (missingDependency) {
      const dependency = actions.find(
        (candidate) => candidate.id === missingDependency,
      );
      return `${formatProposalTool(action.tool_name)} requires ${
        dependency ? formatProposalTool(dependency.tool_name) : "another action"
      }.`;
    }
  }
  return null;
}

function formatProposalTool(tool: ProposalAction["tool_name"]) {
  const labels: Record<ProposalAction["tool_name"], string> = {
    complete_first_plan: "Create first edit plan",
    set_cuts: "Apply cuts and pacing",
    set_caption_highlights: "Set caption highlights",
    set_broll_requests: "Set B-roll requests",
    set_look_audio_effects: "Set look, audio, and effects",
  };
  return labels[tool];
}

function formatConfidence(confidence: number | null | undefined) {
  return confidence == null
    ? "Not reported"
    : `${Math.round(confidence * 100)}%`;
}

function formatUsd(cost: number | null | undefined) {
  return cost == null ? "Not reported" : `$${cost.toFixed(4)}`;
}

function formatFlag(flag: string) {
  return flag.replace(/_/g, " ");
}

function waitForDelay(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
