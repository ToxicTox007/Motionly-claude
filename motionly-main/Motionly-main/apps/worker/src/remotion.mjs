/**
 * remotion.mjs
 * -----------
 * Handles render jobs with render_engine = "remotion_premium".
 *
 * Flow:
 *   1. Load the full render job manifest directly from Supabase
 *   2. Resolve all supabase:// media references to signed URLs
 *   3. Filter fixture:// assets (not renderable in production)
 *   4. Write the assembled MotionlyRenderProps to a temp JSON file
 *   5. Spawn the Remotion CLI: node --import ./ts-node-register.mjs src/render-cli.ts
 *   6. Read the render metadata written by the CLI ({output}.json)
 *   7. Upload the rendered MP4 to Supabase Storage
 *   8. Finalise the job/export records directly in Supabase
 *   9. Clean up temp files
 */

import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { createReadStream, createWriteStream, existsSync } from "node:fs";
import { createServer } from "node:http";
import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createClient } from "@supabase/supabase-js";
import {
  assertSafeOutboundMediaUrl,
  manualElementMediaReferences,
  safeFetchMedia,
  sanitizeManualElementProperties,
  validateRenderMediaManifest,
} from "./media-url-policy.mjs";
import { env } from "./queue-common.mjs";
import { createRedactingLineWriter } from "./log-redaction.mjs";
import { mediaToolEnv, rendererEnv } from "./subprocess-env.mjs";

class StructuredRenderError extends Error {
  constructor(code, message, { retryable = false } = {}) {
    super(message);
    this.name = "StructuredRenderError";
    this.code = code;
    this.retryable = retryable;
  }
}

/** Renderer base directory. Defaults to the sibling 'renderer' package. */
function rendererPath() {
  return resolve(
    env("RENDERER_BASE_PATH", join(import.meta.dirname, "../../renderer")),
  );
}

function supabaseClient() {
  const url = env("SUPABASE_URL");
  const key = env("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) {
    throw new Error(
      "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for Remotion rendering.",
    );
  }
  return createClient(url, key, { auth: { persistSession: false } });
}

async function loadRenderJobDirect(
  renderJobId,
  workspaceId,
  projectId,
  client = supabaseClient(),
) {
  const { data, error } = await client
    .from("render_jobs")
    .select("*")
    .eq("id", renderJobId)
    .eq("workspace_id", workspaceId)
    .eq("project_id", projectId)
    .single();
  if (error || !data)
    throw new Error(
      `Failed to load render job: ${error?.message || "missing"}`,
    );
  return data;
}

async function persistProgressDirect(
  renderJobId,
  workspaceId,
  projectId,
  payload,
  client = supabaseClient(),
) {
  const status = payload.status || "rendering";
  const { data, error } = await client
    .from("render_jobs")
    .update({ status, progress_pct: payload.progressPct })
    .eq("id", renderJobId)
    .eq("workspace_id", workspaceId)
    .eq("project_id", projectId)
    .not("status", "in", "(canceled,failed,complete)")
    .select("id");
  if (error)
    throw new Error(`Failed to persist render progress: ${error.message}`);
  if (!Array.isArray(data) || data.length === 0) return false;
  const { error: eventError } = await client.from("render_job_events").insert({
    render_job_id: renderJobId,
    workspace_id: workspaceId,
    project_id: projectId,
    event_type: payload.eventType || "worker_progress",
    to_status: status,
    progress_pct: payload.progressPct,
    message: payload.message,
    metadata: payload.metadata || {},
  });
  if (eventError)
    throw new Error(
      `Failed to persist render progress event: ${eventError.message}`,
    );
  return true;
}

async function loadRenderJobStateDirect(
  renderJobId,
  workspaceId,
  projectId,
  client = supabaseClient(),
) {
  const { data, error } = await client
    .from("render_jobs")
    .select("status,artifact_path")
    .eq("id", renderJobId)
    .eq("workspace_id", workspaceId)
    .eq("project_id", projectId)
    .single();
  if (error || !data)
    throw new Error(
      `Failed to load render job status: ${error?.message || "missing"}`,
    );
  return {
    status: String(data.status || ""),
    artifactPath:
      typeof data.artifact_path === "string" && data.artifact_path
        ? data.artifact_path
        : null,
  };
}

async function loadRenderJobStatusDirect(
  renderJobId,
  workspaceId,
  projectId,
  client = supabaseClient(),
) {
  return (
    await loadRenderJobStateDirect(renderJobId, workspaceId, projectId, client)
  ).status;
}

export function attemptStoragePaths({
  workspaceId,
  projectId,
  renderJobId,
  queueJobId,
  attemptsMade,
}) {
  if (
    typeof queueJobId !== "string" ||
    !/^[A-Za-z0-9:_-]{1,200}$/.test(queueJobId)
  ) {
    throw new StructuredRenderError(
      "queue_attempt_identity_invalid",
      "BullMQ job identity is unavailable.",
    );
  }
  if (!Number.isInteger(attemptsMade) || attemptsMade < 0) {
    throw new StructuredRenderError(
      "queue_attempt_identity_invalid",
      "BullMQ attempt number is unavailable.",
    );
  }
  const encodedJobId = createHash("sha256")
    .update(queueJobId)
    .digest("base64url")
    .slice(0, 32);
  const attemptId = `q-${encodedJobId}-a-${attemptsMade + 1}`;
  const prefix = `workspaces/${workspaceId}/projects/${projectId}/exports/${renderJobId}/attempts/${attemptId}`;
  return {
    attemptId,
    stagingPath: `${prefix}/staging.mp4`,
    finalPath: `${prefix}/final.mp4`,
  };
}

async function persistCompleteDirect(
  renderJobId,
  workspaceId,
  projectId,
  details,
  client = supabaseClient(),
) {
  const { data: completionRows, error: completionError } = await client.rpc(
    "motionly_complete_render",
    {
      p_workspace_id: workspaceId,
      p_project_id: projectId,
      p_render_job_id: renderJobId,
      p_storage_bucket: details.artifactBucket,
      p_storage_path: details.exportStoragePath,
      p_format: "mp4",
      p_width: details.width,
      p_height: details.height,
      p_fps: details.fps,
      p_duration_ms: details.durationMs,
      p_file_size_bytes: details.fileSizeBytes,
      p_thumbnail_url: null,
      p_settings_summary: {
        render_engine: "remotion_premium",
        renderer_version: "remotion-timeline-v2",
      },
      p_benchmark_ms: details.benchmarkMs,
    },
  );
  if (completionError) {
    const error = new Error(
      `Failed to complete render: ${completionError.message}`,
    );
    error.code = completionError.code;
    throw error;
  }
  const completion = Array.isArray(completionRows)
    ? completionRows[0]
    : completionRows;
  const exportId = completion?.export_id || null;
  if (!exportId || completion?.status !== "complete") {
    throw new Error(
      "Failed to complete render: canonical completion result was missing",
    );
  }
  return {
    render_job_id: renderJobId,
    export_id: exportId,
    status: "complete",
    artifact_path: completion.artifact_path,
  };
}

export async function assertRenderCompletableDirect(
  renderJobId,
  workspaceId,
  projectId,
  client = supabaseClient(),
) {
  const status = await loadRenderJobStatusDirect(
    renderJobId,
    workspaceId,
    projectId,
    client,
  );
  if (status === "canceled") {
    throw new StructuredRenderError(
      "render_cancelled",
      "Render was canceled by the user.",
    );
  }
  if (status === "failed") {
    throw new StructuredRenderError(
      "render_terminal_state",
      "Render has already failed.",
    );
  }
  if (status === "complete") {
    throw new StructuredRenderError(
      "render_completion_lost",
      "Render was completed by another attempt.",
    );
  }
  return status;
}

export async function cleanupAttemptArtifacts(
  client,
  artifactBucket,
  attemptPaths,
  { removeFinal = true } = {},
) {
  const paths = [attemptPaths.stagingPath];
  if (removeFinal) paths.push(attemptPaths.finalPath);
  const { error } = await client.storage.from(artifactBucket).remove(paths);
  if (error) {
    console.warn(
      JSON.stringify({
        event: "remotion_attempt_cleanup_failed",
        bucket: artifactBucket,
        attempt_id: attemptPaths.attemptId,
        message: error.message,
      }),
    );
  }
}

async function cleanupAttemptIfLost({
  client,
  artifactBucket,
  attemptPaths,
  renderJobId,
  workspaceId,
  projectId,
  error,
}) {
  if (error?.attemptCleanupDone) return;
  let renderState;
  try {
    renderState = await loadRenderJobStateDirect(
      renderJobId,
      workspaceId,
      projectId,
      client,
    );
  } catch {
    // The staging object is never canonical, but a final attempt artifact might
    // have been committed by an RPC whose response was lost. Retain that final
    // artifact for reconciliation until authoritative state is available.
    await cleanupAttemptArtifacts(client, artifactBucket, attemptPaths, {
      removeFinal: false,
    });
    return;
  }

  const terminal = ["canceled", "failed", "complete"].includes(
    renderState.status,
  );
  if (!terminal) {
    await cleanupAttemptArtifacts(client, artifactBucket, attemptPaths, {
      removeFinal: false,
    });
    return;
  }

  const completedWithAnotherArtifact =
    renderState.status === "complete" &&
    Boolean(renderState.artifactPath) &&
    renderState.artifactPath !== attemptPaths.finalPath;
  const removeFinal =
    renderState.status !== "complete" || completedWithAnotherArtifact;
  await cleanupAttemptArtifacts(client, artifactBucket, attemptPaths, {
    removeFinal,
  });
  if (error && typeof error === "object") error.attemptCleanupDone = true;
}

export async function finalizeAttemptWithCas({
  client,
  renderJobId,
  workspaceId,
  projectId,
  artifactBucket,
  attemptPaths,
  completionDetails,
}) {
  try {
    await assertRenderCompletableDirect(
      renderJobId,
      workspaceId,
      projectId,
      client,
    );
    return await persistCompleteDirect(
      renderJobId,
      workspaceId,
      projectId,
      {
        ...completionDetails,
        artifactBucket,
        exportStoragePath: attemptPaths.finalPath,
      },
      client,
    );
  } catch (error) {
    await cleanupAttemptIfLost({
      client,
      artifactBucket,
      attemptPaths,
      renderJobId,
      workspaceId,
      projectId,
      error,
    });
    throw error;
  }
}

export async function persistRenderFailureDirect(
  job,
  errorMessage,
  retrying,
  failureCode = "render_worker_failed",
) {
  const client = supabaseClient();
  const terminalStatus =
    failureCode === "render_cancelled" ? "canceled" : "failed";
  const status = retrying ? "queued" : terminalStatus;
  const { data, error } = await client
    .from("render_jobs")
    .update({
      status,
      progress_pct: retrying ? 5 : undefined,
      error_code: retrying ? null : failureCode,
      error_message: retrying ? null : errorMessage.slice(0, 500),
    })
    .eq("id", job.resource_id)
    .eq("workspace_id", job.workspace_id)
    .eq("project_id", job.project_id)
    .not("status", "in", "(canceled,failed,complete)")
    .select("id");
  if (error)
    throw new Error(`Failed to persist render failure: ${error.message}`);
  if (!Array.isArray(data) || data.length === 0) return false;
  const { error: eventError } = await client.from("render_job_events").insert({
    render_job_id: job.resource_id,
    workspace_id: job.workspace_id,
    project_id: job.project_id,
    event_type: retrying
      ? "worker_retrying"
      : failureCode === "render_cancelled"
        ? "cancelled"
        : "failed",
    to_status: status,
    progress_pct: retrying ? 5 : null,
    message: retrying
      ? "Render worker failed; BullMQ will retry."
      : errorMessage.slice(0, 500),
    metadata: { failure_code: failureCode },
  });
  if (eventError)
    throw new Error(
      `Failed to persist render failure event: ${eventError.message}`,
    );
  return true;
}

// ---------------------------------------------------------------------------
// Supabase Storage helpers
// ---------------------------------------------------------------------------

/**
 * Parse a supabase:// storage reference.
 * Format: supabase://{bucket}/{path}
 */
function parseSupabaseSrc(src) {
  const withoutScheme = src.replace(/^supabase:\/\//, "");
  const slashIdx = withoutScheme.indexOf("/");
  if (slashIdx < 0) throw new Error(`Invalid supabase:// reference: ${src}`);
  return {
    bucket: withoutScheme.slice(0, slashIdx),
    path: withoutScheme.slice(slashIdx + 1),
  };
}

export function assertSupabaseTenantScope(src, tenantScope) {
  if (typeof src !== "string" || !src.startsWith("supabase://")) return;
  const workspaceId = String(tenantScope?.workspaceId ?? "");
  const projectId = String(tenantScope?.projectId ?? "");
  if (!workspaceId || !projectId) {
    throw new Error("Tenant scope is required for internal media signing.");
  }
  const { path } = parseSupabaseSrc(src);
  const expectedPrefix = `workspaces/${workspaceId}/projects/${projectId}/`;
  if (!path.startsWith(expectedPrefix)) {
    throw new Error(
      "Internal media reference is outside the render job tenant scope.",
    );
  }
}

export function validateRenderTenantScope(renderProps, tenantScope) {
  const candidates = [
    renderProps?.sourceVideo?.src,
    renderProps?.segmentationMatte?.src,
  ];
  for (const asset of renderProps?.assets ?? []) candidates.push(asset?.src);
  for (const element of renderProps?.editPlan?.manual_elements ?? []) {
    for (const reference of manualElementMediaReferences(element)) {
      candidates.push(reference.value);
    }
  }

  const track = renderProps?.musicSelection?.track;
  const metadata = track?.raw_metadata ?? {};
  candidates.push(
    track?.full_track_url,
    track?.preview_url,
    metadata.audio_url,
    metadata.download_url,
    metadata.preview_url,
    metadata.src,
    ...(metadata.sfx_urls ?? []),
  );
  if (metadata.audio_bucket && metadata.audio_path) {
    candidates.push(
      `supabase://${metadata.audio_bucket}/${metadata.audio_path}`,
    );
  }

  for (const stemName of ["vocals", "no_vocals"]) {
    const stem = renderProps?.sourceStems?.[stemName];
    candidates.push(stem?.src);
    if (stem?.bucket && stem?.path) {
      candidates.push(`supabase://${stem.bucket}/${stem.path}`);
    }
  }

  for (const candidate of candidates) {
    assertSupabaseTenantScope(candidate, tenantScope);
  }
}

/**
 * Resolve a durable source reference at execution time. Signed URL values stay
 * in worker memory and are never written back to the render manifest.
 */
export async function signReferenceAtExecution(
  referenceId,
  tenantScope,
  { fetchImpl = globalThis.fetch } = {},
) {
  if (!referenceId || !tenantScope?.workspaceId || !tenantScope?.projectId) {
    throw new Error(
      "Durable media signing requires a reference and tenant scope.",
    );
  }
  const apiBaseUrl = env(
    "WORKER_API_BASE_URL",
    env("API_PUBLIC_BASE_URL"),
  ).replace(/\/$/, "");
  const workerApiToken = env("WORKER_API_TOKEN");
  if (!apiBaseUrl || !workerApiToken) {
    throw new Error(
      "WORKER_API_BASE_URL and WORKER_API_TOKEN are required for media signing.",
    );
  }
  const response = await fetchImpl(`${apiBaseUrl}/internal/media/sign-urls`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-motionly-worker-token": workerApiToken,
    },
    body: JSON.stringify({
      workspace_id: tenantScope.workspaceId,
      project_id: tenantScope.projectId,
      reference_ids: [referenceId],
    }),
  });
  if (!response.ok) {
    throw new Error(`Durable media signing returned HTTP ${response.status}`);
  }
  const payload = await response.json();
  const signedUrl = payload?.signed_urls?.[referenceId];
  if (typeof signedUrl !== "string" || !signedUrl.startsWith("https://")) {
    throw new Error("Durable media signing returned an invalid URL.");
  }
  return signedUrl;
}

/**
 * Create a signed download URL for a supabase:// reference.
 * Uploaded source media uses its durable reference; older derivative types keep
 * their existing execution-time Supabase signing until they gain references.
 */
async function signSupabaseSrc(src, tenantScope, referenceId = null) {
  assertSupabaseTenantScope(src, tenantScope);
  if (referenceId) {
    return signReferenceAtExecution(referenceId, tenantScope);
  }
  const { bucket, path } = parseSupabaseSrc(src);
  const supabase = supabaseClient();
  const { data, error } = await supabase.storage
    .from(bucket)
    .createSignedUrl(path, 7_200);
  if (error) {
    throw new Error(`Failed to create signed URL for ${src}: ${error.message}`);
  }
  return data.signedUrl;
}

/**
 * Upload a local file to Supabase Storage.
 * Returns the storage path on success.
 */
async function uploadToStorage(
  localPath,
  storageBucket,
  storagePath,
  supabase = supabaseClient(),
) {
  const stream = createReadStream(localPath);
  const { error } = await supabase.storage
    .from(storageBucket)
    .upload(storagePath, stream, {
      contentType: "video/mp4",
      upsert: true,
    });
  if (error) {
    throw new Error(
      `Failed to upload rendered MP4 to ${storageBucket}/${storagePath}: ${error.message}`,
    );
  }
  return storagePath;
}

async function promoteAttemptArtifact(supabase, storageBucket, attemptPaths) {
  const { error } = await supabase.storage
    .from(storageBucket)
    .move(attemptPaths.stagingPath, attemptPaths.finalPath);
  if (error) {
    throw new Error(`Failed to promote attempt artifact: ${error.message}`);
  }
  return attemptPaths.finalPath;
}

// ---------------------------------------------------------------------------
// Props assembly
// ---------------------------------------------------------------------------

/**
 * Resolve a media src value to a real HTTP URL:
 *   - supabase://  → sign via Supabase Storage API
 *   - https://     → pass through (Pexels, Pixabay, etc.)
 *   - fixture://   → allowed only in non-production/demo mode
 */
async function resolveSrc(
  src,
  {
    mediaProxy = null,
    allowDataAudio = false,
    tenantScope = null,
    referenceId = null,
  } = {},
) {
  if (!src) return null;
  if (src.startsWith("fixture://")) {
    if (fixtureMediaAllowed()) return null;
    throw new Error("Fixture media is not allowed for production rendering.");
  }
  if (src.startsWith("supabase://")) {
    await assertSafeOutboundMediaUrl(src, {
      allowSupabase: true,
      resolveDns: false,
    });
    const signed = await signSupabaseSrc(src, tenantScope, referenceId);
    return mediaProxy
      ? mediaProxy.register(signed, { trustedInternal: true })
      : signed;
  }
  const validated = await assertSafeOutboundMediaUrl(src, {
    allowDataAudio,
    requireDns: true,
  });
  if (validated.kind === "data_audio") return src;
  if (!mediaProxy) return src;
  return mediaProxy.register(src);
}

// fixture:// media (registered test fixtures) is skipped outside production
// and never accepted in production, whatever else is set.
export function fixtureMediaAllowed(environment = process.env) {
  return String(environment.APP_ENV ?? "").trim() !== "production";
}

function renderTimeoutMs() {
  const seconds = Number(env("RENDER_TIMEOUT_SECONDS", "900"));
  return Math.max(30_000, Number.isFinite(seconds) ? seconds * 1000 : 900_000);
}

function validateBrowserExecutable() {
  const browserPath =
    env("BROWSER_EXECUTABLE_PATH") || env("PUPPETEER_EXECUTABLE_PATH");
  if (!browserPath) {
    throw new StructuredRenderError(
      "chromium_missing",
      "Premium render requires a Chromium executable.",
    );
  }
  if (!existsSync(browserPath)) {
    throw new StructuredRenderError(
      "chromium_missing",
      "Configured Chromium executable was not found.",
    );
  }
}

async function validateWritableTempDir(dirPath) {
  await mkdir(dirPath, { recursive: true });
  const probePath = join(dirPath, "preflight.tmp");
  await writeFile(probePath, "ok");
  await rm(probePath, { force: true });
}

function validateResolvedProps(props) {
  if (!props?.sourceVideo?.src) {
    throw new StructuredRenderError(
      "missing_source_video",
      "Render source video is unavailable.",
    );
  }
  if (!props?.exportSettings?.fps || Number(props.exportSettings.fps) <= 0) {
    throw new StructuredRenderError(
      "invalid_remotion_props",
      "Render props are missing a valid FPS.",
    );
  }
  for (const asset of props.assets ?? []) {
    if (
      asset.kind === "video" &&
      (!asset.src ||
        (!asset.src.startsWith("http") && !asset.src.startsWith("/")))
    ) {
      throw new StructuredRenderError(
        "broll_asset_unavailable",
        "One or more B-roll assets are unavailable.",
      );
    }
  }
  const track = props.musicSelection?.track;
  if (
    track &&
    !(
      track.full_track_url ||
      track.preview_url ||
      track.raw_metadata?.audio_url ||
      track.raw_metadata?.download_url
    )
  ) {
    throw new StructuredRenderError(
      "music_asset_unavailable",
      "Selected music is unavailable for rendering.",
    );
  }
}

/**
 * Convert the stored render_props (which contains supabase:// and fixture://
 * references) into a MotionlyRenderProps dict ready for render-cli.ts.
 *
 * All fixture:// references are dropped. supabase:// references are converted
 * to short-lived signed URLs. Real https:// references are passed through.
 */
export async function assembleRenderProps(
  renderProps,
  mediaProxy,
  tenantScope,
) {
  if (!renderProps.sourceVideo?.referenceId) {
    throw new StructuredRenderError(
      "durable_media_reference_missing",
      "Remotion source media requires a durable object reference.",
    );
  }
  const sourceUrl = await resolveSrc(renderProps.sourceVideo?.src, {
    mediaProxy,
    tenantScope,
    referenceId: renderProps.sourceVideo?.referenceId ?? null,
  });
  if (!sourceUrl) {
    throw new Error(
      "sourceVideo.src is a fixture:// reference — cannot render in production.",
    );
  }

  const resolvedAssets = (
    await Promise.all(
      (renderProps.assets ?? []).map(async (asset) => {
        const src = await resolveSrc(asset.src, { mediaProxy, tenantScope });
        if (!src) return null;
        return { ...asset, src };
      }),
    )
  ).filter(Boolean);

  const editPlan = renderProps.editPlan ?? { schema_version: 1, cuts: [] };
  const resolvedManualElements = await Promise.all(
    (editPlan.manual_elements ?? []).map(async (element) => {
      const properties = sanitizeManualElementProperties(element);
      if (!["broll", "graphic", "music", "sfx"].includes(element?.type)) {
        return { ...element, properties };
      }
      for (const key of ["assetUrl", "src"]) {
        if (typeof properties[key] !== "string") continue;
        const resolved = await resolveSrc(properties[key], {
          mediaProxy,
          tenantScope,
          allowDataAudio: ["music", "sfx"].includes(element.type),
        });
        if (resolved) properties[key] = resolved;
        else delete properties[key];
      }
      return { ...element, properties };
    }),
  );
  const resolvedEditPlan = {
    ...editPlan,
    manual_elements: resolvedManualElements,
  };

  let resolvedMusic = renderProps.musicSelection ?? null;
  if (resolvedMusic?.track) {
    const metadata = { ...(resolvedMusic.track.raw_metadata ?? {}) };
    const trackUrl =
      metadata.audio_url ||
      metadata.download_url ||
      metadata.preview_url ||
      metadata.src ||
      (metadata.audio_bucket && metadata.audio_path
        ? `supabase://${metadata.audio_bucket}/${metadata.audio_path}`
        : "") ||
      resolvedMusic.track.full_track_url ||
      resolvedMusic.track.preview_url ||
      "";
    if (!trackUrl) {
      resolvedMusic = null;
    } else {
      const fullTrackUrl = await resolveSrc(trackUrl, {
        mediaProxy,
        tenantScope,
        allowDataAudio: true,
      });
      const sfxUrls = (
        await Promise.all(
          (metadata.sfx_urls ?? []).map((src) =>
            resolveSrc(src, { mediaProxy, tenantScope, allowDataAudio: true }),
          ),
        )
      ).filter(Boolean);
      const sanitizedMetadata = { ...metadata, sfx_urls: sfxUrls };
      for (const key of ["audio_url", "download_url", "preview_url", "src"]) {
        delete sanitizedMetadata[key];
      }
      resolvedMusic = fullTrackUrl
        ? {
            ...resolvedMusic,
            track: {
              ...resolvedMusic.track,
              full_track_url: fullTrackUrl,
              preview_url: undefined,
              raw_metadata: sanitizedMetadata,
            },
          }
        : null;
    }
  }

  let resolvedStems = renderProps.sourceStems ?? null;
  const activeStem = resolvedStems?.active ?? "vocals";
  if (resolvedStems && !["vocals", "no_vocals"].includes(activeStem)) {
    throw new StructuredRenderError(
      "render_selected_stem_unavailable",
      "The selected source stem is unavailable for rendering.",
    );
  }
  const selectedStem = resolvedStems?.[activeStem];
  if (
    resolvedStems &&
    (!selectedStem ||
      ((!selectedStem.bucket || !selectedStem.path) && !selectedStem.src))
  ) {
    throw new StructuredRenderError(
      "render_selected_stem_unavailable",
      "The selected source stem is unavailable for rendering.",
    );
  }
  for (const stemName of ["vocals", "no_vocals"]) {
    const stem = resolvedStems?.[stemName];
    if (stem?.bucket && stem?.path) {
      resolvedStems = {
        ...resolvedStems,
        [stemName]: {
          ...stem,
          src: await resolveSrc(`supabase://${stem.bucket}/${stem.path}`, {
            mediaProxy,
            tenantScope,
          }),
        },
      };
    } else if (stem?.src) {
      resolvedStems = {
        ...resolvedStems,
        [stemName]: {
          ...stem,
          src: await resolveSrc(stem.src, {
            mediaProxy,
            tenantScope,
          }),
        },
      };
    }
  }
  if (resolvedStems && !resolvedStems[activeStem]?.src) {
    throw new StructuredRenderError(
      "render_selected_stem_unavailable",
      "The selected source stem is unavailable for rendering.",
    );
  }

  return {
    sourceVideo: {
      ...renderProps.sourceVideo,
      src: sourceUrl,
    },
    timeline: renderProps.timeline,
    editPlan: resolvedEditPlan,
    captions: renderProps.captions ?? [],
    words: renderProps.words ?? [],
    assets: resolvedAssets,
    captionStyle: renderProps.captionStyle ?? null,
    templateId: renderProps.templateId ?? "founder_reel_v1",
    exportSettings: renderProps.exportSettings ?? {},
    musicSelection: resolvedMusic,
    effectsConfig: renderProps.effectsConfig ?? null,
    sourceStems: resolvedStems,
    watermark: Boolean(renderProps.watermark),
    segmentationMatte: renderProps.segmentationMatte ?? null,
  };
}

export async function downloadToFile(
  url,
  destPath,
  {
    maxBytes = Number(env("MEDIA_DOWNLOAD_MAX_BYTES", "536870912")),
    timeoutMs = Number(env("MEDIA_DOWNLOAD_TIMEOUT_MS", "120000")),
    trustedInternal = false,
    internalOrigin,
  } = {},
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, timeoutMs));
  try {
    const response = await safeFetchMedia(url, {
      signal: controller.signal,
      trustedInternal,
      ...(internalOrigin !== undefined ? { internalOrigin } : {}),
    });
    if (!response.ok) {
      throw new Error(`Failed to download media (HTTP ${response.status})`);
    }
    if (!response.body)
      throw new Error("Media download returned an empty body");
    const contentLength = Number(response.headers.get("content-length") || 0);
    if (contentLength > maxBytes) {
      throw new Error(`Media download exceeds the ${maxBytes}-byte limit`);
    }

    let downloadedBytes = 0;
    const limiter = new Transform({
      transform(chunk, _encoding, callback) {
        downloadedBytes += chunk.length;
        if (downloadedBytes > maxBytes) {
          callback(
            new Error(`Media download exceeds the ${maxBytes}-byte limit`),
          );
          return;
        }
        callback(null, chunk);
      },
    });
    await pipeline(
      Readable.fromWeb(response.body),
      limiter,
      createWriteStream(destPath, { flags: "wx" }),
    );
  } catch (error) {
    await rm(destPath, { force: true }).catch(() => undefined);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export function startValidatedMediaProxy() {
  const targets = new Map();
  let nextId = 1;
  return new Promise((resolveProxy, reject) => {
    const server = createServer(async (request, response) => {
      const target = targets.get(request.url ?? "");
      if (!target || !["GET", "HEAD"].includes(request.method ?? "")) {
        response.writeHead(404).end();
        return;
      }
      try {
        const headers = request.headers.range
          ? { range: request.headers.range }
          : undefined;
        const upstream = await safeFetchMedia(target.url, {
          headers,
          trustedInternal: target.trustedInternal,
        });
        const responseHeaders = {};
        for (const name of [
          "accept-ranges",
          "content-length",
          "content-range",
          "content-type",
        ]) {
          const value = upstream.headers.get(name);
          if (value) responseHeaders[name] = value;
        }
        response.writeHead(upstream.status, responseHeaders);
        if (request.method === "HEAD" || !upstream.body) {
          response.end();
          return;
        }
        await pipeline(Readable.fromWeb(upstream.body), response);
      } catch {
        if (!response.headersSent) response.writeHead(502);
        response.end();
      }
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      const origin = `http://127.0.0.1:${port}`;
      resolveProxy({
        // Downloads from this proxy (segmentation alpha-keying) trust exactly
        // this origin; every upstream it fetches was validated on register.
        origin,
        register(target, { trustedInternal = false } = {}) {
          const path = `/media/${nextId++}`;
          targets.set(path, { url: target, trustedInternal });
          return `${origin}${path}`;
        },
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

export function startLocalFileServer(filePath) {
  return new Promise((resolve, reject) => {
    // Same URL shape as the media proxy (/media/N): the renderer accepts
    // nothing else (apps/renderer/src/render-props-policy.ts).
    const servedPath = "/media/1";
    const server = createServer((req, res) => {
      if (
        req.url !== servedPath ||
        !["GET", "HEAD"].includes(req.method ?? "")
      ) {
        res.writeHead(404).end();
        return;
      }
      res.writeHead(200, { "Content-Type": "video/webm" });
      if (req.method === "HEAD") {
        res.end();
        return;
      }
      createReadStream(filePath).pipe(res);
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      resolve({
        url: `http://127.0.0.1:${port}${servedPath}`,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

const FFMPEG_KILL_GRACE_MS = 5_000;
const RENDERER_KILL_GRACE_MS = 10_000;
const FFMPEG_STDERR_TAIL_CHARS = 8_192;

/**
 * Run ffmpeg without blocking the event loop.
 *
 * The worker's event loop renews BullMQ job locks, sends heartbeats and polls
 * cancellation, so a long encode must never run synchronously. On timeout or
 * cancellation ffmpeg gets SIGTERM, then SIGKILL after ``killGraceMs``; the
 * promise settles only once the process has exited, so callers can safely
 * delete its working files.
 */
export function runFfmpeg(
  args,
  {
    timeoutMs,
    killGraceMs = FFMPEG_KILL_GRACE_MS,
    isCanceled = null,
    cancelPollMs = 1_000,
  } = {},
) {
  return new Promise((resolve, reject) => {
    let stderr = "";
    let failure = null;
    let settled = false;
    let killTimer = null;
    let polling = false;
    const proc = spawn("ffmpeg", args, {
      stdio: ["ignore", "ignore", "pipe"],
      // Never hand worker secrets to a process parsing untrusted media.
      env: mediaToolEnv(),
    });
    proc.stderr.setEncoding("utf8");
    proc.stderr.on("data", (chunk) => {
      stderr = (stderr + chunk).slice(-FFMPEG_STDERR_TAIL_CHARS);
    });
    const stop = (error) => {
      if (failure) return;
      failure = error;
      proc.kill("SIGTERM");
      killTimer = setTimeout(() => proc.kill("SIGKILL"), killGraceMs);
    };
    const timeoutId = setTimeout(
      () =>
        stop(
          new StructuredRenderError(
            "segmentation_timeout",
            "Background removal timed out.",
            { retryable: true },
          ),
        ),
      Math.max(1, timeoutMs),
    );
    const cancelInterval = isCanceled
      ? setInterval(async () => {
          if (polling) return;
          polling = true;
          try {
            if (await isCanceled()) {
              stop(
                new StructuredRenderError(
                  "render_cancelled",
                  "Render was canceled by the user.",
                ),
              );
            }
          } catch {
            // Keep encoding if status polling is transiently unavailable.
          } finally {
            polling = false;
          }
        }, cancelPollMs)
      : null;
    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      clearTimeout(killTimer);
      if (cancelInterval) clearInterval(cancelInterval);
      fn(value);
    };
    proc.on("error", (error) => settle(reject, error));
    proc.on("close", (code, signal) => {
      if (failure) settle(reject, failure);
      else if (code === 0) settle(resolve, { stderr });
      else {
        settle(
          reject,
          new Error(
            `ffmpeg exited with ${code ?? signal}: ${stderr.slice(-500)}`,
          ),
        );
      }
    });
  });
}

export async function applySegmentationAlphaKey({
  sourceUrl,
  matteUrl,
  featherPx,
  workDir,
  internalOrigin,
  timeoutMs = renderTimeoutMs(),
  killGraceMs = FFMPEG_KILL_GRACE_MS,
  isCanceled = null,
}) {
  await mkdir(workDir, { recursive: true });
  const sourcePath = join(workDir, "source.mp4");
  const mattePath = join(workDir, "matte.mp4");
  const keyedPath = join(workDir, "keyed.webm");
  // Source and matte come through this render's loopback media proxy, which
  // already validated and pinned the upstream. Trust exactly the proxy's
  // origin: any other URL still gets the strict public-address guard.
  await downloadToFile(sourceUrl, sourcePath, {
    trustedInternal: true,
    internalOrigin,
  });
  await downloadToFile(matteUrl, mattePath, {
    trustedInternal: true,
    internalOrigin,
  });
  const feather = Math.max(0, Math.min(20, Number(featherPx ?? 18) / 8));
  try {
    await runFfmpeg(
      [
        "-y",
        "-i",
        sourcePath,
        "-i",
        mattePath,
        "-filter_complex",
        `[1:v]format=gray,scale=iw:ih:flags=bilinear,boxblur=luma_radius=${feather.toFixed(1)}:luma_power=1[alpha];[0:v][alpha]alphamerge[out]`,
        "-map",
        "[out]",
        "-map",
        "0:a?",
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuva420p",
        "-auto-alt-ref",
        "0",
        keyedPath,
      ],
      { timeoutMs, killGraceMs, isCanceled },
    );
  } catch (error) {
    if (error instanceof StructuredRenderError) throw error;
    throw new Error(
      `Failed to alpha-key source for segmentation: ${error.message}`,
    );
  }
  if (!(await stat(keyedPath).catch(() => null))) {
    throw new Error(
      "Failed to alpha-key source for segmentation: no keyed output was written",
    );
  }
  return startLocalFileServer(keyedPath);
}

// ---------------------------------------------------------------------------
// Renderer subprocess
// ---------------------------------------------------------------------------

/**
 * Spawn render-cli.ts and wait for it to exit 0.
 * stdout/stderr are streamed to process.stdout/stderr for service logs.
 */
export function spawnRenderer(
  propsPath,
  outputPath,
  {
    renderJobId,
    workspaceId,
    projectId,
    timeoutMs,
    killGraceMs = RENDERER_KILL_GRACE_MS,
    isCanceled = () =>
      loadRenderJobStatusDirect(renderJobId, workspaceId, projectId).then(
        (status) => status === "canceled",
      ),
    cancelPollMs = 1000,
  },
) {
  return new Promise((resolve, reject) => {
    const rPath = rendererPath();
    const args = [
      "--import",
      "./ts-node-register.mjs",
      "src/render-cli.ts",
      "--props",
      propsPath,
      "--out",
      outputPath,
    ];

    console.log(
      JSON.stringify({
        event: "remotion_render_start",
        renderer_path: rPath,
        output_path: outputPath,
      }),
    );

    const proc = spawn("node", args, {
      cwd: rPath,
      stdio: ["ignore", "pipe", "pipe"],
      // Own process group: Chromium runs as a grandchild, so stopping the
      // render signals the whole group, not only render-cli.
      detached: process.platform !== "win32",
      // Allowlisted: Remotion runs Chromium unsandboxed over user-controlled
      // props, so the renderer must not inherit the worker's secrets
      // (Supabase key, worker token, Redis URL, provider keys).
      env: rendererEnv(process.env, {
        TS_NODE_PROJECT: join(rPath, "tsconfig.json"),
      }),
    });

    // Line-buffered and credential-redacted (render props carry signed URLs).
    const stdoutWriter = createRedactingLineWriter((line) =>
      process.stdout.write(line),
    );
    const stderrWriter = createRedactingLineWriter((line) =>
      process.stderr.write(line),
    );
    proc.stdout.on("data", (data) => stdoutWriter.write(data));
    proc.stderr.on("data", (data) => stderrWriter.write(data));
    proc.on("close", () => {
      stdoutWriter.flush();
      stderrWriter.flush();
    });

    let settled = false;
    let failure = null;
    let killTimer = null;
    let polling = false;
    const signalRenderer = (signal) => {
      try {
        if (process.platform !== "win32" && proc.pid) {
          process.kill(-proc.pid, signal);
        } else {
          proc.kill(signal);
        }
      } catch {
        // Already exited.
      }
    };
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      clearTimeout(killTimer);
      clearInterval(cancelInterval);
      fn(value);
    };
    // Ask the renderer to stop, force it after killGraceMs, and settle only
    // once it has exited: the caller then deletes its temp files and frees
    // the render slot, which must not happen while Chromium is still running.
    const stop = (error) => {
      if (failure) return;
      failure = error;
      signalRenderer("SIGTERM");
      killTimer = setTimeout(() => signalRenderer("SIGKILL"), killGraceMs);
    };
    const timeoutId = setTimeout(
      () =>
        stop(
          new StructuredRenderError(
            "render_timeout",
            "Premium render timed out.",
            { retryable: true },
          ),
        ),
      timeoutMs,
    );
    const cancelInterval = setInterval(async () => {
      if (polling || failure) return;
      polling = true;
      try {
        if (await isCanceled()) {
          stop(
            new StructuredRenderError(
              "render_cancelled",
              "Render was canceled by the user.",
            ),
          );
        }
      } catch {
        // Leave the render running if status polling is transiently unavailable.
      } finally {
        polling = false;
      }
    }, cancelPollMs);

    proc.on("close", (code) => {
      if (failure) {
        // The CLI is gone; make sure nothing it started survives it.
        signalRenderer("SIGKILL");
        finish(reject, failure);
      } else if (code === 0) finish(resolve);
      else
        finish(
          reject,
          new StructuredRenderError(
            "retryable_worker_failure",
            `render-cli.ts exited with code ${code}`,
            { retryable: true },
          ),
        );
    });

    proc.on("error", (err) => {
      finish(
        reject,
        new StructuredRenderError(
          "retryable_worker_failure",
          `Failed to spawn render-cli.ts: ${err.message}`,
          { retryable: true },
        ),
      );
    });
  });
}

/**
 * Read the {output}.json metadata file written by render-cli.ts.
 * Returns render duration, video dimensions, fps, and file size.
 */

function assertEncodedOutputMatchesSpecs(meta, specs) {
  const expected = specs && typeof specs === "object" ? specs : {};
  const actualValues = [
    Number(meta.width),
    Number(meta.height),
    Number(meta.fps),
  ];
  const expectedValues = [
    Number(expected.width),
    Number(expected.height),
    Number(expected.fps),
  ];
  if (
    !actualValues.every(Number.isFinite) ||
    !expectedValues.every(Number.isFinite) ||
    actualValues.some((value, index) => value !== expectedValues[index])
  ) {
    throw new StructuredRenderError(
      "render_output_specs_mismatch",
      `Encoded output ${meta.width}x${meta.height}@${meta.fps} does not match requested ${expected.width}x${expected.height}@${expected.fps}.`,
    );
  }
}
async function readRenderMeta(outputPath) {
  const metaPath = `${outputPath}.json`;
  let meta = {};
  try {
    meta = JSON.parse(await readFile(metaPath, "utf8"));
  } catch {
    // Meta file is non-critical — fall back to defaults
  }
  const fileStat = await stat(outputPath);
  return {
    render_time_ms: meta.render_time_ms ?? meta.duration_ms ?? 0,
    video_duration_ms: meta.video_duration_ms ?? null,
    width: meta.width ?? 1080,
    height: meta.height ?? 1920,
    fps: meta.fps ?? 30,
    file_size_bytes: fileStat.size,
  };
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/**
 * Called by worker.mjs when job.render_engine === "remotion_premium".
 *
 * @param {object} job - The queue job record (from BullMQ)
 * @param {object} ctx - optional progress callback
 */
export async function runRemotionJob(
  job,
  { onProgress = null, queueIdentity = null } = {},
) {
  const renderJobId = String(job.resource_id);
  const workspaceId = String(job.workspace_id);
  const projectId = String(job.project_id);
  const tenantScope = { workspaceId, projectId };
  const attemptPaths = attemptStoragePaths({
    workspaceId,
    projectId,
    renderJobId,
    queueJobId: queueIdentity?.id,
    attemptsMade: queueIdentity?.attemptsMade,
  });

  const propsPath = `/tmp/motionly-props-${attemptPaths.attemptId}.json`;
  const outputPath = `/tmp/motionly-output-${attemptPaths.attemptId}.mp4`;
  const keyedWorkDir = `/tmp/motionly-keyed-${attemptPaths.attemptId}`;
  const exportBucket = env("EXPORT_STORAGE_BUCKET", "exports");
  const client = supabaseClient();

  const started = Date.now();
  let keyedServer = null;
  let mediaProxy = null;
  const progress = async (
    progressPct,
    message,
    eventType,
    metadata = {},
    status = "rendering",
  ) => {
    const payload = { progressPct, message, eventType, metadata, status };
    const applied = await persistProgressDirect(
      renderJobId,
      workspaceId,
      projectId,
      payload,
      client,
    );
    if (applied && onProgress) {
      await onProgress(payload);
    }
  };

  try {
    await progress(
      10,
      "Running premium render preflight.",
      "preflight_started",
      {},
      "preparing",
    );
    validateBrowserExecutable();
    await validateWritableTempDir(keyedWorkDir);
    // 1. Fetch the full render job manifest directly from the database.
    const jobRecord = await loadRenderJobDirect(
      renderJobId,
      workspaceId,
      projectId,
      client,
    );
    const renderProps = jobRecord.manifest?.render_props;
    if (!renderProps) {
      throw new StructuredRenderError(
        "invalid_remotion_props",
        "Render job manifest is missing render props.",
      );
    }

    // 2. Revalidate and resolve all media references to signed / guarded URLs.
    await progress(
      25,
      "Resolving render media assets.",
      "render_progress",
      {},
      "preparing",
    );
    validateRenderTenantScope(renderProps, tenantScope);
    await validateRenderMediaManifest(renderProps, {
      allowFixture: fixtureMediaAllowed(),
      requireDns: true,
    });
    mediaProxy = await startValidatedMediaProxy();
    const resolvedProps = await assembleRenderProps(
      renderProps,
      mediaProxy,
      tenantScope,
    );
    validateResolvedProps(resolvedProps);
    const backgroundRemoval = resolvedProps.effectsConfig?.background_removal;
    const segmentationMatte = renderProps.segmentationMatte;
    if (backgroundRemoval?.enabled && segmentationMatte?.src) {
      const matteUrl = await resolveSrc(segmentationMatte.src, {
        mediaProxy,
        tenantScope,
      });
      if (!matteUrl) {
        throw new Error(
          "segmentation matte could not be resolved for premium render",
        );
      }
      keyedServer = await applySegmentationAlphaKey({
        sourceUrl: resolvedProps.sourceVideo.src,
        matteUrl,
        featherPx: backgroundRemoval.feather_px,
        workDir: keyedWorkDir,
        internalOrigin: mediaProxy.origin,
        timeoutMs: renderTimeoutMs(),
        isCanceled: async () =>
          (await loadRenderJobStatusDirect(
            renderJobId,
            workspaceId,
            projectId,
            client,
          )) === "canceled",
      });
      resolvedProps.sourceVideo = {
        ...resolvedProps.sourceVideo,
        src: keyedServer.url,
        has_alpha: true,
      };
    }

    // 3. Write props to temp file.
    await writeFile(propsPath, JSON.stringify(resolvedProps, null, 2));

    // 4. Spawn render-cli.ts. It polls cancellation while rendering.
    await progress(
      45,
      "Rendering premium video composition.",
      "rendering_started",
    );
    await spawnRenderer(propsPath, outputPath, {
      renderJobId,
      workspaceId,
      projectId,
      timeoutMs: renderTimeoutMs(),
    });

    // 5. Check authoritative cancellation after rendering and before upload.
    const meta = await readRenderMeta(outputPath);
    assertEncodedOutputMatchesSpecs(meta, jobRecord.specs);
    await assertRenderCompletableDirect(
      renderJobId,
      workspaceId,
      projectId,
      client,
    );
    const benchmarkMs = Date.now() - started;

    // 6. Stage only this BullMQ attempt's output, then recheck before it can
    // become the attempt's final artifact or be published by the CAS RPC.
    await progress(
      88,
      "Uploading premium export.",
      "upload_started",
      {},
      "uploading",
    );
    let attemptArtifactUploaded = false;
    try {
      await uploadToStorage(
        outputPath,
        exportBucket,
        attemptPaths.stagingPath,
        client,
      );
      attemptArtifactUploaded = true;
      await assertRenderCompletableDirect(
        renderJobId,
        workspaceId,
        projectId,
        client,
      );
      await promoteAttemptArtifact(client, exportBucket, attemptPaths);
      await assertRenderCompletableDirect(
        renderJobId,
        workspaceId,
        projectId,
        client,
      );

      const result = await finalizeAttemptWithCas({
        client,
        renderJobId,
        workspaceId,
        projectId,
        artifactBucket: exportBucket,
        attemptPaths,
        completionDetails: {
          benchmarkMs,
          fileSizeBytes: meta.file_size_bytes,
          width: meta.width,
          height: meta.height,
          fps: meta.fps,
          durationMs: meta.video_duration_ms,
        },
      });

      console.log(
        JSON.stringify({
          event: "remotion_render_complete",
          render_job_id: renderJobId,
          export_id: result.export_id,
          attempt_id: attemptPaths.attemptId,
          benchmark_ms: benchmarkMs,
          file_size_bytes: meta.file_size_bytes,
        }),
      );
      return result;
    } catch (error) {
      if (attemptArtifactUploaded) {
        await cleanupAttemptIfLost({
          client,
          artifactBucket: exportBucket,
          attemptPaths,
          renderJobId,
          workspaceId,
          projectId,
          error,
        });
      }
      throw error;
    }
  } finally {
    if (keyedServer) {
      await keyedServer.close();
    }
    if (mediaProxy) {
      await mediaProxy.close();
    }
    // 7. Clean up only local attempt files (storage cleanup is deliberately
    // scoped to this attempt and never touches a canonical winner).
    await Promise.allSettled([
      rm(propsPath, { force: true }),
      rm(outputPath, { force: true }),
      rm(`${outputPath}.json`, { force: true }),
      rm(keyedWorkDir, { recursive: true, force: true }),
    ]);
  }
}
