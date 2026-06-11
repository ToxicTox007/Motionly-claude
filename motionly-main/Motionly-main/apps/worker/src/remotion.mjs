/**
 * remotion.mjs
 * -----------
 * Handles render jobs with render_engine = "remotion_premium".
 *
 * Flow:
 *   1. Fetch the full render job manifest from the API
 *   2. Resolve all supabase:// media references to signed URLs
 *   3. Filter fixture:// assets (not renderable in production)
 *   4. Write the assembled MotionlyRenderProps to a temp JSON file
 *   5. Spawn the Remotion CLI: node --loader ts-node/esm src/render-cli.ts
 *   6. Read the render metadata written by the CLI ({output}.json)
 *   7. Upload the rendered MP4 to Supabase Storage
 *   8. POST /internal/renders/{id}/complete to finalise the job record
 *   9. Clean up temp files
 */

import { spawn } from "node:child_process";
import { readFile, rm, stat, writeFile } from "node:fs/promises";
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
  const buffer = await readFile(localPath);
  const supabase = supabaseClient();
  const { error } = await supabase.storage
    .from(storageBucket)
    .upload(storagePath, buffer, {
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
 *   - fixture://   → return null (will be filtered out)
 */
async function resolveSrc(src) {
  if (!src) return null;
  if (src.startsWith("fixture://")) return null;
  if (src.startsWith("supabase://")) return signSupabaseSrc(src);
  return src; // already a real HTTP(S) URL
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
        if (!src) return null; // fixture — drop silently
        return { ...asset, src };
      }),
    )
  ).filter(Boolean);

  // --- Music selection ---
  // Drop the music selection entirely if the track URL is fixture-based,
  // since there is no real audio file to embed.
  let resolvedMusic = renderProps.musicSelection ?? null;
  if (resolvedMusic?.track) {
    const trackUrl =
      resolvedMusic.track.full_track_url ||
      resolvedMusic.track.preview_url ||
      "";
    if (trackUrl.startsWith("fixture://") || !trackUrl.startsWith("http")) {
      resolvedMusic = null; // silently drop fixture music
    }
  }

  return {
    sourceVideo: {
      ...renderProps.sourceVideo,
      src: sourceUrl,
    },
    editPlan: renderProps.editPlan ?? { schema_version: 1, cuts: [] },
    captions: renderProps.captions ?? [],
    words: renderProps.words ?? [],
    assets: resolvedAssets,
    templateId: renderProps.templateId ?? "founder_reel_v1",
    exportSettings: renderProps.exportSettings ?? {},
    musicSelection: resolvedMusic,
    effectsConfig: renderProps.effectsConfig ?? null,
  };
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
// API completion report
// ---------------------------------------------------------------------------

async function reportComplete(
  apiBaseUrl,
  workerToken,
  renderJobId,
  workspaceId,
  projectId,
  {
    exportStoragePath,
    artifactBucket,
    benchmarkMs,
    fileSizeBytes,
    width,
    height,
    fps,
    durationMs,
  },
) {
  const url = `${apiBaseUrl}/internal/renders/${renderJobId}/complete`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-motionly-worker-token": workerToken,
    },
    body: JSON.stringify({
      workspace_id: workspaceId,
      project_id: projectId,
      export_storage_path: exportStoragePath,
      artifact_bucket: artifactBucket,
      benchmark_ms: benchmarkMs,
      file_size_bytes: fileSizeBytes,
      width,
      height,
      fps,
      duration_ms: durationMs,
    }),
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(
      `complete-remotion API call failed (${response.status}): ${body.slice(0, 400)}`,
    );
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/**
 * Called by worker.mjs when job.render_engine === "remotion_premium".
 *
 * @param {object} job - The queue job record (from Upstash)
 * @param {object} ctx - { apiBaseUrl: string, workerToken: string }
 */
export async function runRemotionJob(job, { apiBaseUrl, workerToken }) {
  const renderJobId = String(job.resource_id);
  const workspaceId = String(job.workspace_id);
  const projectId = String(job.project_id);

  const propsPath = `/tmp/motionly-props-${renderJobId}.json`;
  const outputPath = `/tmp/motionly-output-${renderJobId}.mp4`;
  const exportBucket = env("EXPORT_STORAGE_BUCKET", "exports");
  const exportStoragePath = `workspaces/${workspaceId}/projects/${projectId}/exports/${renderJobId}.mp4`;

  const started = Date.now();

  try {
    // 1. Fetch the full render job manifest from the API
    const manifestRes = await fetch(
      `${apiBaseUrl}/internal/renders/${renderJobId}` +
        `?workspace_id=${workspaceId}&project_id=${projectId}`,
      {
        headers: { "x-motionly-worker-token": workerToken },
      },
    );
    if (!manifestRes.ok) {
      const body = await manifestRes.text().catch(() => "");
      throw new Error(
        `Failed to fetch render job manifest (${manifestRes.status}): ${body.slice(0, 400)}`,
      );
    }
    const jobRecord = await manifestRes.json();
    const renderProps = jobRecord.manifest?.render_props;
    if (!renderProps) {
      throw new Error("render job manifest is missing render_props");
    }

    // 2. Resolve all media references to signed / real URLs
    const resolvedProps = await assembleRenderProps(renderProps);

    // 3. Write props to temp file
    await writeFile(propsPath, JSON.stringify(resolvedProps, null, 2));

    // 4. Spawn render-cli.ts
    await spawnRenderer(propsPath, outputPath);

    // 5. Read render metadata
    const meta = await readRenderMeta(outputPath);
    const benchmarkMs = Date.now() - started;

    // 6. Upload rendered MP4 to Supabase Storage
    await uploadToStorage(outputPath, exportBucket, exportStoragePath);

    // 7. Report completion to API
    const result = await reportComplete(
      apiBaseUrl,
      workerToken,
      renderJobId,
      workspaceId,
      projectId,
      {
        exportStoragePath,
        artifactBucket: exportBucket,
        benchmarkMs,
        fileSizeBytes: meta.file_size_bytes,
        width: meta.width,
        height: meta.height,
        fps: meta.fps,
        durationMs: meta.video_duration_ms,
      },
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
    // 8. Clean up temp files (best-effort)
    await Promise.allSettled([
      rm(propsPath, { force: true }),
      rm(outputPath, { force: true }),
      rm(`${outputPath}.json`, { force: true }),
    ]);
  }
}
