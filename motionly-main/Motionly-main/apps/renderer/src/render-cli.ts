import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { bundle } from "@remotion/bundler";
import { getCompositions, renderMedia } from "@remotion/renderer";
import type { MotionlyRenderProps } from "./types";

/** Path to the file that caches the last webpack bundle location. */
const BUNDLE_CACHE_FILE = resolve(".remotion-bundle");

type CliArgs = {
  propsPath: string;
  outputPath: string;
};

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const inputProps = JSON.parse(
    await readFile(args.propsPath, "utf8"),
  ) as MotionlyRenderProps;
  assertProductionProps(inputProps);

  const entryPoint = resolve("src/index.ts");
  const bundleLocation = await getOrCreateBundle(entryPoint);

  const compositions = await getCompositions(bundleLocation, { inputProps });
  const composition = compositions.find((item) => item.id === "MotionlyPremium");

  if (!composition) {
    throw new Error("MotionlyPremium composition was not found.");
  }

  await mkdir(dirname(args.outputPath), { recursive: true });

  const renderStarted = Date.now();
  await renderMedia({
    composition,
    serveUrl: bundleLocation,
    codec: "h264",
    outputLocation: args.outputPath,
    inputProps,
    // Use system Chrome when available in containerized environments.
    browserExecutable:
      process.env.BROWSER_EXECUTABLE_PATH ||
      process.env.PUPPETEER_EXECUTABLE_PATH ||
      undefined,
  });

  const renderTimeMs = Date.now() - renderStarted;

  // Actual video duration in milliseconds (frames / fps * 1000)
  const videoDurationMs = Math.round(
    (composition.durationInFrames / composition.fps) * 1000,
  );

  await writeFile(
    `${args.outputPath}.json`,
    JSON.stringify(
      {
        output_path: args.outputPath,
        render_time_ms: renderTimeMs,
        // Keep duration_ms for backward-compat but it now reflects RENDER time
        duration_ms: renderTimeMs,
        video_duration_ms: videoDurationMs,
        composition_id: composition.id,
        width: composition.width,
        height: composition.height,
        fps: composition.fps,
      },
      null,
      2,
    ),
  );
}

/**
 * Returns the Remotion webpack bundle location.
 * Uses a file-based cache so repeated renders in the same container
 * skip the ~30 second webpack step after the first render.
 */
async function getOrCreateBundle(entryPoint: string): Promise<string> {
  // Check for a pre-warmed bundle (created by prebundle.ts at container start)
  try {
    const cached = (await readFile(BUNDLE_CACHE_FILE, "utf8")).trim();
    if (cached && existsSync(cached)) {
      console.log(`[render-cli] Using cached bundle: ${cached}`);
      return cached;
    }
  } catch {
    // Cache file missing or unreadable — fall through to create a new bundle
  }

  console.log("[render-cli] Building Remotion bundle (first render in this container)...");
  const location = await bundle({ entryPoint });

  // Cache for subsequent renders in this container lifetime
  await writeFile(BUNDLE_CACHE_FILE, location).catch(() => {
    /* Non-critical — ignore write errors */
  });

  return location;
}

function parseArgs(args: string[]): CliArgs {
  const outputIndex = args.indexOf("--out");
  const propsIndex = args.indexOf("--props");
  if (propsIndex < 0 || !args[propsIndex + 1]) {
    throw new Error("--props is required for production rendering.");
  }
  if (outputIndex < 0 || !args[outputIndex + 1]) {
    throw new Error("--out is required for production rendering.");
  }
  return {
    outputPath: args[outputIndex + 1],
    propsPath: args[propsIndex + 1],
  };
}

function assertProductionProps(inputProps: MotionlyRenderProps) {
  if (
    !inputProps.sourceVideo?.src ||
    inputProps.sourceVideo.src.startsWith("fixture://")
  ) {
    throw new Error("A real sourceVideo.src URL is required for rendering.");
  }
  const fixtureAsset = inputProps.assets.find((asset) =>
    asset.src?.startsWith("fixture://"),
  );
  if (fixtureAsset) {
    throw new Error(`Fixture media is not allowed in render assets: ${fixtureAsset.id}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
