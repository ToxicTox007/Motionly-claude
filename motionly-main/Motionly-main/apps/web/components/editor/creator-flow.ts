import type {
  BrollSlotSummary,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  ExportSummary,
  MusicSelectionSummary,
  RenderJobSummary,
  TranscriptSummary,
  UploadedVideoSummary,
  VisualAnalysisStatusSummary,
  WorkflowDetail,
  WorkflowRunState,
} from "@motionly/shared";
import {
  renderStatusLabel,
  uploadStatusMessage,
} from "@/lib/product-flow-status";
import {
  FIRST_DRAFT_STATE_ORDER,
  isCanonicalDraftReady,
  workflowStateIndex,
} from "@/lib/workflow-ui.mjs";
import type { CreatorTask } from "./layout/types";

export type CreatorInspectorTab = "ai" | "captions" | "look" | "export";

const WORKFLOW_STAGE_COPY: Record<
  (typeof FIRST_DRAFT_STATE_ORDER)[number],
  {
    label: string;
    description: string;
    detail: string;
    actionLabel: string | null;
  }
> = {
  uploaded: {
    label: "Uploaded",
    description:
      "Source video is registered with the durable first-draft workflow.",
    detail: "Upload received.",
    actionLabel: null,
  },
  analyzing: {
    label: "Analyzing",
    description: "Transcript and visual context are being prepared.",
    detail: "Analyzing audio and video.",
    actionLabel: null,
  },
  proposal_ready: {
    label: "Proposal ready",
    description: "The workflow-linked AI proposal is ready to load.",
    detail: "Opening the canonical proposal.",
    actionLabel: "Review",
  },
  awaiting_approval: {
    label: "Awaiting approval",
    description: "Review the proposed edit before durable assembly begins.",
    detail: "Your approval is required.",
    actionLabel: "Review",
  },
  assembling: {
    label: "Assembling",
    description:
      "PostgreSQL and Temporal are assembling the approved timeline.",
    detail: "Binding the approved plan, timeline, and source lineage.",
    actionLabel: null,
  },
  draft_rendering: {
    label: "Draft rendering",
    description: "The canonical first draft is rendering.",
    detail: "Rendering the approved draft.",
    actionLabel: "View",
  },
  draft_ready: {
    label: "Draft ready",
    description: "The linked render and export are ready for review.",
    detail: "Canonical first draft is ready.",
    actionLabel: "Open",
  },
};

function terminalStageIndex(workflow: WorkflowDetail): number {
  if (workflow.draft_render?.id) return workflowStateIndex("draft_rendering");
  const types = new Set(
    (workflow.artifacts ?? []).map((artifact) => artifact.type),
  );
  if (types.has("timeline_revision") || types.has("decision")) {
    return workflowStateIndex("assembling");
  }
  if (types.has("proposal")) return workflowStateIndex("awaiting_approval");
  if (types.has("transcript") || types.has("visual_manifest")) {
    return workflowStateIndex("analyzing");
  }
  return workflowStateIndex("uploaded");
}

function workflowTasks(workflow: WorkflowDetail): CreatorTask[] {
  const stateIndex = workflowStateIndex(workflow.state);
  const currentIndex =
    stateIndex >= 0 ? stateIndex : terminalStageIndex(workflow);
  const draftReady = isCanonicalDraftReady(workflow);
  const tasks = FIRST_DRAFT_STATE_ORDER.map((state, index): CreatorTask => {
    const copy = WORKFLOW_STAGE_COPY[state];
    const isCurrent = index === currentIndex;
    let status: CreatorTask["status"] =
      index < currentIndex ? "done" : isCurrent ? "active" : "waiting";
    let progress = index < currentIndex ? 100 : isCurrent ? 25 : 0;
    let detail = copy.detail;

    if (state === "draft_rendering" && isCurrent) {
      progress = workflow.draft_render?.progress_pct ?? 5;
      detail =
        workflow.draft_render?.error_message ||
        `Rendering canonical draft: ${progress}%`;
      if (workflow.draft_render?.error_code) status = "blocked";
    }
    if (state === "draft_ready" && isCurrent) {
      status = draftReady ? "done" : "blocked";
      progress = draftReady ? 100 : 0;
      detail = draftReady
        ? copy.detail
        : "Draft lineage is incomplete; render and export IDs must match.";
    }

    return {
      key: state,
      label: copy.label,
      shortLabel: copy.label,
      description: copy.description,
      status,
      progress,
      detail,
      actionLabel: copy.actionLabel,
    };
  });

  if (workflow.state === "failed" || workflow.state === "cancelled") {
    const cancelled = workflow.state === "cancelled";
    tasks.push({
      key: workflow.state,
      label: cancelled ? "Cancelled" : "Workflow failed",
      shortLabel: cancelled ? "Cancelled" : "Failed",
      description: cancelled
        ? "The canonical first-draft workflow was cancelled."
        : "The workflow stopped safely and can be retried when allowed.",
      status: "blocked",
      progress: workflow.draft_render?.progress_pct ?? 0,
      detail: cancelled
        ? "Workflow cancelled. Retry creates a new canonical run."
        : workflow.draft_render?.error_message ||
          workflow.failure_code ||
          "Workflow failed.",
      actionLabel: workflow.allowed_actions.includes("retry") ? "Retry" : null,
    });
  }
  return tasks;
}

export function buildCreatorTasks({
  upload,
  transcript,
  visualStatus,
  activePlan,
  renderJobs,
  exports,
  workflow,
}: {
  upload: UploadedVideoSummary;
  transcript: TranscriptSummary | null;
  visualStatus: VisualAnalysisStatusSummary;
  activePlan: EditPlanRecordSummary | null;
  captionTracks: CaptionTrackSummary[];
  brollSlots: BrollSlotSummary[];
  musicSelection: MusicSelectionSummary | null;
  renderJobs: RenderJobSummary[];
  exports: ExportSummary[];
  workflow: WorkflowDetail | null;
}): CreatorTask[] {
  if (workflow) return workflowTasks(workflow);

  // Manual proposal generation remains an in-memory, standalone editor flow.
  // It is intentionally not presented as durable first-draft readiness.
  const latestRenderJob = renderJobs[0] ?? null;
  const hasStandalonePlan = activePlan !== null;
  const hasStandaloneExport =
    exports.length > 0 || latestRenderJob?.status === "complete";
  const transcriptReady = transcript?.status === "ready";
  const transcriptFailed =
    transcript?.status === "failed" ||
    transcript?.status === "captions_unavailable";
  const visualReady = visualStatus.status === "ready";
  const visualBlocked = visualStatus.status === "unavailable";

  return [
    {
      key: "upload",
      label: "Upload",
      shortLabel: "Upload",
      description: "Source video is received and ready for editing.",
      status: upload.status === "ready" ? "done" : "active",
      progress: upload.status === "ready" ? 100 : 65,
      detail: uploadStatusMessage(upload.status),
      actionLabel: null,
    },
    {
      key: "prepare",
      label: "Prepare",
      shortLabel: "Prepare",
      description:
        "Transcript and visual context are prepared for a manual proposal.",
      status: transcriptFailed
        ? "blocked"
        : transcriptReady && (visualReady || visualBlocked)
          ? "done"
          : "active",
      progress: transcriptReady
        ? 100
        : Math.max(5, transcript?.progress_pct ?? 0),
      detail: transcriptFailed
        ? transcript?.error_message || "Transcription needs a retry."
        : transcriptReady
          ? "Transcript ready."
          : "Preparing transcript and visual context.",
      actionLabel: transcriptFailed ? "Retry" : null,
    },
    {
      key: "draft",
      label: "Standalone edit",
      shortLabel: "Edit",
      description: "A manual proposal generated in this browser session.",
      status: hasStandalonePlan
        ? "done"
        : transcriptReady
          ? "active"
          : "waiting",
      progress: hasStandalonePlan ? 100 : transcriptReady ? 25 : 0,
      detail: hasStandalonePlan
        ? `Timeline v${activePlan.version} is available.`
        : "Generate and review a standalone proposal.",
      actionLabel: hasStandalonePlan ? "Review" : "Generate",
    },
    {
      key: "export",
      label: "Standalone export",
      shortLabel: "Export",
      description: "Exports outside the canonical first-draft workflow.",
      status: hasStandaloneExport
        ? "done"
        : latestRenderJob?.status === "failed"
          ? "blocked"
          : latestRenderJob
            ? "active"
            : "waiting",
      progress: hasStandaloneExport
        ? 100
        : (latestRenderJob?.progress_pct ?? 0),
      detail: latestRenderJob
        ? renderStatusLabel(latestRenderJob.status)
        : "No standalone render started.",
      actionLabel: hasStandaloneExport ? "Download" : "Render",
    },
  ];
}

export function inspectorTabForCreatorTask(
  taskKey: string,
): CreatorInspectorTab {
  switch (taskKey as WorkflowRunState | string) {
    case "uploaded":
    case "analyzing":
    case "proposal_ready":
    case "awaiting_approval":
    case "assembling":
    case "upload":
    case "prepare":
    case "draft":
    case "failed":
    case "cancelled":
      return "ai";
    case "draft_rendering":
    case "draft_ready":
    case "export":
      return "export";
    case "review":
      return "captions";
    case "polish":
      return "look";
    default:
      return "ai";
  }
}
