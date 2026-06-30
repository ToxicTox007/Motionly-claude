import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

async function main() {
  const browserPath =
    process.env.BROWSER_EXECUTABLE_PATH || process.env.PUPPETEER_EXECUTABLE_PATH;
  if (!browserPath) {
    throw new Error("Chromium smoke test requires BROWSER_EXECUTABLE_PATH or PUPPETEER_EXECUTABLE_PATH.");
  }

  const workDir = await mkdtemp(join(tmpdir(), "motionly-chromium-smoke-"));
  const sourcePath = join(workDir, "source.mp4");
  const propsPath = join(workDir, "props.json");
  const outputPath = join(workDir, "output.mp4");

  const source = spawnSync(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-f",
      "lavfi",
      "-i",
      "color=c=black:s=320x180:d=1",
      "-f",
      "lavfi",
      "-i",
      "anullsrc=channel_layout=stereo:sample_rate=44100",
      "-shortest",
      "-r",
      "30",
      "-c:v",
      "libx264",
      "-pix_fmt",
      "yuv420p",
      "-c:a",
      "aac",
      sourcePath,
    ],
    { encoding: "utf8" },
  );
  if (source.status !== 0) {
    throw new Error(`Failed to generate smoke source video: ${source.stderr}`);
  }

  await writeFile(
    propsPath,
    JSON.stringify(
      {
        sourceVideo: { src: sourcePath, width: 320, height: 180, duration_ms: 1000 },
        editPlan: {
          schema_version: 1,
          style: "founder",
          target_duration_s: 1,
          cuts: [{ start_ms: 0, end_ms: 1000, reason: "smoke" }],
          caption_highlights: [],
          broll_requests: [],
          zoom_moments: [],
          music_mood: "none",
          effects: [],
          color_grade: "clean_founder",
          risk_flags: [],
          manual_elements: [],
        },
        captions: [],
        words: [],
        assets: [],
        templateId: "founder_reel_v1",
        exportSettings: {
          width: 320,
          height: 180,
          fps: 30,
          video_codec: "h264",
          audio_codec: "aac",
          pixel_format: "yuv420p",
          format: "mp4",
        },
      },
      null,
      2,
    ),
  );

  const render = spawnSync(
    "node",
    ["--loader", "ts-node/esm", "src/render-cli.ts", "--props", propsPath, "--out", outputPath],
    {
      cwd: resolve("."),
      encoding: "utf8",
      env: process.env,
    },
  );
  if (render.status !== 0) {
    throw new Error(`Chromium smoke render failed: ${render.stderr || render.stdout}`);
  }

  const outputStats = await stat(outputPath);
  assert.ok(outputStats.size > 0, "Smoke render output should be non-empty");

  const metadata = JSON.parse(await readFile(`${outputPath}.json`, "utf8"));
  assert.equal(metadata.composition_id, "MotionlyPremium");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
