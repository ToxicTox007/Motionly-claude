import type {
  BrollSlotSummary,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EffectsConfigSummary,
  ExportSummary,
  MusicSelectionSummary,
  MusicTrackSummary,
  RenderJobSummary,
  TranscriptSummary,
  UploadedVideoSummary,
  VisualPresetKey,
} from "@motionly/shared";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { EditorClient } from "@/components/editor-client";
import { env } from "@/lib/env";
import { createClient } from "@/lib/supabase/server";

export default async function ProjectDetailsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  let title = "Project";
  let workspaceId = "";
  let userId = "";
  let apiAccessToken = "";
  let upload: UploadedVideoSummary | null = null;
  let uploadAssetUrls: {
    source_video_url: string | null;
    thumbnail_url: string | null;
  } = {
    source_video_url: null,
    thumbnail_url: null,
  };
  let transcript: TranscriptSummary | null = null;
  let captionTracks: CaptionTrackSummary[] = [];
  let editPlans: EditPlanRecordSummary[] = [];
  let activePlanId: string | null = null;
  let brollSlots: BrollSlotSummary[] = [];
  let musicSelection: MusicSelectionSummary | null = null;
  let effectsConfig: EffectsConfigSummary | null = null;
  let renderJobs: RenderJobSummary[] = [];
  let exports: ExportSummary[] = [];

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!user || !session?.access_token) {
    redirect(`/login?next=${encodeURIComponent(`/projects/${projectId}`)}`);
  }

  userId = user.id;
  apiAccessToken = session.access_token;
  const { data: project } = await supabase
    .from("projects")
    .select(
      "id,title,workspace_id,uploaded_videos!uploaded_videos_project_id_fkey(id,status,original_filename,file_size_bytes,mime_type,storage_bucket,storage_path,duration_ms,width,height,thumbnail_path,metadata,error_message,updated_at)",
    )
    .eq("id", projectId)
    .limit(1)
    .maybeSingle();

  if (!project) {
    return (
      <AppShell title="Project not found">
        <p className="text-sm text-muted-foreground">
          This project does not exist or you do not have access to it.
        </p>
      </AppShell>
    );
  }

  title = project.title;
  workspaceId = project.workspace_id;
  const uploads = Array.isArray(project.uploaded_videos)
    ? project.uploaded_videos
    : [];
  upload =
    uploads
      .sort(
        (a: { updated_at: string }, b: { updated_at: string }) =>
          b.updated_at.localeCompare(a.updated_at),
      )
      .map((item: {
        id: string;
        status: string;
        original_filename: string;
        file_size_bytes: number | null;
        mime_type: string;
        storage_bucket: string;
        storage_path: string;
        duration_ms: number | null;
        width: number | null;
        height: number | null;
        thumbnail_path: string | null;
        metadata: { warnings?: string[] } | null;
        error_message: string | null;
        updated_at: string;
      }) => ({
        id: item.id,
        projectId,
        workspaceId,
        originalFilename: item.original_filename,
        status: item.status as UploadedVideoSummary["status"],
        durationMs: item.duration_ms,
        width: item.width,
        height: item.height,
        thumbnailPath: item.thumbnail_path,
        warnings: item.metadata?.warnings ?? [],
        updatedAt: item.updated_at,
      }))
      [0] ?? null;
  const apiHeaders = {
    authorization: `Bearer ${apiAccessToken}`,
    "x-motionly-workspace-id": workspaceId,
    "x-motionly-user-id": userId,
  };
  if (upload && ["uploaded", "processing", "ready", "failed"].includes(upload.status)) {
    const assetUrlResponse = await fetch(
      `${env.apiBaseUrl}/projects/${projectId}/uploads/${upload.id}/asset-urls`,
      {
        headers: apiHeaders,
        cache: "no-store",
      },
    );
    if (assetUrlResponse.ok) {
      const payload = (await assetUrlResponse.json()) as {
        source_video_url?: string;
        thumbnail_url?: string | null;
      };
      uploadAssetUrls = {
        source_video_url: payload.source_video_url ?? null,
        thumbnail_url: payload.thumbnail_url ?? null,
      };
    }
  }

  const { data: transcriptRow } = upload?.id
    ? await supabase
        .from("transcripts")
        .select(
          "id,status,language,provider,model,confidence,uploaded_video_id,transcript_words(id,word_index,text,start_ms,end_ms,confidence)",
        )
        .eq("project_id", projectId)
        .eq("uploaded_video_id", upload.id)
        .order("updated_at", { ascending: false })
        .limit(1)
        .maybeSingle()
    : { data: null };

  if (transcriptRow) {
    const words = Array.isArray(transcriptRow.transcript_words)
      ? transcriptRow.transcript_words
      : [];
    transcript = {
      id: transcriptRow.id,
      status: transcriptRow.status,
      language: transcriptRow.language,
      provider: transcriptRow.provider,
      model: transcriptRow.model,
      confidence: transcriptRow.confidence,
      words: words
        .sort(
          (a: { word_index: number }, b: { word_index: number }) =>
            a.word_index - b.word_index,
        )
        .map(
          (word: {
            id: string;
            word_index: number;
            text: string;
            start_ms: number;
            end_ms: number;
            confidence: number | null;
          }) => ({
            id: word.id,
            index: word.word_index,
            text: word.text,
            start_ms: word.start_ms,
            end_ms: word.end_ms,
            confidence: word.confidence,
          }),
        ),
    };
  }

  const { data: tracks } = transcript?.id
    ? await supabase
        .from("caption_tracks")
        .select(
          "id,preset,schema_version,style,hide_filler_words,caption_segments(id,segment_index,start_ms,end_ms,text,line_count,words)",
        )
        .eq("project_id", projectId)
        .eq("transcript_id", transcript.id)
        .order("updated_at", { ascending: false })
    : { data: [] };

  captionTracks = (tracks ?? []).map((track) => {
    const segments = Array.isArray(track.caption_segments)
      ? track.caption_segments
      : [];
    return {
      id: track.id,
      preset: track.preset,
      schema_version: track.schema_version,
      style: track.style,
      hide_filler_words: track.hide_filler_words,
      segments: segments
        .sort(
          (a: { segment_index: number }, b: { segment_index: number }) =>
            a.segment_index - b.segment_index,
        )
        .map(
          (segment: {
            id: string;
            segment_index: number;
            start_ms: number;
            end_ms: number;
            text: string;
            line_count: number;
            words: unknown;
          }) => ({
            id: segment.id,
            index: segment.segment_index,
            start_ms: segment.start_ms,
            end_ms: segment.end_ms,
            text: segment.text,
            line_count: segment.line_count,
            words: Array.isArray(segment.words) ? segment.words : [],
          }),
        ),
    };
  });

  const { data: planRows } = transcript?.id
    ? await supabase
        .from("edit_plans")
        .select(
          "id,project_id,transcript_id,version,schema_version,status,is_active,style,target_duration_s,broll_density,prompt,feedback,provider,model,cache_key,schema_hash,latency_ms,cost_estimate_usd,validation_errors,summary,plan,created_at,updated_at",
        )
        .eq("project_id", projectId)
        .eq("transcript_id", transcript.id)
        .order("version", { ascending: false })
    : { data: [] };

  editPlans = (planRows ?? []).map((plan) => ({
    id: plan.id,
    project_id: plan.project_id,
    transcript_id: plan.transcript_id,
    version: plan.version,
    schema_version: plan.schema_version,
    status: plan.status,
    is_active: plan.is_active,
    style: plan.style,
    target_duration_s: plan.target_duration_s,
    broll_density: plan.broll_density,
    prompt: plan.prompt,
    feedback: plan.feedback,
    provider: plan.provider,
    model: plan.model,
    cache_key: plan.cache_key,
    schema_hash: plan.schema_hash,
    latency_ms: plan.latency_ms,
    cost_estimate_usd: plan.cost_estimate_usd,
    validation_errors: Array.isArray(plan.validation_errors)
      ? plan.validation_errors
      : [],
    summary: plan.summary,
    plan: plan.plan,
    created_at: plan.created_at,
    updated_at: plan.updated_at,
  }));
  activePlanId = editPlans.find((plan) => plan.is_active)?.id ?? null;

  const { data: musicSelectionRow } = activePlanId
    ? await supabase
        .from("project_music_selections")
        .select(
          "id,workspace_id,project_id,edit_plan_id,music_track_id,music_volume,sfx_intensity,rights_confirmed,status,cache_key,created_at,updated_at",
        )
        .eq("project_id", projectId)
        .eq("edit_plan_id", activePlanId)
        .order("updated_at", { ascending: false })
        .limit(1)
        .maybeSingle()
    : { data: null };

  let musicTrack: MusicTrackSummary | null = null;
  if (musicSelectionRow?.music_track_id) {
    const { data: musicTrackRow } = await supabase
      .from("music_tracks")
      .select(
        "id,workspace_id,project_id,provider,provider_track_id,title,license,duration_ms,mood_tags,bpm,energy,vocals,loopable,beat_map,raw_metadata,created_at",
      )
      .eq("id", musicSelectionRow.music_track_id)
      .limit(1)
      .maybeSingle();
    if (musicTrackRow) {
      musicTrack = {
        id: musicTrackRow.id,
        workspace_id: musicTrackRow.workspace_id,
        project_id: musicTrackRow.project_id,
        provider: musicTrackRow.provider,
        provider_track_id: musicTrackRow.provider_track_id,
        title: musicTrackRow.title,
        license: musicTrackRow.license,
        duration_ms: musicTrackRow.duration_ms,
        mood_tags: Array.isArray(musicTrackRow.mood_tags)
          ? musicTrackRow.mood_tags
          : [],
        bpm: musicTrackRow.bpm,
        energy: musicTrackRow.energy,
        vocals: musicTrackRow.vocals,
        loopable: musicTrackRow.loopable,
        beat_map: Array.isArray(musicTrackRow.beat_map)
          ? musicTrackRow.beat_map
          : [],
        raw_metadata: musicTrackRow.raw_metadata ?? {},
        created_at: musicTrackRow.created_at,
      };
    }
  }
  if (musicSelectionRow) {
    musicSelection = {
      id: musicSelectionRow.id,
      workspace_id: musicSelectionRow.workspace_id,
      project_id: musicSelectionRow.project_id,
      edit_plan_id: musicSelectionRow.edit_plan_id,
      music_track_id: musicSelectionRow.music_track_id,
      music_volume: musicSelectionRow.music_volume,
      sfx_intensity: musicSelectionRow.sfx_intensity,
      rights_confirmed: musicSelectionRow.rights_confirmed,
      status: musicSelectionRow.status,
      cache_key: musicSelectionRow.cache_key,
      created_at: musicSelectionRow.created_at,
      updated_at: musicSelectionRow.updated_at,
      music_track: musicTrack,
    };
  }

  const { data: effectsRow } = activePlanId
    ? await supabase
        .from("visual_presets")
        .select(
          "id,workspace_id,project_id,edit_plan_id,version,schema_version,status,preset,lut_intensity,texture,motion_effects,background_removal,gradient_ramp,after_effects_presets,after_effects_project_url,correction_manifest_url,effect_count_limit_per_30s,render_time_impact,preview_before_url,preview_after_url,cache_key,error_code,error_message,created_at,updated_at",
        )
        .eq("project_id", projectId)
        .eq("edit_plan_id", activePlanId)
        .order("updated_at", { ascending: false })
        .limit(1)
        .maybeSingle()
    : { data: null };

  if (effectsRow) {
    effectsConfig = {
      id: effectsRow.id,
      workspace_id: effectsRow.workspace_id,
      project_id: effectsRow.project_id,
      edit_plan_id: effectsRow.edit_plan_id,
      version: effectsRow.version,
      schema_version: effectsRow.schema_version,
      status: effectsRow.status,
      preset: effectsRow.preset,
      lut_intensity: effectsRow.lut_intensity,
      texture: effectsRow.texture ?? {
        grain: 0.18,
        vignette: 0.16,
        light_leak: 0.08,
        anamorphic_flare: false,
      },
      motion_effects: Array.isArray(effectsRow.motion_effects)
        ? effectsRow.motion_effects
        : [],
      background_removal: effectsRow.background_removal ?? {
        enabled: false,
        apply_to_broll: true,
        feather_px: 18,
        spill_suppression: 0.45,
        replacement: "gradient_ramp",
      },
      gradient_ramp: effectsRow.gradient_ramp ?? {
        enabled: false,
        start_color: "#0f172a",
        end_color: "#f59e0b",
        angle_deg: 135,
        opacity: 0.28,
        blend_mode: "soft-light",
      },
      after_effects_presets: Array.isArray(effectsRow.after_effects_presets)
        ? effectsRow.after_effects_presets
        : [],
      after_effects_project_url: effectsRow.after_effects_project_url,
      correction_manifest_url: effectsRow.correction_manifest_url,
      effect_count_limit_per_30s: effectsRow.effect_count_limit_per_30s,
      render_time_impact: effectsRow.render_time_impact,
      preview_before_url: effectsRow.preview_before_url,
      preview_after_url: effectsRow.preview_after_url,
      cache_key: effectsRow.cache_key,
      error_code: effectsRow.error_code,
      error_message: effectsRow.error_message,
      created_at: effectsRow.created_at,
      updated_at: effectsRow.updated_at,
    };
  }

  const { data: brollRows } = activePlanId
    ? await supabase
        .from("broll_slots")
        .select(
          "id,workspace_id,project_id,edit_plan_id,version,schema_version,status,start_ms,end_ms,keywords,intent,density,priority,selected_asset_id,candidate_asset_ids,crossfade_ms,provider,score,error_code,error_message,created_at,updated_at",
        )
        .eq("project_id", projectId)
        .eq("edit_plan_id", activePlanId)
        .order("version", { ascending: false })
        .order("start_ms", { ascending: true })
    : { data: [] };

  const latestBrollVersion = Math.max(
    ...(brollRows ?? []).map((slot) => slot.version),
    0,
  );
  const latestBrollRows = (brollRows ?? []).filter(
    (slot) => slot.version === latestBrollVersion,
  );
  const assetIds = Array.from(
    new Set(
      latestBrollRows
        .flatMap((slot) => [
          ...(Array.isArray(slot.candidate_asset_ids)
            ? slot.candidate_asset_ids
            : []),
          slot.selected_asset_id,
        ])
        .filter(Boolean),
    ),
  );
  const { data: assetRows } = assetIds.length
    ? await supabase
        .from("stock_assets")
        .select(
          "id,workspace_id,project_id,provider,provider_asset_id,title,thumbnail_url,preview_video_url,download_url,license,width,height,duration_ms,orientation,score,raw_metadata,created_at",
        )
        .in("id", assetIds)
    : { data: [] };
  const assetsById = new Map(
    (assetRows ?? []).map((asset) => [asset.id, asset]),
  );
  brollSlots = latestBrollRows.map((slot) => {
    const candidateIds = Array.isArray(slot.candidate_asset_ids)
      ? slot.candidate_asset_ids
      : [];
    const candidates = candidateIds
      .map((assetId: string) => assetsById.get(assetId))
      .filter(Boolean);
    return {
      id: slot.id,
      workspace_id: slot.workspace_id,
      project_id: slot.project_id,
      edit_plan_id: slot.edit_plan_id,
      version: slot.version,
      schema_version: slot.schema_version,
      status: slot.status,
      start_ms: slot.start_ms,
      end_ms: slot.end_ms,
      keywords: Array.isArray(slot.keywords) ? slot.keywords : [],
      intent: slot.intent,
      density: slot.density,
      priority: slot.priority,
      selected_asset_id: slot.selected_asset_id,
      candidate_asset_ids: candidateIds,
      crossfade_ms: slot.crossfade_ms,
      provider: slot.provider,
      score: slot.score,
      error_code: slot.error_code,
      error_message: slot.error_message,
      selected_asset: slot.selected_asset_id
        ? (assetsById.get(slot.selected_asset_id) ?? null)
        : null,
      candidates,
      created_at: slot.created_at,
      updated_at: slot.updated_at,
    } as BrollSlotSummary;
  });

  const [renderJobsResponse, exportsResponse] = await Promise.all([
    fetch(`${env.apiBaseUrl}/projects/${projectId}/renders`, {
      headers: apiHeaders,
      cache: "no-store",
    }),
    fetch(`${env.apiBaseUrl}/projects/${projectId}/exports`, {
      headers: apiHeaders,
      cache: "no-store",
    }),
  ]);
  if (renderJobsResponse.ok) {
    const payload = (await renderJobsResponse.json()) as {
      jobs?: RenderJobSummary[];
    };
    renderJobs = payload.jobs ?? [];
  }
  if (exportsResponse.ok) {
    const payload = (await exportsResponse.json()) as {
      exports?: ExportSummary[];
    };
    exports = payload.exports ?? [];
  }

  return (
    <EditorClient
      projectId={projectId}
      workspaceId={workspaceId}
      userId={userId}
      apiAccessToken={apiAccessToken}
      title={title}
      upload={upload}
      uploadAssetUrls={uploadAssetUrls}
      transcript={transcript}
      captionTracks={captionTracks}
      editPlans={editPlans}
      activePlanId={activePlanId}
      brollSlots={brollSlots}
      musicSelection={musicSelection}
      effectsConfig={effectsConfig}
      renderJobs={renderJobs}
      exports={exports}
    />
  );
}

function toVisualPreset(value: string | undefined): VisualPresetKey | null {
  const allowed: VisualPresetKey[] = [
    "vogue_luxury",
    "champagne_gold",
    "dubai_influencer",
    "old_money",
    "clean_founder",
    "cinematic_hotel",
  ];
  return allowed.includes(value as VisualPresetKey)
    ? (value as VisualPresetKey)
    : null;
}
