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
 *   5. Spawn the Remotion CLI: node --loader ts-node/esm src/render-cli.ts
 *   6. Read the render metadata written by the CLI ({output}.json)
 *   7. Upload the rendered MP4 to Supabase Storage
 *   8. Finalise the job/export records directly in Supabase
 *   9. Clean up temp files
 */

import { spawn, spawnSync } from "node:child_process";
import { createReadStream } from "node:fs";
import { createServer } from "node:http";
import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createClient } from "@supabase/supabase-js";

// ---------------------------------------------------------------------------
// Environment helpers
// ---------------------------------------------------------------------------

function env(name, fallback = "") {
  return process.env[name] || fallback;
}

/** Renderer base directory. Defaults to the sibling 'renderer' package. */
function rendererPath() {
  return resolve(env("RENDERER_BASE_PATH", join(import.meta.dirname, "../../renderer")));
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

async function loadRenderJobDirect(renderJobId, workspaceId, projectId) {
  const { data, error } = await supabaseClient()
    .from("render_jobs")
    .select("*")
    .eq("id", renderJobId)
    .eq("workspace_id", workspaceId)
    .eq("project_id", projectId)
    .single();
  if (error || !data) throw new Error(`Failed to load render job: ${error?.message || "missing"}`);
  return data;
}

async function persistProgressDirect(renderJobId, workspaceId, projectId, payload) {
  const client = supabaseClient();
  const { error } = await client
    .from("render_jobs")
    .update({ status: "rendering", progress_pct: payload.progressPct })
    .eq("id", renderJobId)
    .eq("workspace_id", workspaceId)
    .eq("project_id", projectId);
  if (error) throw new Error(`Failed to persist render progress: ${error.message}`);
  await client.from("render_job_events").insert({
    render_job_id: renderJobId,
    workspace_id: workspaceId,
    project_id: projectId,
    event_type: payload.eventType || "worker_progress",
    to_status: "rendering",
    progress_pct: payload.progressPct,
    message: payload.message,
    metadata: payload.metadata || {},
  });
}

async function persistCompleteDirect(renderJobId, workspaceId, projectId, details) {
  const client = supabaseClient();
  const { data: existing } = await client
    .from("exports")
    .select("id")
    .eq("render_job_id", renderJobId)
    .limit(1)
    .maybeSingle();
  let exportId = existing?.id || null;
  if (!exportId) {
    const { data: latest } = await client
      .from("exports")
      .select("version")
      .eq("project_id", projectId)
      .order("version", { ascending: false })
      .limit(1)
      .maybeSingle();
    const { data: created, error: exportError } = await client
      .from("exports")
      .insert({
        workspace_id: workspaceId,
        project_id: projectId,
        render_job_id: renderJobId,
        version: Number(latest?.version || 0) + 1,
        storage_bucket: details.artifactBucket,
        storage_path: details.exportStoragePath,
        format: "mp4",
        width: details.width,
        height: details.height,
        fps: details.fps,
        duration_ms: details.durationMs,
        file_size_bytes: details.fileSizeBytes,
      })
      .select("id")
      .single();
    if (exportError) throw new Error(`Failed to create export: ${exportError.message}`);
    exportId = created.id;
  }
  const { error } = await client
    .from("render_jobs")
    .update({
      status: "complete",
      progress_pct: 100,
      artifact_path: details.exportStoragePath,
      benchmark_ms: details.benchmarkMs,
      error_code: null,
      error_message: null,
    })
    .eq("id", renderJobId)
    .eq("workspace_id", workspaceId)
    .eq("project_id", projectId);
  if (error) throw new Error(`Failed to complete render job: ${error.message}`);
  await client.from("render_job_events").insert({
    render_job_id: renderJobId,
    workspace_id: workspaceId,
    project_id: projectId,
    event_type: "complete",
    to_status: "complete",
    progress_pct: 100,
    message: "Premium render completed by the Remotion worker.",
    metadata: { export_id: exportId },
  });
  return { render_job_id: renderJobId, export_id: exportId, status: "complete" };
}

export async function persistRenderFailureDirect(job, errorMessage, retrying) {
  const client = supabaseClient();
  const status = retrying ? "queued" : "failed";
  const { error } = await client
    .from("render_jobs")
    .update({
      status,
      progress_pct: retrying ? 5 : undefined,
      error_code: retrying ? null : "render_worker_failed",
      error_message: retrying ? null : errorMessage.slice(0, 500),
    })
    .eq("id", job.resource_id)
    .eq("workspace_id", job.workspace_id)
    .eq("project_id", job.project_id);
  if (error) throw new Error(`Failed to persist render failure: ${error.message}`);
  await client.from("render_job_events").insert({
    render_job_id: job.resource_id,
    workspace_id: job.workspace_id,
    project_id: job.project_id,
    event_type: retrying ? "worker_retrying" : "worker_failed",
    to_status: status,
    progress_pct: retrying ? 5 : null,
    message: retrying ? "Render worker failed; BullMQ will retry." : errorMessage.slice(0, 500),
    metadata: {},
  });
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

/**
 * Create a signed download URL for a supabase:// reference.
 * TTL: 2 hours (sufficient for any realistic render job).
 */
async function signSupabaseSrc(src) {
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
async function uploadToStorage(localPath, storageBucket, storagePath) {
  const stream = createReadStream(localPath);
  const supabase = supabaseClient();
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

// ---------------------------------------------------------------------------
// Props assembly
// ---------------------------------------------------------------------------

/**
 * Resolve a media src value to a real HTTP URL:
 *   - supabase://  → sign via Supabase Storage API
 *   - https://     → pass through (Pexels, Pixabay, etc.)
 *   - fixture://   → allowed only in non-production/demo mode
 */
async function resolveSrc(src) {
  if (!src) return null;
  if (src.startsWith("fixture://")) {
    if (fixtureMediaAllowed()) return null;
    throw new Error("Fixture media is not allowed for production rendering.");
  }
  if (src.startsWith("supabase://")) return signSupabaseSrc(src);
  return src; // already a real HTTP(S) URL
}

function fixtureMediaAllowed() {
  return (
    env("APP_ENV") !== "production" ||
    env("MOTIONLY_DEMO_MODE") === "true" ||
    env("ALLOW_FIXTURE_PROVIDERS") === "true"
  );
}

/**
 * Convert the stored render_props (which contains supabase:// and fixture://
 * references) into a MotionlyRenderProps dict ready for render-cli.ts.
 *
 * All fixture:// references are dropped. supabase:// references are converted
 * to short-lived signed URLs. Real https:// references are passed through.
 */
async function assembleRenderProps(renderProps) {
  // --- Source video ---
  const sourceUrl = await resolveSrc(renderProps.sourceVideo?.src);
  if (!sourceUrl) {
    throw new Error(
      "sourceVideo.src is a fixture:// reference — cannot render in production.",
    );
  }

  // --- B-roll assets ---
  const resolvedAssets = (
    await Promise.all(
      (renderProps.assets ?? []).map(async (asset) => {
        const src = await resolveSrc(asset.src);
        if (!src) return null; // development/demo fixture asset
        return { ...asset, src };
      }),
    )
  ).filter(Boolean);

  // --- Music selection ---
  // Resolve production catalog music from response fields or raw metadata.
  let resolvedMusic = renderProps.musicSelection ?? null;
  if (resolvedMusic?.track) {
    const trackUrl =
      resolvedMusic.track.raw_metadata?.audio_url ||
      resolvedMusic.track.raw_metadata?.download_url ||
      resolvedMusic.track.raw_metadata?.src ||
      (resolvedMusic.track.raw_metadata?.audio_bucket &&
      resolvedMusic.track.raw_metadata?.audio_path
        ? `supabase://${resolvedMusic.track.raw_metadata.audio_bucket}/${resolvedMusic.track.raw_metadata.audio_path}`
        : "") ||
      resolvedMusic.track.full_track_url ||
      resolvedMusic.track.preview_url ||
      "";
    if (trackUrl.startsWith("fixture://")) {
      if (!fixtureMediaAllowed()) {
        throw new Error("Fixture music is not allowed for production rendering.");
      }
      resolvedMusic = null;
    } else if (!trackUrl.startsWith("http")) {
      if (trackUrl.startsWith("supabase://")) {
        const signedTrackUrl = await resolveSrc(trackUrl);
        resolvedMusic = {
          ...resolvedMusic,
          track: {
            ...resolvedMusic.track,
            full_track_url: signedTrackUrl,
          },
        };
      } else {
        resolvedMusic = null;
      }
    } else {
      resolvedMusic = {
        ...resolvedMusic,
        track: {
          ...resolvedMusic.track,
          full_track_url: trackUrl,
        },
      };
    }
  }

  let resolvedStems = renderProps.sourceStems ?? null;
  if (resolvedStems?.vocals?.bucket && resolvedStems?.vocals?.path) {
    resolvedStems = {
      ...resolvedStems,
      vocals: {
        ...resolvedStems.vocals,
        src: await resolveSrc(
          `supabase://${resolvedStems.vocals.bucket}/${resolvedStems.vocals.path}`,
        ),
      },
    };
  }

  return {
    sourceVideo: {
      ...renderProps.sourceVideo,
      src: sourceUrl,
    },
    timeline: renderProps.timeline,
    editPlan: renderProps.editPlan ?? { schema_version: 1, cuts: [] },
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

async function downloadToFile(url, destPath) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to download media (${response.status}): ${url}`);
  }
  await writeFile(destPath, Buffer.from(await response.arrayBuffer()));
}

function startLocalFileServer(filePath) {
  return new Promise((resolve, reject) => {
    const server = createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "video/webm" });
      createReadStream(filePath).pipe(res);
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      resolve({
        url: `http://127.0.0.1:${port}/keyed.webm`,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

async function applySegmentationAlphaKey({ sourceUrl, matteUrl, featherPx, workDir }) {
  await mkdir(workDir, { recursive: true });
  const sourcePath = join(workDir, "source.mp4");
  const mattePath = join(workDir, "matte.mp4");
  const keyedPath = join(workDir, "keyed.webm");
  await downloadToFile(sourceUrl, sourcePath);
  await downloadToFile(matteUrl, mattePath);
  const feather = Math.max(0, Math.min(20, Number(featherPx ?? 18) / 8));
  const result = spawnSync(
    "ffmpeg",
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
    { encoding: "utf8" },
  );
  if (result.status !== 0 || !(await stat(keyedPath).catch(() => null))) {
    throw new Error(
      `Failed to alpha-key source for segmentation: ${(result.stderr || "").slice(-500)}`,
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
function spawnRenderer(propsPath, outputPath) {
  return new Promise((resolve, reject) => {
    const rPath = rendererPath();
    const args = [
      "--loader",
      "ts-node/esm",
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
      env: {
        ...process.env,
        TS_NODE_PROJECT: join(rPath, "tsconfig.json"),
        // Let Remotion find Chrome in Docker containers
        ...(process.env.BROWSER_EXECUTABLE_PATH && {
          BROWSER_EXECUTABLE_PATH: process.env.BROWSER_EXECUTABLE_PATH,
        }),
        ...(process.env.PUPPETEER_EXECUTABLE_PATH && {
          PUPPETEER_EXECUTABLE_PATH: process.env.PUPPETEER_EXECUTABLE_PATH,
        }),
      },
    });

    proc.stdout.on("data", (data) => process.stdout.write(data));
    proc.stderr.on("data", (data) => process.stderr.write(data));

    proc.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`render-cli.ts exited with code ${code}`));
    });

    proc.on("error", (err) => {
      reject(new Error(`Failed to spawn render-cli.ts: ${err.message}`));
    });
  });
}

/**
 * Read the {output}.json metadata file written by render-cli.ts.
 * Returns render duration, video dimensions, fps, and file size.
 */
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
export async function runRemotionJob(job, { onProgress = null } = {}) {
  const renderJobId = String(job.resource_id);
  const workspaceId = String(job.workspace_id);
  const projectId = String(job.project_id);

  const propsPath = `/tmp/motionly-props-${renderJobId}.json`;
  const outputPath = `/tmp/motionly-output-${renderJobId}.mp4`;
  const keyedWorkDir = `/tmp/motionly-keyed-${renderJobId}`;
  const exportBucket = env("EXPORT_STORAGE_BUCKET", "exports");
  const exportStoragePath = `workspaces/${workspaceId}/projects/${projectId}/exports/${renderJobId}.mp4`;

  const started = Date.now();
  let keyedServer = null;
  const progress = async (progressPct, message, eventType, metadata = {}) => {
    const payload = { progressPct, message, eventType, metadata };
    await persistProgressDirect(renderJobId, workspaceId, projectId, payload);
    if (onProgress) {
      await onProgress(payload);
    }
  };

  try {
    await progress(20, "Preparing premium render manifest.", "remotion_prepare");
    // 1. Fetch the full render job manifest directly from the database.
    const jobRecord = await loadRenderJobDirect(renderJobId, workspaceId, projectId);
    const renderProps = jobRecord.manifest?.render_props;
    if (!renderProps) {
      throw new Error("render job manifest is missing render_props");
    }

    // 2. Resolve all media references to signed / real URLs
    await progress(30, "Resolving render media assets.", "remotion_assets");
    const resolvedProps = await assembleRenderProps(renderProps);
    const backgroundRemoval = resolvedProps.effectsConfig?.background_removal;
    const segmentationMatte = renderProps.segmentationMatte;
    if (backgroundRemoval?.enabled && segmentationMatte?.src) {
      const matteUrl = await resolveSrc(segmentationMatte.src);
      if (!matteUrl) {
        throw new Error("segmentation matte could not be resolved for premium render");
      }
      keyedServer = await applySegmentationAlphaKey({
        sourceUrl: resolvedProps.sourceVideo.src,
        matteUrl,
        featherPx: backgroundRemoval.feather_px,
        workDir: keyedWorkDir,
      });
      resolvedProps.sourceVideo = {
        ...resolvedProps.sourceVideo,
        src: keyedServer.url,
        has_alpha: true,
      };
    }

    // 3. Write props to temp file
    await writeFile(propsPath, JSON.stringify(resolvedProps, null, 2));

    // 4. Spawn render-cli.ts
    await progress(45, "Rendering premium video composition.", "remotion_rendering");
    await spawnRenderer(propsPath, outputPath);

    // 5. Read render metadata
    const meta = await readRenderMeta(outputPath);
    const benchmarkMs = Date.now() - started;

    // 6. Upload rendered MP4 to Supabase Storage
    await progress(88, "Uploading premium export.", "remotion_uploading");
    await uploadToStorage(outputPath, exportBucket, exportStoragePath);

    // 7. Persist completion directly; the API is not part of the media path.
    const completionDetails = {
      exportStoragePath,
      artifactBucket: exportBucket,
      benchmarkMs,
      fileSizeBytes: meta.file_size_bytes,
      width: meta.width,
      height: meta.height,
      fps: meta.fps,
      durationMs: meta.video_duration_ms,
    };
    const result = await persistCompleteDirect(
      renderJobId, workspaceId, projectId, completionDetails,
    );

    console.log(
      JSON.stringify({
        event: "remotion_render_complete",
        render_job_id: renderJobId,
        export_id: result.export_id,
        benchmark_ms: benchmarkMs,
        file_size_bytes: meta.file_size_bytes,
      }),
    );

    return result;
  } finally {
    if (keyedServer) {
      await keyedServer.close();
    }
    // 8. Clean up temp files (best-effort)
    await Promise.allSettled([
      rm(propsPath, { force: true }),
      rm(outputPath, { force: true }),
      rm(`${outputPath}.json`, { force: true }),
      rm(keyedWorkDir, { recursive: true, force: true }),
    ]);
  }
}
