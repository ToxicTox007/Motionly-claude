export type ProductFlowStageKey =
  | "uploaded"
  | "validated"
  | "transcribing"
  | "visual_analysis"
  | "planning"
  | "captions_ready"
  | "render_queued"
  | "render_active"
  | "export_ready";

export type ProductFlowStage = {
  key: ProductFlowStageKey;
  label: string;
  description: string;
};

export const PRODUCT_FLOW_STAGES: ProductFlowStage[] = [
  {
    key: "uploaded",
    label: "Uploaded",
    description: "Source file received by secure storage",
  },
  {
    key: "validated",
    label: "Validated",
    description: "Media duration, codec, resolution, and thumbnail checked",
  },
  {
    key: "transcribing",
    label: "Transcribing",
    description: "Audio is being converted into editable caption words",
  },
  {
    key: "visual_analysis",
    label: "Visual analysis",
    description: "Frames, scenes, and composition hints are being analyzed",
  },
  {
    key: "planning",
    label: "Planning",
    description: "Timeline structure, b-roll, audio, and style are being drafted",
  },
  {
    key: "captions_ready",
    label: "Captions ready",
    description: "Caption text and timings are ready for editing",
  },
  {
    key: "render_queued",
    label: "Render queued",
    description: "The export job is waiting for an available worker",
  },
  {
    key: "render_active",
    label: "Render active",
    description: "Frames are being rendered, encoded, or uploaded",
  },
  {
    key: "export_ready",
    label: "Export ready",
    description: "The finished video is available to download or publish",
  },
];

const RENDER_STATUS_LABELS: Record<string, string> = {
  queued: "Render queued",
  processing: "Processing",
  preparing: "Processing",
  rendering: "Render active",
  uploading: "Uploading export",
  complete: "Export ready",
  failed: "Failed",
  canceled: "Canceled",
};

const PUBLISH_STATUS_LABELS: Record<string, string> = {
  queued: "Publish queued",
  publishing: "Publishing",
  published: "Published",
  failed: "Failed",
};

export function productFlowStageIndex(status: string): number {
  const normalized = status.toLowerCase().replaceAll("_", " ");
  if (normalized.includes("export") || normalized.includes("complete"))
    return stageIndex("export_ready");
  if (
    normalized.includes("rendering") ||
    normalized.includes("preparing") ||
    normalized.includes("encoding") ||
    normalized.includes("uploading export")
  )
    return stageIndex("render_active");
  if (normalized.includes("render") && normalized.includes("queued"))
    return stageIndex("render_queued");
  if (normalized.includes("caption") || normalized.includes("subtitle"))
    return stageIndex("captions_ready");
  if (normalized.includes("plan") || normalized.includes("draft"))
    return stageIndex("planning");
  if (normalized.includes("visual") || normalized.includes("analy"))
    return stageIndex("visual_analysis");
  if (normalized.includes("transcrib") || normalized.includes("audio"))
    return stageIndex("transcribing");
  if (normalized.includes("validat") || normalized.includes("process"))
    return stageIndex("validated");
  if (normalized.includes("upload")) return stageIndex("uploaded");
  return stageIndex("validated");
}

export function uploadStatusMessage(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "processing")
    return PRODUCT_FLOW_STAGES[stageIndex("validated")].description;
  if (normalized === "uploaded")
    return "Upload confirmed. Waiting for media validation.";
  if (normalized === "ready") return "Source is ready for editing.";
  if (normalized === "failed") return "Video preprocessing failed.";
  return `Processing: ${status.replaceAll("_", " ")}`;
}

export function renderStatusLabel(status: string) {
  return RENDER_STATUS_LABELS[status] ?? humanizeStatus(status);
}

export function publishStatusLabel(status: string) {
  return PUBLISH_STATUS_LABELS[status] ?? humanizeStatus(status);
}

export function queuedWaitBandLabel(
  queuedAt: string | null | undefined,
  nowMs = Date.now(),
) {
  if (!queuedAt) return "Wait band: queued";
  const queuedMs = new Date(queuedAt).getTime();
  if (!Number.isFinite(queuedMs)) return "Wait band: queued";
  const ageMinutes = Math.max(0, (nowMs - queuedMs) / 60_000);
  if (ageMinutes < 2) return "Wait band: short";
  if (ageMinutes < 10) return "Wait band: moderate";
  return "Wait band: extended";
}

function stageIndex(key: ProductFlowStageKey) {
  return PRODUCT_FLOW_STAGES.findIndex((stage) => stage.key === key);
}

function humanizeStatus(status: string) {
  return status
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}
