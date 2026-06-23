import assert from "node:assert/strict";
import { createReadStream } from "node:fs";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { bundle } from "@remotion/bundler";
import { getCompositions, renderMedia } from "@remotion/renderer";
import type { MotionlyRenderProps } from "./types.js";

const enabled = process.env.RUN_RENDERER_ENCODED_TESTS === "true";

test(
  "upload-to-export Remotion golden preserves selected frames and output timing",
  { skip: enabled ? false : "set RUN_RENDERER_ENCODED_TESTS=true" },
  async () => {
    const directory = await mkdtemp(join(tmpdir(), "motionly-remotion-parity-"));
    const source = join(directory, "source.mp4");
    const opening = join(directory, "opening.mp4");
    const payoff = join(directory, "payoff.mp4");
    createFixtureVideo(source);
    const server = createRangeServer(source);
    await new Promise<void>((resolveServer) => server.listen(0, "127.0.0.1", resolveServer));
    const address = server.address();
    assert(address && typeof address !== "string");
    const sourceUrl = `http://127.0.0.1:${address.port}/source.mp4`;

    try {
      const serveUrl = await bundle({ entryPoint: resolve("src/index.ts") });
      const variants = [
        renderProps(sourceUrl, 0, 1000, "opening"),
        renderProps(sourceUrl, 2500, 4000, "payoff"),
      ];
      for (const [index, inputProps] of variants.entries()) {
        const compositions = await getCompositions(serveUrl, { inputProps });
        const composition = compositions.find((item) => item.id === "MotionlyPremium");
        assert(composition);
        await renderMedia({
          composition,
          serveUrl,
          codec: "h264",
          outputLocation: index === 0 ? opening : payoff,
          inputProps,
          browserExecutable:
            process.env.BROWSER_EXECUTABLE_PATH ||
            process.env.PUPPETEER_EXECUTABLE_PATH ||
            undefined,
        });
      }

      assert.notDeepEqual(await readFile(opening), await readFile(payoff));
      const durations = [probeDuration(opening), probeDuration(payoff)];
      assert(0.9 <= durations[0] && durations[0] <= 1.1);
      assert(1.4 <= durations[1] && durations[1] <= 1.7);
      assert(durations[1] > durations[0]);
      const openingPixel = probePixel(opening);
      const payoffPixel = probePixel(payoff);
      assert(openingPixel[0] > openingPixel[2] + 150, `expected red opening: ${openingPixel}`);
      assert(payoffPixel[2] > payoffPixel[0] + 150, `expected blue payoff: ${payoffPixel}`);
    } finally {
      await new Promise<void>((resolveServer) => server.close(() => resolveServer()));
      await rm(directory, { recursive: true, force: true });
    }
  },
);

function renderProps(
  sourceUrl: string,
  sourceStartMs: number,
  sourceEndMs: number,
  reason: string,
): MotionlyRenderProps {
  return {
    sourceVideo: { src: sourceUrl, width: 160, height: 90, duration_ms: 4000 },
    timeline: {
      schema_version: 1,
      duration_ms: sourceEndMs - sourceStartMs,
      clips: [
        {
          id: "cut-0",
          source_start_ms: sourceStartMs,
          source_end_ms: sourceEndMs,
          output_start_ms: 0,
          output_end_ms: sourceEndMs - sourceStartMs,
          reason,
        },
      ],
    },
    editPlan: {
      schema_version: 1,
      style: "founder",
      target_duration_s: 10,
      cuts: [{ start_ms: sourceStartMs, end_ms: sourceEndMs, reason }],
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
      width: 160,
      height: 90,
      fps: 10,
      video_codec: "h264",
      audio_codec: "aac",
      pixel_format: "yuv420p",
      format: "mp4",
    },
  };
}

function createFixtureVideo(path: string) {
  const result = spawnSync(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-f",
      "lavfi",
      "-i",
      "color=c=red:size=160x90:rate=10:duration=2",
      "-f",
      "lavfi",
      "-i",
      "color=c=blue:size=160x90:rate=10:duration=2",
      "-f",
      "lavfi",
      "-i",
      "sine=frequency=440:sample_rate=44100:duration=4",
      "-filter_complex",
      "[0:v][1:v]concat=n=2:v=1:a=0[v]",
      "-map",
      "[v]",
      "-map",
      "2:a",
      "-shortest",
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-pix_fmt",
      "yuv420p",
      "-c:a",
      "aac",
      "-movflags",
      "+faststart",
      path,
    ],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
}

function probePixel(path: string): [number, number, number] {
  const result = spawnSync(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-ss",
      "0.2",
      "-i",
      path,
      "-frames:v",
      "1",
      "-vf",
      "scale=1:1",
      "-pix_fmt",
      "rgb24",
      "-f",
      "rawvideo",
      "pipe:1",
    ],
  );
  assert.equal(result.status, 0, String(result.stderr));
  const output = result.stdout as Buffer;
  return [output[0] ?? 0, output[1] ?? 0, output[2] ?? 0];
}

function probeDuration(path: string) {
  const result = spawnSync(
    "ffprobe",
    [
      "-v",
      "error",
      "-show_entries",
      "format=duration",
      "-of",
      "default=noprint_wrappers=1:nokey=1",
      path,
    ],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  return Number(Number(result.stdout.trim()).toFixed(1));
}

function createRangeServer(path: string) {
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
