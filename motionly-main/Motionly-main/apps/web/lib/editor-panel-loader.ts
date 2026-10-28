// Server-only: serverApiFetch refuses to run in the browser.
import type {
  BillingStatusSummary,
  BrollSlotSummary,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EffectsConfigSummary,
  ExportSummary,
  MusicSelectionSummary,
  RenderJobSummary,
  TranscriptSummary,
  VisualAnalysisStatusSummary,
  WorkflowList,
} from "@motionly/shared";
import type { EditorPanelErrors, EditorPanelKey } from "@/lib/editor-panels";
import { serverApiFetch } from "@/lib/server-api";

export type UploadAssetUrlsResponse = {
  source_video_url?: string;
  thumbnail_url?: string | null;
  expires_in_seconds?: number;
  expires_at?: string | null;
  source_reference_id?: string | null;
  thumbnail_reference_id?: string | null;
};

export type EditorPanelData = {
  assetUrls: UploadAssetUrlsResponse | null;
  transcript: TranscriptSummary | null;
  captions: { tracks?: CaptionTrackSummary[] } | null;
  plans: {
    plans?: EditPlanRecordSummary[];
    active_plan_id?: string | null;
  } | null;
  broll: { slots?: BrollSlotSummary[] } | null;
  music: MusicSelectionSummary | null;
  effects: EffectsConfigSummary | null;
  renders: { jobs?: RenderJobSummary[] } | null;
  exports: { exports?: ExportSummary[] } | null;
  billing: BillingStatusSummary | null;
  visual: VisualAnalysisStatusSummary | null;
  workflows: WorkflowList | null;
};

type PanelSpec = {
  path: string;
  /** A 404 means "nothing yet" (no transcript, no music chosen), so it is an
   * empty panel. Elsewhere the resource always exists for a project and a
   * 404 is a failure. */
  missingIsEmpty: boolean;
};

type PanelOutcome<T> =
  | { data: T | null; error: null }
  | { data: null; error: { requestId: string | null } };

async function loadPanel<T>(
  key: EditorPanelKey,
  { path, missingIsEmpty }: PanelSpec,
  headers: Record<string, string>,
): Promise<PanelOutcome<T>> {
  const result = await serverApiFetch<T>(path, { headers });
  if (result.ok) return { data: result.data, error: null };
  if (result.kind === "http" && result.status === 404 && missingIsEmpty) {
    return { data: null, error: null };
  }
  // Path, status and request id only: never headers or bodies.
  console.warn("Editor panel failed to load", {
    panel: key,
    kind: result.kind,
    status: result.status,
    requestId: result.requestId,
  });
  return { data: null, error: { requestId: result.requestId ?? null } };
}

/**
 * Loads every editor panel independently (B27): one failing request never
 * rejects the page. Each panel is either data (possibly a genuine empty) or an
 * entry in `errors`, so the editor can show "couldn't load" with Retry
 * instead of treating a failure as "nothing here yet".
 */
export async function loadEditorPanels(
  projectId: string,
  headers: Record<string, string>,
  upload: { id: string } | null,
): Promise<{ data: EditorPanelData; errors: EditorPanelErrors }> {
  const project = `/projects/${projectId}`;
  const specs: Record<EditorPanelKey, PanelSpec | null> = {
    assetUrls: upload
      ? {
          path: `${project}/uploads/${upload.id}/asset-urls`,
          missingIsEmpty: false,
        }
      : null,
    transcript: { path: `${project}/transcript`, missingIsEmpty: true },
    captions: { path: `${project}/captions`, missingIsEmpty: false },
    plans: { path: `${project}/plans`, missingIsEmpty: false },
    broll: { path: `${project}/broll`, missingIsEmpty: false },
    music: { path: `${project}/music/selection`, missingIsEmpty: true },
    effects: { path: `${project}/effects`, missingIsEmpty: true },
    renders: { path: `${project}/renders`, missingIsEmpty: false },
    exports: { path: `${project}/exports`, missingIsEmpty: false },
    billing: { path: "/billing/status", missingIsEmpty: false },
    visual: { path: `${project}/visual/status`, missingIsEmpty: true },
    workflows: {
      path: `${project}/workflows?limit=50`,
      missingIsEmpty: false,
    },
  };

  const keys = Object.keys(specs) as EditorPanelKey[];
  const outcomes = await Promise.all(
    keys.map((key) => {
      const spec = specs[key];
      return spec
        ? loadPanel<unknown>(key, spec, headers)
        : Promise.resolve({ data: null, error: null } as const);
    }),
  );

  const data = {} as Record<EditorPanelKey, unknown>;
  const errors: EditorPanelErrors = {};
  keys.forEach((key, index) => {
    const outcome = outcomes[index];
    data[key] = outcome.data;
    if (outcome.error) errors[key] = outcome.error;
  });
  return { data: data as EditorPanelData, errors };
}
