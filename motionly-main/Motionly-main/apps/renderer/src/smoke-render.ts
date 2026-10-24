import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createReadStream } from "node:fs";
import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

async function main() {
  const browserPath =
    process.env.BROWSER_EXECUTABLE_PATH ||
    process.env.PUPPETEER_EXECUTABLE_PATH;
  if (!browserPath) {
    throw new Error(
      "Chromium smoke test requires BROWSER_EXECUTABLE_PATH or PUPPETEER_EXECUTABLE_PATH.",
    );
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

  const server = createRangeServer(sourcePath);
  await new Promise<void>((ready) => server.listen(0, "127.0.0.1", ready));
  const { port } = server.address() as AddressInfo;
  // The render CLI only accepts the worker's loopback media proxy paths.
  const sourceUrl = `http://127.0.0.1:${port}/media/1`;

  await writeFile(
    propsPath,
    JSON.stringify(
      {
        sourceVideo: {
          // Remotion fetches media over HTTP; a bare filesystem path resolves
          // inside its bundle server and 404s, so serve the file on loopback.
          src: sourceUrl,
          width: 320,
          height: 180,
          duration_ms: 1000,
        },
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

  // Render asynchronously: a synchronous spawn would block this process's
  // event loop, so the loopback media server could never answer Chromium.
  let render: { status: number | null; output: string };
  try {
    render = await runRenderCli(propsPath, outputPath);
  } finally {
    await new Promise<void>((closed) => server.close(() => closed()));
  }
  if (render.status !== 0) {
    throw new Error(`Chromium smoke render failed: ${render.output}`);
  }

  const outputStats = await stat(outputPath);
  assert.ok(outputStats.size > 0, "Smoke render output should be non-empty");

  const metadata = JSON.parse(await readFile(`${outputPath}.json`, "utf8"));
  assert.equal(metadata.composition_id, "MotionlyPremium");
}

function runRenderCli(
  propsPath: string,
  outputPath: string,
): Promise<{ status: number | null; output: string }> {
  return new Promise((done) => {
    const child = spawn(
      "node",
      [
        "--import",
        "./ts-node-register.mjs",
        "src/render-cli.ts",
        "--props",
        propsPath,
        "--out",
        outputPath,
      ],
      { cwd: resolve("."), env: process.env },
    );
    let output = "";
    child.stdout.on("data", (chunk) => (output += chunk));
    child.stderr.on("data", (chunk) => (output += chunk));
    child.on("close", (status) => done({ status, output }));
  });
}

function createRangeServer(path: string): Server {
  return createServer(async (request, response) => {
    const file = await stat(path);
    const range = request.headers.range;
    response.setHeader("accept-ranges", "bytes");
    response.setHeader("content-type", "video/mp4");
    if (!range) {
      response.writeHead(200, { "content-length": file.size });
      createReadStream(path).pipe(response);
      return;
    }
    const [rawStart, rawEnd] = range.replace("bytes=", "").split("-");
    const start = Number(rawStart || 0);
    const end = Math.min(Number(rawEnd || file.size - 1), file.size - 1);
    response.writeHead(206, {
      "content-length": end - start + 1,
      "content-range": `bytes ${start}-${end}/${file.size}`,
    });
    createReadStream(path, { start, end }).pipe(response);
  });
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
