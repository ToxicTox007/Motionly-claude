import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { bundle } from "@remotion/bundler";
import { getCompositions, renderStill } from "@remotion/renderer";
import type { MotionlyRenderProps } from "./types.js";

const enabled = process.env.RUN_RENDERER_GOLDEN_TESTS === "true";
const goldenPath = resolve("src/goldens/renderer-frame-signatures.json");

test(
  "renderer golden frames cover effects, captions, transitions, and framing",
  { skip: enabled ? false : "set RUN_RENDERER_GOLDEN_TESTS=true" },
  async () => {
    const directory = await mkdtemp(join(tmpdir(), "motionly-frame-goldens-"));
    const source = join(directory, "source.mp4");
    createFixture(source);
    const server = createRangeServer(source);
    await new Promise<void>((resolveServer) =>
      server.listen(0, "127.0.0.1", resolveServer),
    );
    const address = server.address();
    assert(address && typeof address !== "string");
    const sourceUrl = `http://127.0.0.1:${address.port}/source.mp4`;

    try {
      const serveUrl = await bundle({ entryPoint: resolve("src/index.ts") });
      const signatures: Record<string, string> = {};
      const browserExecutable =
        process.env.BROWSER_EXECUTABLE_PATH ||
        process.env.PUPPETEER_EXECUTABLE_PATH ||
        undefined;
      for (const [name, inputProps] of Object.entries(scenarios(sourceUrl))) {
        const compositions = await getCompositions(serveUrl, {
          inputProps,
          browserExecutable,
        });
        const composition = compositions.find(
          (item) => item.id === "MotionlyPremium",
        );
        assert(composition);
        const output = join(directory, `${name}.png`);
        await renderStill({
          composition,
          serveUrl,
          output,
          frame: 8,
          inputProps,
          browserExecutable,
        });
        signatures[name] = createHash("sha256")
          .update(await readFile(output))
          .digest("hex");
      }
      if (process.env.UPDATE_GOLDENS === "true") {
        await writeFile(goldenPath, `${JSON.stringify(signatures, null, 2)}\n`);
      }
      const expected = JSON.parse(await readFile(goldenPath, "utf8"));
      assert.deepEqual(signatures, expected);
      assert.equal(
        new Set(Object.values(signatures)).size,
        Object.keys(signatures).length,
      );
    } finally {
      await new Promise<void>((resolveServer) =>
        server.close(() => resolveServer()),
      );
      await rm(directory, { recursive: true, force: true });
    }
  },
);

function scenarios(sourceUrl: string): Record<string, MotionlyRenderProps> {
  const base = baseProps(sourceUrl);
  return {
    effects: {
      ...base,
      effectsConfig: {
        id: "effects",
        workspace_id: "workspace",
        project_id: "project",
        version: 1,
        schema_version: 1,
        status: "ready",
        preset: "champagne_gold",
        lut_intensity: 0.9,
        texture: {
          grain: 0.25,
          vignette: 0.25,
          light_leak: 0.2,
          anamorphic_flare: true,
        },
        motion_effects: ["flash"],
        motion: {
          ken_burns: {
            enabled: false,
            start_scale: 1,
            end_scale: 1.1,
            pan_x_pct: 0,
            pan_y_pct: 0,
          },
          speed_ramp: {
            enabled: false,
            start_rate: 1,
            peak_rate: 1.5,
            end_rate: 1,
          },
          beat_impact: { enabled: false, intensity: 0.5 },
          auto_reframe: {
            enabled: false,
            target_aspect: "9:16",
            subject_x_pct: 50,
            subject_y_pct: 50,
            smoothing: 0.65,
          },
          keyframes: [],
          clip_effects: {},
        },
        background_removal: {
          enabled: false,
          feather_px: 18,
          apply_to_broll: false,
        },
        gradient_ramp: {
          enabled: true,
          start_color: "#111827",
          end_color: "#f59e0b",
          angle_deg: 135,
          opacity: 0.35,
          blend_mode: "screen",
        },
        after_effects_presets: [],
        effect_count_limit_per_30s: 8,
        render_time_impact: "medium",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      } as unknown as MotionlyRenderProps["effectsConfig"],
    },
    captions: {
      ...base,
      captions: [
        {
          id: "caption",
          index: 0,
          start_ms: 0,
          end_ms: 2000,
          text: "GOLDEN FRAME",
          words: [],
          line_count: 1,
          highlight_words: ["GOLDEN"],
        },
      ],
      captionStyle: {
        mode: "word_pop",
        font_family: "Arial",
        font_size_px: 28,
        font_weight: 800,
        text_case: "uppercase",
        text_color: "#ffffff",
        accent_color: "#f59e0b",
        background_color: "#111111",
        background_opacity: 0.7,
        border_radius_px: 8,
        padding_x_px: 12,
        padding_y_px: 6,
        letter_spacing_px: 0,
        stroke_width_px: 1,
        stroke_color: "#000000",
        safe_area_bottom_pct: 12,
        placement: "lower",
        animation: "pop",
        max_lines: 2,
        contrast_guardrail: "required",
      },
    },
    transition: {
      ...base,
      editPlan: {
        ...base.editPlan,
        manual_elements: [
          {
            id: "transition",
            type: "transition",
            name: "Fade",
            zone: "timeline",
            start_ms: 0,
            end_ms: 1200,
            x_pct: 50,
            y_pct: 50,
            width_pct: 100,
            height_pct: 100,
            rotation_deg: 0,
            opacity: 1,
            properties: { transition: "fade_black" },
          },
        ],
      },
    },
    framing: {
      ...base,
      exportSettings: {
        ...base.exportSettings,
        framing: {
          output_aspect: "16:9",
          fit_mode: "custom",
          crop: { x: 0.15, y: 0.8, zoom: 1.8 },
          padding_color: "#ff00ff",
        },
      },
    },
  };
}

function baseProps(sourceUrl: string): MotionlyRenderProps {
  return {
    sourceVideo: { src: sourceUrl, width: 160, height: 90, duration_ms: 2000 },
    timeline: {
      schema_version: 1,
      duration_ms: 2000,
      clips: [
        {
          id: "clip",
          source_start_ms: 0,
          source_end_ms: 2000,
          output_start_ms: 0,
          output_end_ms: 2000,
        },
      ],
    },
    editPlan: {
      schema_version: 1,
      style: "founder",
      target_duration_s: 10,
      cuts: [{ start_ms: 0, end_ms: 2000, reason: "golden" }],
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

function createFixture(path: string) {
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
      "testsrc2=size=160x90:rate=10:duration=2",
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-pix_fmt",
      "yuv420p",
      path,
    ],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
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
